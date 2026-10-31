"""Failure-injection contracts for indexing, links, and evaluation accounting."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.benchmark import TraceBenchmarkRunner
from autonomic_kb.cli import build_parser
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.telemetry import TaskOutcome, TelemetryStore
from autonomic_kb.util import sha256_text
from tests.support import make_vault, write_memory


class IndexIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        self.index = KnowledgeIndex(self.config)
        self.addCleanup(self.index.close)

    def note(self, path, identity, title="Shared", relations=None):
        return write_memory(self.config, path, identity, title, "original claim", relations=relations)

    def snapshot(self):
        return {
            table: sorted(tuple(row) for row in self.index.connection.execute(f"SELECT * FROM {table}"))
            for table in ("notes", "notes_fts", "links", "file_manifest", "meta")
        }

    def targets(self, source="source"):
        return dict(self.index.connection.execute("SELECT target,target_id FROM links WHERE source_id=?", (source,)))

    def test_failed_rebuild_preserves_last_committed_index(self):
        self.note("a.md", "a")
        self.index.index_vault()
        before = self.snapshot()
        with (
            patch.object(self.index, "_parse", side_effect=OSError("injected unreadable note")),
            self.assertRaises(OSError),
        ):
            self.index.rebuild()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.index.search("original")[0]["declared_id"], "a")

    def test_failed_insert_preserves_index_and_manifest(self):
        self.note("a.md", "a")
        self.index.index_vault()
        before = self.snapshot()
        self.note("b.md", "b")
        with (
            patch.object(self.index, "_insert", side_effect=RuntimeError("injected insertion failure")),
            self.assertRaises(RuntimeError),
        ):
            self.index.index_vault()
        self.assertEqual(self.snapshot(), before)
        self.index.index_vault()
        self.assertEqual({row["declared_id"] for row in self.index.all_notes()}, {"a", "b"})

    def test_refresh_does_not_commit_a_callers_outer_transaction(self):
        self.note("a.md", "a")
        self.index.index_vault()
        self.note("b.md", "b")
        self.index.connection.execute("INSERT INTO meta(key,value) VALUES('caller-owned','pending')")
        self.index.index_vault()
        self.assertIsNotNone(self.index.get("b"))
        self.index.connection.rollback()
        self.assertIsNone(self.index.get("b"))
        self.assertIsNotNone(self.index.get("a"))
        self.assertIsNone(self.index.connection.execute("SELECT value FROM meta WHERE key='caller-owned'").fetchone())

    def test_other_readers_do_not_observe_an_empty_rebuild(self):
        import sqlite3

        self.note("a.md", "a")
        self.index.index_vault()
        parse = self.index._parse
        observed = []

        def inspect_reader(*args):
            reader = sqlite3.connect(self.config.index_path)
            try:
                observed.append(reader.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            finally:
                reader.close()
            return parse(*args)

        with patch.object(self.index, "_parse", side_effect=inspect_reader):
            self.index.rebuild()
        self.assertEqual(observed, [1])

    def test_source_relative_links_are_bound_inside_the_vault(self):
        self.note("folder/note.md", "a")
        self.note("outside.md", "b", "Outside")
        self.note("folder/source.md", "source", "Source", {"related-to": ["./note", "../outside", "../../outside"]})
        self.index.index_vault()
        self.assertEqual(self.targets(), {"./note": "a", "../outside": "b", "../../outside": ""})

    @unittest.skipIf(os.name == "nt", "Windows ctime can represent creation rather than metadata change")
    def test_same_size_in_place_edit_with_restored_mtime_is_not_cached(self):
        path = self.note("a.md", "a")
        self.index.index_vault()
        old = path.stat()
        path.write_text(path.read_text().replace("original", "modified"))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        if path.stat().st_ctime_ns == old.st_ctime_ns:
            self.skipTest("filesystem does not expose a distinct change time")
        self.index.index_vault()
        self.assertEqual(self.index.get("a")["summary"], "modified claim")

    def test_generated_duplicate_identity_cannot_collide_with_a_declared_id(self):
        reserved = "shared::duplicate::" + sha256_text("b.md")[:8]
        self.note("a.md", "shared")
        self.note("b.md", "shared")
        self.note("c.md", reserved)
        self.index.index_vault()
        self.assertEqual(len(self.index.all_notes()), 3)
        self.assertEqual(self.index.get(reserved)["path"], "c.md")
        self.assertEqual(len({row["id"] for row in self.index.all_notes()}), 3)

    def test_ambiguous_title_does_not_create_arbitrary_graph_edge(self):
        self.note("a/note.md", "a")
        self.note("b/note.md", "b")
        self.note("source.md", "source", "Source", {"related-to": ["Shared", "note", "a/note"]})
        self.index.index_vault()
        self.assertEqual(self.targets(), {"Shared": "", "note": "", "a/note": "a"})
        (self.config.vault / "b/note.md").unlink()
        self.index.index_vault()
        self.assertEqual(self.targets()["Shared"], "a")

    def test_missing_qualified_link_does_not_fall_back_to_another_directory(self):
        self.note("real/note.md", "a")
        self.note("source.md", "source", "Source", {"related-to": ["missing/note", "real/note.md"]})
        self.index.index_vault()
        self.assertEqual(self.targets(), {"missing/note": "", "real/note.md": "a"})

    def test_duplicate_canonical_target_remains_unresolved(self):
        self.note("a.md", "shared", "A")
        self.note("b.md", "shared", "B")
        self.note("source.md", "source", "Source", {"related-to": ["shared"]})
        self.index.index_vault()
        self.assertEqual(self.targets()["shared"], "")

    def test_rebuild_and_incremental_refresh_have_same_logical_graph(self):
        self.note("z.md", "shared", "Z")
        self.index.index_vault()
        self.note("a.md", "shared", "A")
        self.note("source.md", "source", "Source", {"related-to": ["shared", "A", "Z"]})
        self.index.index_vault()
        incremental = self.targets()
        self.index.rebuild()
        rebuilt = self.targets()
        # Canonical references are ambiguous in both cases; paths/titles identify the same files.
        self.assertEqual(incremental["shared"], rebuilt["shared"])
        for target in ("A", "Z"):
            self.assertEqual(self.index.get(rebuilt[target])["title"], target)


class OutcomeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = make_vault(Path(self.temporary.name))
        self.store = TelemetryStore(self.config)

    def outcome(self, pair, mode, success=True, **kwargs):
        return TaskOutcome(
            f"{pair}:{mode}",
            "task",
            mode,
            success,
            pair_id=pair,
            metadata={
                "repo_snapshot": "repo",
                "vault_snapshot": "vault",
                "model_id": "model",
                "agent_version": "agent",
                "evaluator_id": "evaluator",
                "tool_config": "tools",
                "evaluated": True,
            },
            **kwargs,
        )

    def test_missing_maintenance_and_work_counters_stay_unknown(self):
        outcome = self.outcome("pair", "kb", input_tokens=10, output_tokens=2)
        self.store.record_outcome(outcome)
        self.assertIsNone(outcome.total_tokens)
        self.assertIsNone(outcome.searches)
        self.assertIsNone(outcome.file_reads)
        self.assertFalse(outcome.usage_complete)

    def test_cli_does_not_invent_zero_usage(self):
        args = build_parser().parse_args(["outcome", "--task", "x", "--mode", "kb"])
        for name in ("cached_tokens", "maintenance_tokens", "searches", "file_reads", "latency_ms"):
            self.assertIsNone(getattr(args, name), name)

    def test_incomplete_usage_flag_is_not_overwritten_by_present_counts(self):
        outcome = self.outcome(
            "pair",
            "kb",
            input_tokens=10,
            output_tokens=2,
            maintenance_tokens=0,
            usage_complete=False,
        )
        self.store.record_outcome(outcome)
        self.assertFalse(outcome.usage_complete)
        self.assertIsNone(outcome.total_tokens)

    def test_task_failures_remain_visible_when_token_usage_is_missing(self):
        for mode in ("no-kb", "kb"):
            self.store.record_outcome(
                self.outcome(
                    "complete",
                    mode,
                    input_tokens=10,
                    output_tokens=2,
                    maintenance_tokens=0,
                )
            )
        self.store.record_outcome(
            self.outcome("failed", "no-kb", input_tokens=20, output_tokens=2, maintenance_tokens=0)
        )
        self.store.record_outcome(self.outcome("failed", "kb", success=False))
        report = TraceBenchmarkRunner(self.config).run()
        self.assertEqual(report["mean_success_delta"], -0.5)
        self.assertEqual(report["matched_pairs"], 2)
        self.assertEqual(report["token_pairs"], 1)
        self.assertEqual(report["mean_token_delta"], 0)

    def test_usage_unknown_pair_never_reports_zero_cost_savings(self):
        self.store.record_outcome(self.outcome("unknown", "no-kb"))
        self.store.record_outcome(self.outcome("unknown", "kb"))
        report = TraceBenchmarkRunner(self.config).run()
        self.assertIsNone(report["mean_token_delta"])
        self.assertEqual(report["mean_success_delta"], 0)

    def test_pair_ids_do_not_share_namespace_with_legacy_task_hashes(self):
        for mode in ("no-kb", "kb"):
            self.store.record_outcome(self.outcome("same", mode))
            self.store.record_outcome(TaskOutcome(f"legacy:{mode}", "same", mode, True))
        result = self.store.paired_summary()
        self.assertEqual(result["pairs"], 2)
        self.assertEqual(sum(item["matched"] for item in result["items"]), 1)

    def test_precomputed_totals_are_not_trusted(self):
        for mode in ("no-kb", "kb"):
            self.store.record_outcome(self.outcome("p", mode, input_tokens=10, output_tokens=2, maintenance_tokens=0))
        rows = self.store.outcomes()
        rows[1]["total_tokens"] = -99999
        self.store.outcome_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        self.assertEqual(TraceBenchmarkRunner(self.config).run()["mean_token_delta"], 0)

    def test_earlier_accounting_is_preserved_but_not_silently_upgraded(self):
        for mode in ("no-kb", "kb"):
            self.store.record_outcome(self.outcome("p", mode, input_tokens=10, output_tokens=2, maintenance_tokens=0))
        rows = self.store.outcomes()
        for row in rows:
            row.pop("accounting_version")
        self.store.outcome_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        report = TraceBenchmarkRunner(self.config).run()
        self.assertEqual(report["matched_pairs"], 1)
        self.assertEqual(report["token_pairs"], 0)
        self.assertEqual(report["mean_success_delta"], 0)
        self.assertIsNone(report["mean_token_delta"])
        self.assertEqual(len(self.store.outcomes()), 2)

    def test_corrupt_outcome_record_is_not_silently_lost(self):
        for raw in ('{"broken":', "[]"):
            self.store.outcome_path.write_text(raw + "\n")
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "line 1"):
                self.store.paired_summary()

    def test_invalid_outcome_labels_and_latencies_are_rejected(self):
        for field, value in (
            ("success", "false"),
            ("unsafe", "false"),
            ("kb_mode", "typo"),
            ("latency_ms", float("nan")),
            ("latency_ms", -1),
        ):
            outcome = self.outcome("bad", "kb")
            setattr(outcome, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.store.record_outcome(outcome)


if __name__ == "__main__":
    unittest.main()
