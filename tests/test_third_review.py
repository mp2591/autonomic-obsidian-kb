"""Regression tests for the storage and lifecycle findings in issue #8.

Editors are simulated with plain file operations, never with KB helpers such as
``atomic_write``, so the KB cannot mistake an editor's save for its own write.
"""

from __future__ import annotations

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from autonomic_kb import storage
from autonomic_kb.cli import main
from autonomic_kb.config import KBConfig
from autonomic_kb.evidence import OperationLedger
from autonomic_kb.healing import Healer, forget
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.lifecycle import Lifecycle
from autonomic_kb.markdown import parse_markdown
from autonomic_kb.mcp_server import MCPServer
from autonomic_kb.retrieval import Retriever
from autonomic_kb.storage import move_into, semantic_transaction
from autonomic_kb.util import atomic_write, remove_file
from autonomic_kb.validation import ValidationIssue, Validator
from tests.support import make_vault, write_memory


def _editor_atomic_save(path: Path, text: str) -> None:
    """Save the way most editors do: write a temporary file, then rename it over the note."""
    temporary = path.with_name(f".{path.name}.editor-save")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _every_file_text(root: Path) -> str:
    """Text of every file under ``root`` (binary files such as the SQLite index are skipped)."""
    contents = (path.read_bytes() for path in root.rglob("*") if path.is_file())
    return "\n".join(data.decode("utf-8", "ignore") for data in contents if b"\x00" not in data)


def _status(path: Path) -> str:
    return str(parse_markdown(path.read_text(encoding="utf-8")).metadata.get("status"))


class RollbackOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        self.vault = self.config.vault

    def test_human_edit_after_a_kb_rewrite_survives_a_later_failure(self):
        note = write_memory(
            self.config, "facts/note.md", "kb:global:fact:note", "Note", "Original fact", scope="global"
        )
        original = note.read_text()
        human = "Human line appended in place after the KB rewrite."

        def failing_ledger_write(*args, **kwargs):
            with note.open("a", encoding="utf-8") as handle:
                handle.write(human + "\n")
            raise OSError(errno.ENOSPC, "No space left on device")

        lifecycle = Lifecycle(self.config)
        try:
            with (
                patch.object(OperationLedger, "append", side_effect=failing_ledger_write),
                self.assertRaises(OSError) as raised,
            ):
                lifecycle.revalidate("kb:global:fact:note", reason="still holds")
        finally:
            lifecycle.close()
        self.assertIn(human, _every_file_text(self.vault))
        self.assertEqual(note.read_text(), original)  # the failed transaction left nothing behind
        conflicts = storage.rollback_conflicts(self.vault)
        self.assertEqual([item["path"] for item in conflicts[0]["conflicts"]], ["facts/note.md"])
        preserved = self.vault / conflicts[0]["conflicts"][0]["preserved"]
        self.assertIn(human, preserved.read_text())
        self.assertIn("facts/note.md", "\n".join(raised.exception.__notes__))
        KnowledgeIndex(self.config).index_vault()  # a preserved conflict does not block the vault

    def test_external_save_to_a_note_the_transaction_never_wrote_is_kept(self):
        unrelated = write_memory(self.config, "unrelated.md", "unrelated", "Unrelated", "kept by the editor")
        with self.assertRaises(RuntimeError), semantic_transaction(self.vault):
            atomic_write(self.vault / "created.md", "a note the KB created\n")
            _editor_atomic_save(unrelated, "human rewrite saved during the transaction\n")
            raise RuntimeError("injected failure")
        self.assertEqual(unrelated.read_text(), "human rewrite saved during the transaction\n")
        self.assertFalse((self.vault / "created.md").exists())
        self.assertEqual(storage.rollback_conflicts(self.vault), [])

    def test_note_created_by_another_program_during_a_failed_transaction_stays(self):
        with self.assertRaises(RuntimeError), semantic_transaction(self.vault):
            atomic_write(self.vault / "kb-created.md", "kb output\n")
            (self.vault / "human-created.md").write_text("a note a person created meanwhile\n")
            raise RuntimeError("injected failure")
        self.assertEqual((self.vault / "human-created.md").read_text(), "a note a person created meanwhile\n")
        self.assertFalse((self.vault / "kb-created.md").exists())
        self.assertTrue(list((self.vault / ".kb" / "rolled-back").rglob("kb-created.md")))

    def test_rollback_conflicts_are_listed_and_acknowledged_by_reconcile(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before")
        with self.assertRaises(RuntimeError), semantic_transaction(self.vault):
            atomic_write(note, "kb rewrite\n")
            _editor_atomic_save(note, "human save on top of the kb rewrite\n")
            raise RuntimeError("injected failure")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--json", "--vault", str(self.vault), "reconcile"]), 0)
        listed = json.loads(output.getvalue())["rollback_conflicts"]
        self.assertEqual(listed[0]["conflicts"][0]["path"], "note.md")
        with redirect_stdout(io.StringIO()):
            code = main(["--vault", str(self.vault), "reconcile", listed[0]["transaction"], "--acknowledge"])
        self.assertEqual(code, 0)
        self.assertEqual(storage.rollback_conflicts(self.vault), [])
        self.assertIn("human save on top", _every_file_text(self.vault / ".kb" / "rolled-back"))

    def test_save_made_after_the_snapshot_is_what_rollback_restores(self):
        note = write_memory(
            self.config, "facts/note.md", "kb:global:fact:note", "Note", "Original fact", scope="global"
        )
        saved = note.read_text() + "Editor line saved after the transaction began.\n"
        real_snapshot = storage._snapshot

        def snapshot_then_editor_saves(vault, snapshot):
            files = real_snapshot(vault, snapshot)
            _editor_atomic_save(note, saved)  # before the KB reads and rewrites the note
            return files

        lifecycle = Lifecycle(self.config)
        try:
            with (
                patch("autonomic_kb.storage._snapshot", side_effect=snapshot_then_editor_saves),
                patch.object(OperationLedger, "append", side_effect=OSError(errno.ENOSPC, "No space left")),
                self.assertRaises(OSError),
            ):
                lifecycle.revalidate("kb:global:fact:note", reason="still holds")
        finally:
            lifecycle.close()
        self.assertEqual(note.read_text(), saved)  # the version the KB replaced, byte for byte
        self.assertEqual(storage.rollback_conflicts(self.vault), [])

    def test_rollback_does_not_bring_back_a_note_deleted_before_the_kb_removed_it(self):
        for removal in ("remove_file", "move_into"):
            with self.subTest(removal=removal):
                note = write_memory(self.config, f"{removal}.md", removal, "Deleted", "deleted by a person")
                with self.assertRaises((RuntimeError, FileNotFoundError)), semantic_transaction(self.vault):
                    note.unlink()  # a person deletes it after the transaction began
                    if removal == "remove_file":
                        remove_file(note)
                    else:
                        move_into(note, self.vault / self.config.archive_dir, removal)
                    raise RuntimeError("injected failure")
                self.assertFalse(note.exists())

    def test_a_rollback_that_fails_part_way_keeps_what_it_preserved_and_the_journal(self):
        first = write_memory(self.config, "a.md", "a", "A", "before a")
        second = write_memory(self.config, "b.md", "b", "B", "before b")
        originals = (first.read_text(), second.read_text())
        real_link_exclusive = storage._link_exclusive

        def failing_for_second(source, destination):
            if Path(destination).name == "b.md":
                raise OSError(errno.EIO, "I/O error")
            return real_link_exclusive(source, destination)

        with (
            patch("autonomic_kb.storage._link_exclusive", side_effect=failing_for_second),
            self.assertRaises(RuntimeError) as raised,
            semantic_transaction(self.vault),
        ):
            atomic_write(first, "kb rewrite of a\n")
            atomic_write(second, "kb rewrite of b\n")
            with first.open("a", encoding="utf-8") as handle:
                handle.write("first human edit\n")
            raise RuntimeError("injected failure")
        notes = "\n".join(raised.exception.__notes__)
        self.assertIn("a.md ->", notes)
        self.assertIn("could not restore b.md", notes)
        self.assertEqual(first.read_text(), originals[0])
        self.assertEqual(storage.rollback_conflicts(self.vault)[0]["conflicts"][0]["path"], "a.md")
        with self.assertRaisesRegex(ValueError, "kb reconcile"):
            KnowledgeIndex(self.config).index_vault()
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        _editor_atomic_save(first, "second human save\n")
        result = storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual((first.read_text(), second.read_text()), originals)
        self.assertEqual(result["conflicts"][0]["path"], "a.md")
        kept = _every_file_text(self.vault / ".kb" / "rolled-back")
        self.assertIn("first human edit", kept)
        self.assertIn("second human save", kept)
        KnowledgeIndex(self.config).index_vault()

    def test_rollback_works_where_hard_links_are_unsupported(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before")
        other = write_memory(self.config, "other.md", "other", "Other", "untouched by the KB")
        original = note.read_text()
        with (
            patch("autonomic_kb.storage.os.link", side_effect=PermissionError("hard links unsupported")),
            self.assertRaises(RuntimeError),
            semantic_transaction(self.vault),
        ):
            atomic_write(note, "kb rewrite\n")
            _editor_atomic_save(other, "a person's save\n")
            raise RuntimeError("injected failure")
        self.assertEqual(note.read_text(), original)
        self.assertEqual(other.read_text(), "a person's save\n")
        self.assertTrue(list((self.vault / ".kb" / "rolled-back").rglob("note.md")))


class RaceSafeMoveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def _race(self, links: bool, edit: str, through_forget: bool) -> tuple[KBConfig, dict, str]:
        """Move a note while an editor saves it right after the link or exclusive copy."""
        config = make_vault(Path(self.temporary.name) / f"{links}-{edit}-{through_forget}")
        note = write_memory(
            config, "facts/note.md", "kb:global:fact:raced", "Raced", "Fact before the editor saved", scope="global"
        )
        existing = config.vault / config.archive_dir / "note.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("an archived note that must not be replaced\n")
        human = f"editor save racing the move ({links}, {edit})"
        held: list = []
        fired: list[bool] = []

        def editor() -> None:
            if fired:
                return
            fired.append(True)
            if edit == "atomic":
                _editor_atomic_save(note, f"{human}\n")
            elif edit == "in-place":  # opened by path after the capture, so this re-creates the note
                with note.open("a", encoding="utf-8") as handle:
                    handle.write(f"{human}\n")
            else:  # through a descriptor the editor opened before the move started
                held[0].write(f"{human}\n")
                held[0].flush()

        real_link, real_copy = os.link, storage._copy_exclusive

        def link(source, destination, *args, **kwargs):
            if not links:
                raise PermissionError("hard links unsupported")
            real_link(source, destination, *args, **kwargs)
            if Path(destination).parent == existing.parent:
                editor()

        def copy(source, destination):
            real_copy(source, destination)
            editor()

        try:
            with (
                patch("autonomic_kb.storage.os.link", side_effect=link),
                patch("autonomic_kb.storage._copy_exclusive", side_effect=copy),
            ):
                if through_forget:
                    result = forget(config, "kb:global:fact:raced")
                else:
                    held.append(note.open("a", encoding="utf-8"))
                    moved = move_into(note, existing.parent, "kb:global:fact:raced")
                    destination = moved.destination.relative_to(config.vault).as_posix()
                    result = {"action": "archived", "destination": destination, "conflicts": moved.conflicts}
        finally:
            for handle in held:
                handle.close()
        self.assertEqual(existing.read_text(), "an archived note that must not be replaced\n")
        return config, result, human

    def test_concurrent_saves_survive_forget_on_both_move_paths(self):
        for links in (True, False):
            for edit in ("atomic", "in-place"):
                with self.subTest(links=links, edit=edit):
                    config, result, human = self._race(links, edit, through_forget=True)
                    self.assertEqual(result["action"], "archived")
                    self.assertIn(human, _every_file_text(config.vault))
                    self.assertIn("Fact before the editor saved", (config.vault / result["destination"]).read_text())
                    self.assertEqual(result["conflicts"][0]["path"], "facts/note.md")

    def test_writes_through_an_open_descriptor_survive_a_move(self):
        for links in (True, False):
            with self.subTest(links=links):
                config, result, human = self._race(links, "open-descriptor", through_forget=False)
                self.assertIn(human, _every_file_text(config.vault))
                if links:  # the destination is the same file, so the write is carried along
                    self.assertIn(human, (config.vault / result["destination"]).read_text())
                    self.assertEqual(result["conflicts"], [])
                else:  # the copy no longer matches the source, so the source is kept and reported
                    self.assertTrue(result["conflicts"])

    def test_rollback_of_a_move_keeps_a_source_recreated_by_an_editor(self):
        config = make_vault(Path(self.temporary.name) / "rollback")
        note = write_memory(config, "note.md", "note", "Note", "before the move")
        original = note.read_text()
        with self.assertRaises(RuntimeError), semantic_transaction(config.vault):
            move_into(note, config.vault / config.archive_dir, "note")
            _editor_atomic_save(note, "editor re-created the note during the move\n")
            raise RuntimeError("injected failure")
        self.assertEqual(note.read_text(), original)
        self.assertEqual(list((config.vault / config.archive_dir).glob("*.md")), [])
        self.assertIn("editor re-created the note", _every_file_text(config.vault / ".kb" / "rolled-back"))
        self.assertEqual(storage.rollback_conflicts(config.vault)[0]["conflicts"][0]["path"], "note.md")

    def _forget_with_failing_copies(self, failures: int) -> tuple[KBConfig, Path, str, BaseException]:
        """Forget where hard links are unsupported and the first ``failures`` copies fill the disk."""
        config = make_vault(Path(self.temporary.name) / f"copy-fails-{failures}")
        note = write_memory(config, "facts/note.md", "kb:global:fact:note", "Note", "Some fact", scope="global")
        original = note.read_text()
        real_copy = shutil.copyfileobj
        calls: list[str] = []

        def partial_copy(reader, writer, *args, **kwargs):
            calls.append(writer.name)
            if len(calls) <= failures:
                writer.write(reader.read(40))  # some bytes land, then the disk is full
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_copy(reader, writer, *args, **kwargs)

        with (
            patch("autonomic_kb.storage.os.link", side_effect=PermissionError("hard links unsupported")),
            patch("autonomic_kb.storage.shutil.copyfileobj", side_effect=partial_copy),
            self.assertRaises(OSError) as raised,
        ):
            forget(config, "kb:global:fact:note")
        return config, note, original, raised.exception

    def test_a_copy_that_fails_part_way_leaves_no_truncated_note(self):
        config, note, original, _ = self._forget_with_failing_copies(1)
        self.assertEqual(list((config.vault / config.archive_dir).glob("*.md")), [])
        self.assertEqual(note.read_text(), original)
        self.assertEqual(list(config.vault.rglob("*.kb-move-*")), [])
        KnowledgeIndex(config).index_vault()  # not blocked

    def test_when_every_copy_fails_the_captured_note_is_reported_and_reconcile_restores_it(self):
        config, note, original, error = self._forget_with_failing_copies(1000)
        self.assertIn("the captured version", "\n".join(error.__notes__))
        self.assertEqual(list((config.vault / config.archive_dir).glob("*.md")), [])
        journal = storage.pending_transactions(config.vault)[0]["journal"]
        storage.resolve_transaction(config.vault, journal, "restore-snapshot")
        self.assertEqual(note.read_text(), original)


class CrashReconcileOwnershipTests(unittest.TestCase):
    """After a real crash, `kb reconcile --restore-snapshot` restores only what the KB wrote."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        self.vault = self.config.vault
        self.kb_note = write_memory(self.config, "kb-note.md", "kb-note", "KB note", "before the crash")
        self.other = write_memory(self.config, "other.md", "other", "Other", "a note the KB never touches")
        self.original = self.kb_note.read_text()

    def _crash(self, script: str) -> None:
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        command = [sys.executable, "-c", script, str(self.vault), str(self.kb_note), str(self.config.repo)]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)

    def _crash_after_kb_write(self) -> str:
        self._crash(
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "from autonomic_kb.util import atomic_write\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    atomic_write(Path(sys.argv[2]), 'half-finished kb write\\n')\n"
            "    os._exit(1)\n"
        )
        return storage.pending_transactions(self.vault)[0]["journal"]

    def test_a_crash_before_any_kb_write_restores_nothing(self):
        self._crash(
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    os._exit(1)  # killed while planning, before the first write\n"
        )
        _editor_atomic_save(self.other, "edited by a person after the crash\n")
        (self.vault / "after-crash.md").write_text("created by a person after the crash\n")
        pending = storage.pending_transactions(self.vault)[0]
        self.assertEqual(pending["kb_written"], [])
        self.assertEqual(pending["external"], ["after-crash.md", "other.md"])
        result = storage.resolve_transaction(self.vault, pending["journal"], "restore-snapshot", delete_new=True)
        self.assertEqual(result["restored"], [])
        self.assertEqual(self.other.read_text(), "edited by a person after the crash\n")
        self.assertTrue((self.vault / "after-crash.md").exists())

    def test_a_crash_right_after_a_move_lands_is_attributed_to_the_kb(self):
        self._crash(
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.config import KBConfig\n"
            "from autonomic_kb.healing import forget\n"
            "real = os.link\n"
            "def crash_after(source, target, *args, **kwargs):\n"
            "    real(source, target, *args, **kwargs)\n"
            "    if '99-archive' in str(target):\n"
            "        os._exit(1)\n"
            "os.link = crash_after\n"
            "forget(KBConfig.load(Path(sys.argv[1]), Path(sys.argv[3])), 'kb-note')\n"
        )
        pending = storage.pending_transactions(self.vault)[0]
        self.assertIn("99-archive/kb-note.md", pending["kb_written"])
        self.assertNotIn("99-archive/kb-note.md", pending["external"])
        storage.resolve_transaction(self.vault, pending["journal"], "restore-snapshot")
        self.assertEqual(self.kb_note.read_text(), self.original)
        self.assertEqual(list((self.vault / "99-archive").glob("*.md")), [])

    def test_restore_snapshot_keeps_changes_to_paths_the_kb_never_wrote(self):
        journal = self._crash_after_kb_write()
        _editor_atomic_save(self.other, "edited by a person after the crash\n")
        (self.vault / "after-crash.md").write_text("created by a person after the crash\n")
        pending = storage.pending_transactions(self.vault)[0]
        self.assertEqual(pending["kb_written"], ["kb-note.md"])
        self.assertEqual(pending["external"], ["after-crash.md", "other.md"])
        result = storage.resolve_transaction(self.vault, journal, "restore-snapshot", delete_new=True)
        self.assertEqual(result["restored"], ["kb-note.md"])
        self.assertEqual(result["kept_external"], ["after-crash.md", "other.md"])
        self.assertEqual(self.kb_note.read_text(), self.original)
        self.assertEqual(self.other.read_text(), "edited by a person after the crash\n")
        self.assertTrue((self.vault / "after-crash.md").exists())

    def test_a_write_interrupted_right_after_it_lands_is_still_attributed_to_the_kb(self):
        script = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb import util\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "real = os.replace\n"
            "def crash_after(source, target):\n"
            "    real(source, target)\n"
            "    os._exit(1)\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    util.os.replace = crash_after\n"
            "    util.atomic_write(Path(sys.argv[2]), 'half-finished kb write\\n')\n"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        command = [sys.executable, "-c", script, str(self.vault), str(self.kb_note)]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)
        self.assertEqual(self.kb_note.read_text(), "half-finished kb write\n")
        pending = storage.pending_transactions(self.vault)[0]
        self.assertEqual(pending["kb_written"], ["kb-note.md"])
        self.assertEqual(pending["external"], [])
        storage.resolve_transaction(self.vault, pending["journal"], "restore-snapshot")
        self.assertEqual(self.kb_note.read_text(), self.original)

    def test_a_failed_write_is_not_reported_as_a_conflict(self):
        failing = patch("autonomic_kb.util.os.replace", side_effect=OSError(errno.EIO, "I/O error"))
        with self.assertRaises(OSError) as raised, semantic_transaction(self.vault):
            failing.start()  # only the KB write inside the transaction fails
            try:
                atomic_write(self.kb_note, "never lands\n")
            finally:
                failing.stop()
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual(self.kb_note.read_text(), self.original)
        self.assertEqual(storage.rollback_conflicts(self.vault), [])
        self.assertFalse(getattr(raised.exception, "__notes__", None))

    def test_restore_snapshot_preserves_a_save_made_on_top_of_the_kb_write(self):
        journal = self._crash_after_kb_write()
        _editor_atomic_save(self.kb_note, "a person saved over the half-finished write\n")
        result = storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual(self.kb_note.read_text(), self.original)
        preserved = self.vault / result["conflicts"][0]["preserved"]
        self.assertEqual(preserved.read_text(), "a person saved over the half-finished write\n")
        self.assertEqual(storage.rollback_conflicts(self.vault)[0]["conflicts"][0]["path"], "kb-note.md")
        KnowledgeIndex(self.config).index_vault()


class HealLifecycleTests(unittest.TestCase):
    """Heal must never move a note past review or back into retrieval.

    The vault retrieves low-scoring notes (minimum_score 0.05), and a stale control note
    proves that a note heal had wrongly turned ``stale`` would show up in both the MCP
    catalog and retrieval, so those assertions can fail.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        config = make_vault(Path(self.temporary.name))
        path = config.vault / "kb.toml"
        switch = "allow_cross_repo=false\nallow_privileged_remember=false"
        text = path.read_text().replace("allow_cross_repo=false", switch)
        path.write_text(text.replace("minimum_score=0.20", "minimum_score=0.05"))
        self.config = KBConfig.load(config.vault, config.repo)
        write_memory(
            self.config,
            "facts/control.md",
            "kb:global:fact:stale-control",
            "Stale control",
            "Stale control note about the nightly deploy job and the release checklist.",
            scope="global",
            status="stale",
            confidence=0.56,
            freshness="stale",
        )

    def _heal(self) -> dict:
        healer = Healer(self.config)
        try:
            return healer.heal(True)
        finally:
            healer.close()

    def _catalog(self) -> set[str]:
        return {item["id"] for item in MCPServer(self.config).read_resource("kb://memories")["memories"]}

    def _retrieved(self, task: str) -> set[str]:
        with KnowledgeIndex(self.config) as index:
            manifest = Retriever(self.config, index).retrieve(task, budget=400, record=False)
        return {item.id for item in manifest.items}

    def _assert_visible_when_stale(self, task: str) -> None:
        self.assertIn("kb:global:fact:stale-control", self._catalog())
        self.assertIn("kb:global:fact:stale-control", self._retrieved(task))

    def test_heal_keeps_an_unreviewed_candidate_out_of_the_catalog_and_retrieval(self):
        candidate = {
            "title": "Nightly deploy job",
            "summary": "The nightly deploy job rebuilds the documentation site.",
            "scope": "global",
            "applies_to": ["nonexistent.py"],
        }
        result = MCPServer(self.config).call_tool("kb_remember", candidate)
        self.assertEqual(result["status"], "inbox")
        self.assertNotIn(result["memory_id"], self._catalog())
        first = self._heal()
        planned = {action["action"] for action in first["actions"] if action["path"] == result["path"]}
        self.assertIn("mark-stale", planned)
        self._heal()
        note = self.config.vault / result["path"]
        self.assertEqual(_status(note), "inbox")
        task = "nightly deploy job documentation site"
        self._assert_visible_when_stale(task)
        self.assertNotIn(result["memory_id"], self._catalog())
        self.assertNotIn(result["memory_id"], self._retrieved(task))

    def test_heal_does_not_revive_retired_notes(self):
        paths = {}
        for status in ("superseded", "archived", "retracted"):
            paths[status] = write_memory(
                self.config,
                f"retired/{status}.md",
                f"kb:global:fact:{status}",
                f"Retired {status}",
                f"Retired {status} note about the release checklist.",
                scope="global",
                status=status,
                invalidation={"paths": ["nonexistent.py"]},
            )
        for _ in range(2):
            self._heal()
        for status, path in paths.items():
            self.assertEqual(_status(path), status)
        task = "release checklist retired note"
        self._assert_visible_when_stale(task)
        retired = {f"kb:global:fact:{status}" for status in paths}
        self.assertEqual(self._catalog() & retired, set())
        self.assertEqual(self._retrieved(task) & retired, set())

    def test_repeated_heal_marks_a_stale_note_once(self):
        path = write_memory(
            self.config,
            "facts/once.md",
            "kb:global:fact:once",
            "Once",
            "A note whose source path disappeared.",
            scope="global",
            invalidation={"paths": ["nonexistent.py"]},
        )
        for _ in range(3):
            self._heal()
        metadata = parse_markdown(path.read_text()).metadata
        self.assertEqual((metadata["status"], metadata["confidence"]), ("stale", 0.72))
        operations = [item.operation for item in OperationLedger(self.config).iter_operations("kb:global:fact:once")]
        self.assertEqual(operations, ["AMEND"])

    def test_heal_reports_a_conflict_its_rollback_preserved(self):
        note = write_memory(
            self.config,
            "facts/active.md",
            "kb:global:fact:active",
            "Active",
            "Active note.",
            scope="global",
            invalidation={"paths": ["nonexistent.py"]},
        )
        real_append, real_validate = OperationLedger.append, Validator.validate
        calls: list[int] = []

        def append_then_human_edits(ledger, *args, **kwargs):
            with note.open("a", encoding="utf-8") as handle:  # Obsidian appends to the note heal rewrote
                handle.write("Human edit during heal\n")
            return real_append(ledger, *args, **kwargs)

        def validate_with_regression(validator):
            report = real_validate(validator)
            calls.append(1)
            if len(calls) == 2:  # post-validation finds a new error, so heal rolls back
                report.issues.append(ValidationIssue("error", "injected", "facts/active.md", "new error", "", False))
            return report

        with (
            patch.object(OperationLedger, "append", append_then_human_edits),
            patch.object(Validator, "validate", validate_with_regression),
        ):
            result = self._heal()
        self.assertTrue(result["rolled_back"])
        conflict = result["rollback"]["conflicts"][0]
        self.assertEqual(conflict["path"], "facts/active.md")
        self.assertIn("Human edit during heal", (self.config.vault / conflict["preserved"]).read_text())

    def test_heal_still_marks_an_active_note_stale(self):
        path = write_memory(
            self.config,
            "facts/active.md",
            "kb:global:fact:active",
            "Active",
            "Active note about the build cache.",
            scope="global",
            invalidation={"paths": ["nonexistent.py"]},
        )
        self._heal()
        self._heal()
        self.assertEqual(_status(path), "stale")


class ReviewerInterleavingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        for name, status, port in (("old", "active", 8000), ("new1", "inbox", 8080), ("new2", "inbox", 9090)):
            folder = "facts" if status == "active" else self.config.inbox_dir
            write_memory(
                self.config,
                f"{folder}/{name}.md",
                f"kb:global:fact:{name}",
                f"Port {port}",
                f"The service listens on port {port}.",
                scope="global",
                status=status,
                claim_key="service.port",
                claim_value=port,
            )
        write_memory(self.config, "facts/other.md", "kb:global:fact:other", "Other", "Unrelated fact.", scope="global")

    def _interleave(self, first, second) -> None:
        """Run ``second`` to completion when ``first`` is about to take its transaction lock."""
        real = storage.semantic_transaction
        fired: list[bool] = []

        @contextmanager
        def hooked(vault):
            if not fired:
                fired.append(True)
                second()
            with real(vault):
                yield

        with patch("autonomic_kb.lifecycle.semantic_transaction", hooked):
            first()

    def _note(self, identity: str) -> dict:
        with KnowledgeIndex(self.config) as index:
            index.index_vault()
            note = index.get(identity)
        return parse_markdown((self.config.vault / note["path"]).read_text()).metadata

    def _operations(self, identity: str) -> list[str]:
        return [item.operation for item in OperationLedger(self.config).iter_operations(identity)]

    def test_competing_supersede_fails_instead_of_activating_two_successors(self):
        first, second = Lifecycle(self.config), Lifecycle(self.config)
        try:
            with self.assertRaisesRegex(ValueError, "already superseded"):
                self._interleave(
                    lambda: first.supersede("kb:global:fact:old", "kb:global:fact:new1", reason="reviewer A"),
                    lambda: second.supersede("kb:global:fact:old", "kb:global:fact:new2", reason="reviewer B"),
                )
        finally:
            first.close()
            second.close()
        old = self._note("kb:global:fact:old")
        self.assertEqual((old["status"], old["superseded_by"]), ("superseded", "kb:global:fact:new2"))
        self.assertEqual(self._note("kb:global:fact:new2")["status"], "active")
        self.assertEqual(self._note("kb:global:fact:new2")["supersedes"], ["kb:global:fact:old"])
        self.assertEqual(self._note("kb:global:fact:new1")["status"], "inbox")
        self.assertNotIn("supersedes", self._note("kb:global:fact:new1"))
        self.assertEqual(self._operations("kb:global:fact:old").count("SUPERSEDE"), 1)
        self.assertEqual(self._operations("kb:global:fact:new1"), [])

    def test_competing_merge_fails_when_a_source_was_superseded_meanwhile(self):
        first, second = Lifecycle(self.config), Lifecycle(self.config)
        try:
            with self.assertRaisesRegex(ValueError, "already superseded"):
                self._interleave(
                    lambda: first.merge(
                        "kb:global:fact:new1", ["kb:global:fact:old", "kb:global:fact:other"], reason="reviewer A"
                    ),
                    lambda: second.supersede("kb:global:fact:old", "kb:global:fact:new2", reason="reviewer B"),
                )
        finally:
            first.close()
            second.close()
        self.assertEqual(self._note("kb:global:fact:other")["status"], "active")
        self.assertEqual(self._note("kb:global:fact:new1")["status"], "inbox")
        self.assertEqual(self._operations("kb:global:fact:other"), [])
        self.assertEqual(self._operations("kb:global:fact:old"), ["SUPERSEDE"])

    def test_competing_split_fails_when_its_source_was_superseded_meanwhile(self):
        first, second = Lifecycle(self.config), Lifecycle(self.config)
        try:
            with self.assertRaisesRegex(ValueError, "already superseded"):
                self._interleave(
                    lambda: first.split(
                        "kb:global:fact:old", ["kb:global:fact:new1", "kb:global:fact:other"], reason="reviewer A"
                    ),
                    lambda: second.supersede("kb:global:fact:old", "kb:global:fact:new2", reason="reviewer B"),
                )
        finally:
            first.close()
            second.close()
        self.assertEqual(self._note("kb:global:fact:new1")["status"], "inbox")
        self.assertNotIn("split_from", self._note("kb:global:fact:other"))
        self.assertEqual(self._operations("kb:global:fact:old"), ["SUPERSEDE"])

    def test_competing_revalidate_fails_when_the_note_was_superseded_meanwhile(self):
        first, second = Lifecycle(self.config), Lifecycle(self.config)
        try:
            with self.assertRaisesRegex(ValueError, "superseded and cannot be revalidated"):
                self._interleave(
                    lambda: first.revalidate("kb:global:fact:old", reason="reviewer A"),
                    lambda: second.supersede("kb:global:fact:old", "kb:global:fact:new2", reason="reviewer B"),
                )
        finally:
            first.close()
            second.close()
        self.assertEqual(self._operations("kb:global:fact:old"), ["SUPERSEDE"])

    def test_a_status_changed_on_disk_after_indexing_is_refused(self):
        lifecycle = Lifecycle(self.config)
        real_note = lifecycle._note

        def indexed_then_edited(identity):
            note = real_note(identity)  # the index says inbox; a person then activates it by hand
            path = self.config.vault / note["path"]
            path.write_text(path.read_text().replace('status: "inbox"', 'status: "active"'))
            return note

        try:
            with (
                patch.object(lifecycle, "_note", side_effect=indexed_then_edited),
                self.assertRaisesRegex(ValueError, "changed on disk from inbox to active"),
            ):
                lifecycle.promote("kb:global:fact:new1", reason="reviewer")
        finally:
            lifecycle.close()
        self.assertEqual(self._operations("kb:global:fact:new1"), [])

    def test_promote_rechecks_status_under_the_lock(self):
        first, second = Lifecycle(self.config), Lifecycle(self.config)
        try:
            with self.assertRaisesRegex(ValueError, "only inbox memories can be promoted"):
                self._interleave(
                    lambda: first.promote("kb:global:fact:new1", reason="reviewer A"),
                    lambda: second.promote("kb:global:fact:new1", reason="reviewer B"),
                )
        finally:
            first.close()
            second.close()
        self.assertEqual(self._operations("kb:global:fact:new1"), ["AMEND"])


if __name__ == "__main__":
    unittest.main()
