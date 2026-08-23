from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomic_kb.healing import Compactor, Healer, forget
from autonomic_kb.markdown import parse_markdown
from autonomic_kb.validation import Validator
from tests.support import make_vault, write_memory


class ValidationHealingTests(unittest.TestCase):
    def test_detects_duplicate_ids_broken_links_and_contradictions(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            first = write_memory(
                config,
                "a.md",
                "kb:repository:fact:duplicate",
                "A",
                "The store is markdown",
                claim_key="authority",
                claim_value="markdown",
            )
            first.write_text(first.read_text() + "\n[[Missing Note]]\n")
            write_memory(
                config,
                "b.md",
                "kb:repository:fact:duplicate",
                "B",
                "The store is sqlite",
                claim_key="authority",
                claim_value="sqlite",
            )
            v = Validator(config)
            report = v.validate()
            v.close()
            codes = {i.code for i in report.issues}
            self.assertIn("duplicate-id", codes)
            self.assertIn("broken-link", codes)
            self.assertIn("contradiction", codes)

    def test_heal_repairs_mechanical_metadata_but_not_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            path = config.vault / "raw.md"
            path.write_text("A raw durable fact.\n")
            h = Healer(config)
            plan = h.heal(False)
            result = h.heal(True)
            h.close()
            self.assertTrue(any(a["action"] == "repair-metadata" for a in plan["actions"]))
            metadata = parse_markdown(path.read_text()).metadata
            self.assertIn("id", metadata)
            self.assertGreater(result["applied"], 0)

    def test_source_change_is_marked_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            source = repo / "config.txt"
            source.write_text("one")
            config = make_vault(root, repo)
            expected = hashlib.sha256(b"one").hexdigest()
            note = write_memory(
                config,
                "fact.md",
                "kb:repository:fact:source",
                "Source fact",
                "Config is one",
                invalidation={"source_hashes": {"config.txt": expected}},
            )
            source.write_text("two")
            h = Healer(config)
            result = h.heal(True)
            h.close()
            metadata = parse_markdown(note.read_text()).metadata
            self.assertEqual(metadata["status"], "stale")
            self.assertGreater(result["applied"], 0)

    def test_compact_and_forget_archive_non_destructively(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            write_memory(
                config, "old.md", "kb:repository:fact:old", "Old", "old low value", status="stale", utility=0.1
            )
            c = Compactor(config)
            result = c.compact(True)
            c.close()
            self.assertEqual(result["applied"], 1)
            write_memory(config, "forget.md", "kb:repository:fact:forget", "Forget", "archive me")
            archived = forget(config, "kb:repository:fact:forget")
            self.assertEqual(archived["action"], "archived")
            self.assertTrue((config.vault / archived["destination"]).exists())


if __name__ == "__main__":
    unittest.main()
