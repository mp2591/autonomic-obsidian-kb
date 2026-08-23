from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.git_context import GitContext
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.retrieval import Retriever
from tests.support import make_vault, write_memory


class RetrievalTests(unittest.TestCase):
    def _git(self):
        return GitContext(root="/tmp/demo", branch="main", remote="", head="abc", changed_paths=["src/index.py"])

    def test_budget_progressive_disclosure_and_why(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            write_memory(
                config,
                "commands/test.md",
                "kb:repository:command:test",
                "Test command",
                "Run python -m unittest discover to execute the complete test suite",
                memory_type="command",
                applies_to=["tests/**", "src/**"],
            )
            write_memory(
                config,
                "architecture/large.md",
                "kb:repository:architecture:large",
                "Broad architecture",
                "A broad unrelated architecture description",
                memory_type="architecture",
            )
            with KnowledgeIndex(config) as index, patch("autonomic_kb.retrieval.inspect_git", return_value=self._git()):
                manifest = Retriever(config, index).retrieve(
                    "What command runs the unittest suite?", budget=160, paths=["tests/test_cli.py"]
                )
                self.assertLessEqual(manifest.used_tokens, 160)
                self.assertEqual(manifest.items[0].id, "kb:repository:command:test")
                self.assertLessEqual(manifest.items[0].layer, 2)
                why = index.why("kb:repository:command:test")
                self.assertTrue(why["recent_decisions"][0]["selected"])

    def test_module_and_branch_scope_are_hard_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            write_memory(
                config,
                "module.md",
                "kb:module:fact:api",
                "API module",
                "special API behavior",
                scope="module",
                module="api",
                applies_to=["api/**"],
            )
            write_memory(
                config,
                "branch.md",
                "kb:branch:fact:experiment",
                "Branch behavior",
                "special API behavior",
                scope="branch",
                branch="experiment",
            )
            write_memory(config, "repo.md", "kb:repository:fact:general", "General API", "special API behavior")
            with KnowledgeIndex(config) as index, patch("autonomic_kb.retrieval.inspect_git", return_value=self._git()):
                manifest = Retriever(config, index).retrieve("special API behavior", budget=300, paths=["src/index.py"])
                ids = {item.id for item in manifest.items}
                self.assertIn("kb:repository:fact:general", ids)
                self.assertNotIn("kb:module:fact:api", ids)
                self.assertNotIn("kb:branch:fact:experiment", ids)
                reasons = " ".join(item["reason"] for item in manifest.excluded)
                self.assertIn("module", reasons)
                self.assertIn("branch", reasons)

    def test_graph_expansion_can_add_the_specific_solution(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            write_memory(
                config,
                "failure.md",
                "kb:repository:known-failure:sqlite",
                "SQLite lock failure",
                "sqlite database locked during indexing",
                memory_type="known-failure",
                relations={"fixed-by": ["kb:repository:solution:connection-ownership"]},
                applies_to=["src/**"],
            )
            write_memory(
                config,
                "solution.md",
                "kb:repository:solution:connection-ownership",
                "Connection ownership",
                "Use deterministic connection ownership and context managers",
                memory_type="solution",
                applies_to=["src/**"],
            )
            with KnowledgeIndex(config) as index, patch("autonomic_kb.retrieval.inspect_git", return_value=self._git()):
                manifest = Retriever(config, index).retrieve(
                    "debug sqlite database locked", budget=300, paths=["src/index.py"]
                )
                ids = {item.id for item in manifest.items}
                self.assertIn("kb:repository:known-failure:sqlite", ids)
                self.assertIn("kb:repository:solution:connection-ownership", ids)


if __name__ == "__main__":
    unittest.main()
