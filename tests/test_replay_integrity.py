"""Replay comparisons must use frozen inputs and keep failures observable."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.replay import MatchedReplayHarness
from tests.support import make_vault


class ReplayIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config = make_vault(self.root, self.repo)
        self.harness = MatchedReplayHarness(self.config)

    def case(self):
        command = [
            sys.executable,
            "-S",
            "-c",
            'print(\'{"input_tokens":10,"output_tokens":2,"maintenance_tokens":0}\')',
        ]
        return {
            "task": "test paired state",
            "baseline_command": command,
            "kb_command": command,
            "evaluator_command": [sys.executable, "-S", "-c", "pass"],
            "model_id": "test",
            "agent_version": "test",
            "tool_config": "test",
        }

    def run_spec(self, cases):
        path = self.root / "spec.json"
        path.write_text(json.dumps({"tasks": cases}))
        return self.harness.run(path)

    def test_both_arms_use_one_frozen_repository_snapshot(self):
        source = self.repo / "value.txt"
        source.write_text("before")
        case = self.case()
        case["evaluator_command"] = [
            sys.executable,
            "-S",
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text() == 'before'",
        ]
        copy = self.harness._copy_snapshot

        def copy_then_external_edit(src, dst, **kwargs):
            copy(src, dst, **kwargs)
            if src == self.repo:
                source.write_text("after")

        with patch.object(self.harness, "_copy_snapshot", side_effect=copy_then_external_edit):
            result = self.run_spec([case])["results"][0]
        self.assertTrue(result["baseline"]["success"])
        self.assertTrue(result["kb"]["success"])
        self.assertEqual(source.read_text(), "after")

    def test_timeout_is_still_independently_evaluated(self):
        result = self.harness._run_one(
            "timeout",
            "pair",
            "kb",
            [sys.executable, "-S", "-c", "import time; time.sleep(3)"],
            self.repo,
            0.5,
            [sys.executable, "-S", "-c", "pass"],
            self.config.vault,
        )
        self.assertFalse(result.success)
        self.assertTrue(result.metadata["evaluated"])
        self.assertEqual(result.metadata["agent_status"], "timeout")
        self.assertIsNone(result.total_tokens)

    def test_malformed_metrics_do_not_erase_an_evaluated_task(self):
        metrics = {
            "input_tokens": 10,
            "output_tokens": 2,
            "maintenance_tokens": 0,
            "cached_tokens": 50,
            "retrieved_ids": None,
        }
        result = self.harness._run_one(
            "bad metrics",
            "pair",
            "kb",
            [sys.executable, "-S", "-c", f"print({json.dumps(metrics)!r})"],
            self.repo,
            5,
            [sys.executable, "-S", "-c", "pass"],
            self.config.vault,
        )
        self.assertTrue(result.success)
        self.assertIsNone(result.total_tokens)
        self.assertTrue(result.metadata["metric_errors"])
        self.assertEqual(result.retrieved_ids, [])

    def test_entire_spec_is_checked_before_any_command_executes(self):
        invalid = self.case()
        invalid["evaluator_command"] = "not an argv array"
        with patch.object(self.harness, "_run_one") as run, self.assertRaises(ValueError):
            self.run_spec([self.case(), invalid])
        run.assert_not_called()

    def test_duplicate_pair_ids_are_rejected_before_execution(self):
        case = {**self.case(), "pair_id": "duplicate"}
        with patch.object(self.harness, "_run_one") as run, self.assertRaisesRegex(ValueError, "pair"):
            self.run_spec([case, case])
        run.assert_not_called()

    def test_timeout_must_be_positive_and_finite(self):
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), patch.object(self.harness, "_run_one") as run:
                with self.assertRaises(ValueError):
                    self.run_spec([{**self.case(), "timeout": timeout}])
                run.assert_not_called()

    def test_nested_vault_is_not_copied_into_either_repository_arm(self):
        nested = self.repo / "memory"
        nested.mkdir()
        (nested / "knowledge.md").write_text("candidate knowledge")
        self.config.vault = nested
        self.config.ensure_runtime()
        self.harness = MatchedReplayHarness(self.config)
        case = self.case()
        case["evaluator_command"] = [
            sys.executable,
            "-S",
            "-c",
            "from pathlib import Path; assert not Path('memory/knowledge.md').exists()",
        ]
        result = self.run_spec([case])["results"][0]
        self.assertTrue(result["baseline"]["success"])
        self.assertTrue(result["kb"]["success"])

    def test_worktree_git_pointer_cannot_reuse_the_original_git_database(self):
        (self.repo / ".git").write_text("gitdir: ../shared-git")
        with self.assertRaisesRegex(ValueError, "worktree|standalone"):
            self.harness._copy_snapshot(self.repo, self.root / "copy")

    def test_ignored_virtualenv_symlink_is_not_copied_or_traversed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / ".venv").symlink_to(outside, target_is_directory=True)
        destination = self.root / "copy"
        self.harness._copy_snapshot(self.repo, destination)
        self.assertFalse((destination / ".venv").exists())

    @unittest.skipIf(os.name == "nt", "POSIX executable bits")
    def test_snapshot_identity_covers_executable_permissions(self):
        script = self.repo / "run.sh"
        script.write_text("echo checked\n")
        script.chmod(0o644)
        first = self.harness._fingerprint(self.repo)
        script.chmod(0o755)
        self.assertNotEqual(first, self.harness._fingerprint(self.repo))


if __name__ == "__main__":
    unittest.main()
