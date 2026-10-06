"""Regression tests for the storage and recovery defects in issue #10 (follow-up to PR #9).

Editors are simulated with plain file operations at the names a person can see, never
with KB helpers. Each defect has its own test method (not a subTest), so both unittest
and pytest fail on the code the issue reviewed.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb import storage
from autonomic_kb.healing import forget
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.storage import move_into, semantic_transaction
from autonomic_kb.util import atomic_write
from tests.support import make_vault, write_memory

MARKER = "unique human marker saved by an editor"


def _editor_atomic_save(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name[:40]}.editor-save")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _editor_append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _files_containing(root: Path, text: str) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if b"\x00" not in data and text.encode() in data:
                found.append(path)
    return found


class _Vault(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        self.vault = self.config.vault

    def assert_human_bytes_retained(self, text: str = MARKER) -> None:
        """The editor's bytes survive in a live note or in a file recovery tooling reports."""
        reported = {item["preserved"] for row in storage.rollback_conflicts(self.vault) for item in row["conflicts"]}
        holders = _files_containing(self.vault, text)
        self.assertTrue(holders, "the editor's bytes survive nowhere")
        relative = [path.relative_to(self.vault) for path in holders]
        live = [path for path in relative if not any(part.startswith(".") for part in path.parts)]
        self.assertTrue(live or {path.as_posix() for path in relative} & reported, (relative, reported))


class FailedCopyCleanupTests(_Vault):
    """Finding 1: cleanup after a failed exclusive copy must not delete an editor's save."""

    def _forget_with_copy_failure(self, fault: str, edit: str) -> None:
        write_memory(self.config, "facts/note.md", "kb:global:fact:note", "Note", "Some fact", scope="global")
        archive = self.vault / self.config.archive_dir
        archive.mkdir(parents=True, exist_ok=True)
        visible = archive / "note.md"
        fired: list[bool] = []

        def editor() -> None:
            if not fired:
                fired.append(True)
                if edit == "atomic":
                    _editor_atomic_save(visible, MARKER + "\n")
                else:
                    _editor_append(visible, MARKER + "\n")

        real_copy, real_fsync, real_copystat = shutil.copyfileobj, os.fsync, shutil.copystat

        def copy(reader, writer, *args, **kwargs):
            if fault == "partial" and Path(writer.name).parent == archive and not fired:
                writer.write(reader.read(40))
                editor()
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_copy(reader, writer, *args, **kwargs)

        def fsync(descriptor):
            target = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            if fault == "fsync" and target.parent == archive and not fired:
                editor()
                raise OSError(errno.EIO, "I/O error")
            return real_fsync(descriptor)

        def copystat(source, destination, *args, **kwargs):
            if fault == "copystat" and Path(destination).parent == archive and not fired:
                editor()
                raise OSError(errno.EIO, "I/O error")
            return real_copystat(source, destination, *args, **kwargs)

        with (
            patch("autonomic_kb.storage.os.link", side_effect=OSError(errno.EXDEV, "cross-device link")),
            patch("autonomic_kb.storage.shutil.copyfileobj", side_effect=copy),
            patch("autonomic_kb.storage.os.fsync", side_effect=fsync),
            patch("autonomic_kb.storage.shutil.copystat", side_effect=copystat),
            self.assertRaises(OSError),
        ):
            forget(self.config, "kb:global:fact:note")
        self.assertTrue(fired, "the fault was never injected")
        self.assert_human_bytes_retained()

    def test_partial_copy_with_atomic_save(self):
        self._forget_with_copy_failure("partial", "atomic")

    def test_partial_copy_with_in_place_save(self):
        self._forget_with_copy_failure("partial", "in-place")

    def test_fsync_failure_with_atomic_save(self):
        self._forget_with_copy_failure("fsync", "atomic")

    def test_fsync_failure_with_in_place_save(self):
        self._forget_with_copy_failure("fsync", "in-place")

    def test_copystat_failure_with_atomic_save(self):
        self._forget_with_copy_failure("copystat", "atomic")

    def test_copystat_failure_with_in_place_save(self):
        self._forget_with_copy_failure("copystat", "in-place")

    def _rollback_with_failed_restore_copy(self, edit: str) -> None:
        note = write_memory(self.config, "note.md", "note", "Note", "before")
        fired: list[bool] = []
        real_copy = shutil.copyfileobj

        def copy(reader, writer, *args, **kwargs):
            if Path(writer.name).parent == self.vault and not fired:  # restoring note.md by copy
                fired.append(True)
                writer.write(reader.read(10))
                (_editor_atomic_save if edit == "atomic" else _editor_append)(note, MARKER + "\n")
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_copy(reader, writer, *args, **kwargs)

        with (
            patch("autonomic_kb.storage.os.link", side_effect=OSError(errno.EXDEV, "cross-device link")),
            patch("autonomic_kb.storage.shutil.copyfileobj", side_effect=copy),
            self.assertRaises(RuntimeError),
            semantic_transaction(self.vault),
        ):
            atomic_write(note, "kb rewrite\n")
            raise RuntimeError("injected failure")
        self.assertTrue(fired, "the restore copy never ran")
        self.assert_human_bytes_retained()
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assert_human_bytes_retained()

    def test_failed_restore_copy_with_atomic_save(self):
        self._rollback_with_failed_restore_copy("atomic")

    def test_failed_restore_copy_with_in_place_save(self):
        self._rollback_with_failed_restore_copy("in-place")

    def test_a_filesystem_without_links_or_no_replace_renames_is_refused_cleanly(self):
        note = write_memory(self.config, "facts/note.md", "kb:global:fact:note", "Note", "Some fact", scope="global")
        original = note.read_text()
        with (
            patch("autonomic_kb.storage.os.link", side_effect=PermissionError(errno.EPERM, "links unsupported")),
            patch("autonomic_kb.storage._rename_noreplace", return_value=False),
            self.assertRaisesRegex(OSError, "nothing was changed"),
        ):
            forget(self.config, "kb:global:fact:note")
        self.assertEqual(note.read_text(), original)
        self.assertEqual(list((self.vault / self.config.archive_dir).glob("*")), [])
        self.assertEqual(storage.pending_transactions(self.vault), [])
        leftovers = [path for kind in ("probe", "move", "copy") for path in self.vault.rglob(f".kb-{kind}-*")]
        self.assertEqual(leftovers, [])


class UnavailableBeforeStateTests(_Vault):
    """Finding 2: reconcile must not report success when a before-state is unavailable."""

    def test_missing_snapshot_keeps_the_journal_and_the_live_note(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before the crash")
        original = note.read_text()
        script = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "from autonomic_kb.util import atomic_write\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    atomic_write(Path(sys.argv[2]), 'half-finished kb write\\n')\n"
            "    os._exit(1)\n"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        command = [sys.executable, "-c", script, str(self.vault), str(note)]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        saved = self.vault / ".kb-transactions" / journal / "note.md"
        backup = Path(self.temporary.name) / "snapshot-note.md"
        shutil.move(saved, backup)  # the before-state is unavailable (say, an unmounted volume)
        with self.assertRaisesRegex(ValueError, "note.md"):
            storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual(note.read_text(), "half-finished kb write\n")  # not displaced
        self.assertEqual(storage.pending_transactions(self.vault)[0]["journal"], journal)
        with self.assertRaisesRegex(ValueError, "kb reconcile"):
            KnowledgeIndex(self.config).index_vault()
        shutil.move(backup, saved)  # the volume is back
        storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual(note.read_text(), original)
        self.assertEqual(storage.pending_transactions(self.vault), [])


class DurableInventoryTests(_Vault):
    """Finding 3: preserved versions stay discoverable when bookkeeping fails or recovery dies."""

    def test_bookkeeping_failure_after_a_capture_keeps_the_human_version_listed(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before")
        original = note.read_text()
        full: list[bool] = []
        real_capture = storage._capture
        real_atomic_write = storage.atomic_write
        real_append = getattr(storage, "_append_durably", None)
        holding = self.vault / ".kb" / "rolled-back"

        def capture(*args, **kwargs):
            result = real_capture(*args, **kwargs)
            full.append(True)  # the disk fills right after the first capture
            return result

        def atomic_write_unless_full(path, *args, **kwargs):
            if full and holding in Path(path).parents:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_atomic_write(path, *args, **kwargs)

        def append_unless_full(path, *args, **kwargs):
            if full and holding in Path(path).parents:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_append(path, *args, **kwargs)

        patches = [
            patch("autonomic_kb.storage._capture", side_effect=capture),
            patch("autonomic_kb.storage.atomic_write", side_effect=atomic_write_unless_full),
        ]
        if real_append is not None:
            patches.append(patch("autonomic_kb.storage._append_durably", side_effect=append_unless_full))
        for active in patches:
            active.start()
        try:
            with self.assertRaises(RuntimeError), semantic_transaction(self.vault):
                atomic_write(note, "kb rewrite\n")
                _editor_append(note, MARKER + "\n")
                raise RuntimeError("injected failure")
        finally:
            for active in patches:
                active.stop()
        for pending in storage.pending_transactions(self.vault):
            storage.resolve_transaction(self.vault, pending["journal"], "restore-snapshot")
        self.assertEqual(note.read_text(), original)
        self.assert_human_bytes_retained()

    def test_reconcile_killed_right_after_a_capture_still_lists_the_human_version(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before the crash")
        original = note.read_text()
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        crash = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "from autonomic_kb.util import atomic_write\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    atomic_write(Path(sys.argv[2]), 'half-finished kb write\\n')\n"
            "    os._exit(1)\n"
        )
        command = [sys.executable, "-c", crash, str(self.vault), str(note)]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)
        _editor_atomic_save(note, MARKER + "\n")
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        killed = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb import storage\n"
            "real = storage._capture\n"
            "def capture(*args, **kwargs):\n"
            "    real(*args, **kwargs)\n"
            "    os._exit(1)\n"
            "storage._capture = capture\n"
            "storage.resolve_transaction(Path(sys.argv[1]), sys.argv[2], 'restore-snapshot')\n"
        )
        command = [sys.executable, "-c", killed, str(self.vault), journal]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)
        storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual(note.read_text(), original)
        self.assert_human_bytes_retained()

    def test_a_failed_intent_record_leaves_the_note_untouched_and_retry_lists_the_edit(self):
        note = write_memory(self.config, "note.md", "note", "Note", "before")
        original = note.read_text()
        holding = self.vault / ".kb" / "rolled-back"
        real_append = storage._append_durably

        def full_disk_for_recovery(path, *args, **kwargs):
            if holding in Path(path).parents:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_append(path, *args, **kwargs)

        with (
            patch("autonomic_kb.storage._append_durably", side_effect=full_disk_for_recovery),
            self.assertRaises(RuntimeError) as raised,
            semantic_transaction(self.vault),
        ):
            atomic_write(note, "kb rewrite\n")
            _editor_append(note, MARKER + "\n")
            raise RuntimeError("injected failure")
        self.assertIn("could not restore note.md", "\n".join(raised.exception.__notes__))
        self.assertIn(MARKER, note.read_text())  # nothing was moved without a record
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        self.assertEqual(note.read_text(), original)
        self.assert_human_bytes_retained()

    def test_retries_keep_conflict_history_and_acknowledgements_per_version(self):
        first = write_memory(self.config, "a.md", "a", "A", "before a")
        second = write_memory(self.config, "b.md", "b", "B", "before b")
        script = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from autonomic_kb.storage import semantic_transaction\n"
            "from autonomic_kb.util import atomic_write\n"
            "with semantic_transaction(Path(sys.argv[1])):\n"
            "    atomic_write(Path(sys.argv[2]), 'kb a\\n')\n"
            "    atomic_write(Path(sys.argv[3]), 'kb b\\n')\n"
            "    os._exit(1)\n"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        command = [sys.executable, "-c", script, str(self.vault), str(first), str(second)]
        self.assertEqual(subprocess.run(command, env=environment, check=False).returncode, 1)
        journal = storage.pending_transactions(self.vault)[0]["journal"]
        saved = self.vault / ".kb-transactions" / journal / "b.md"
        away = Path(self.temporary.name) / "b.md"
        shutil.move(saved, away)
        _editor_atomic_save(first, "first human save\n")
        with self.assertRaisesRegex(ValueError, "b.md"):
            storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        listed = storage.rollback_conflicts(self.vault)
        self.assertEqual([item["path"] for row in listed for item in row["conflicts"]], ["a.md"])
        storage.acknowledge_rollback(self.vault, listed[0]["transaction"])
        _editor_atomic_save(first, "second human save\n")
        shutil.move(away, saved)
        storage.resolve_transaction(self.vault, journal, "restore-snapshot")
        listed = storage.rollback_conflicts(self.vault)
        self.assertEqual(len(listed), 1)
        (conflict,) = listed[0]["conflicts"]
        self.assertIn("second human save", (self.vault / conflict["preserved"]).read_text())
        kept = "\n".join(path.read_text() for path in (self.vault / ".kb" / "rolled-back").rglob("a*.md"))
        self.assertIn("first human save", kept)  # the acknowledged version is still there


class ConflictPlacementFailureTests(_Vault):
    """Finding 4: a failure placing the conflict copy must not strand the only human version."""

    def test_failed_conflict_copy_reports_where_the_human_version_is(self):
        write_memory(self.config, "facts/note.md", "kb:global:fact:note", "Note", "Some fact", scope="global")
        calls: list[int] = []
        real_copy = storage._copy_exclusive

        def copy(source, destination):
            calls.append(1)
            if len(calls) == 1:
                real_copy(source, destination)
                # An editor holding the note open writes after the archive copy, so the
                # captured file no longer matches it and a conflict copy is needed.
                _editor_append(Path(source), MARKER + "\n")
                return
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")  # placing the conflict copy fails
            real_copy(source, destination)

        with (
            patch("autonomic_kb.storage.os.link", side_effect=OSError(errno.EXDEV, "cross-device link")),
            patch("autonomic_kb.storage._copy_exclusive", side_effect=copy),
        ):
            try:
                result = forget(self.config, "kb:global:fact:note")
            except OSError as error:
                result = {"error": str(error), "notes": getattr(error, "__notes__", [])}
        self.assertGreaterEqual(len(calls), 2, result)
        self.assert_human_bytes_retained()


class LongNameTests(_Vault):
    """Finding 5: staging and other intermediate names must fit the filesystem's name limit."""

    def _limit(self) -> int:
        return os.pathconf(self.vault, "PC_NAME_MAX")

    def _round_trip(self, stem: str) -> None:
        name = f"{stem}.md"
        note = write_memory(self.config, f"facts/{name}", "kb:global:fact:long", "Long", "A note.", scope="global")
        original = note.read_text()
        archive = self.vault / self.config.archive_dir
        archive.mkdir(parents=True, exist_ok=True)
        (archive / name).write_text("an archived note with the same name\n")  # forces a suffixed name
        with self.assertRaises(RuntimeError), semantic_transaction(self.vault):
            move_into(note, archive, "kb:global:fact:long")
            raise RuntimeError("injected failure")  # rollback captures and restores the long name
        self.assertEqual(note.read_text(), original)
        result = forget(self.config, "kb:global:fact:long")
        self.assertEqual(result["action"], "archived")
        self.assertIn("A note.", (self.vault / result["destination"]).read_text())
        self.assertEqual((archive / name).read_text(), "an archived note with the same name\n")

    def test_the_223_byte_name_from_the_issue(self):
        self._round_trip("n" * (223 - len(".md")))

    def test_an_ascii_name_at_the_limit(self):
        self._round_trip("a" * (self._limit() - len(".md")))

    def test_a_multibyte_name_at_the_limit(self):
        stem = "é" * ((self._limit() - len(".md")) // 2)
        self.assertLessEqual(len(os.fsencode(stem + ".md")), self._limit())
        self._round_trip(stem)


if __name__ == "__main__":
    unittest.main()
