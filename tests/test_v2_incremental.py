from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.retrieval import Retriever
from tests.support import make_vault, write_memory


class IncrementalIndexTests(unittest.TestCase):
    def test_unchanged_note_is_not_reread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            note = write_memory(config, "one.md", "kb:repository:fact:one", "One", "stable content")
            with KnowledgeIndex(config) as index:
                self.assertEqual(index.index_vault().indexed, 1)
                original = Path.read_text

                def guarded(path: Path, *args, **kwargs):
                    if path == note:
                        raise AssertionError("unchanged Markdown was reread")
                    return original(path, *args, **kwargs)

                with patch.object(Path, "read_text", guarded):
                    stats = index.index_vault()
                self.assertEqual(stats.unchanged, 1)
                self.assertEqual(stats.updated, 0)

    def test_no_retrieval_route_can_return_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            write_memory(config, "fact.md", "kb:repository:fact:x", "X", "completely unrelated durable fact")
            with KnowledgeIndex(config) as index:
                manifest = Retriever(config, index).retrieve("hello", budget=120)
            self.assertEqual(manifest.route, "none")
            self.assertEqual(manifest.state, "no_retrieval_needed")
            self.assertEqual(manifest.items, [])


if __name__ == "__main__":
    unittest.main()
