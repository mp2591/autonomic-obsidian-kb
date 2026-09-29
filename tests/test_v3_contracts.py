from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.calibration import RankPolicy, train_rank_policy
from autonomic_kb.code_graph import RepositoryCodeGraph
from autonomic_kb.compiler import compile_task_view
from autonomic_kb.context_state import ContextStateStore
from autonomic_kb.episodes import EpisodeStore
from autonomic_kb.evidence import EvidenceStore, OperationLedger
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.learning import Learner, LearningCandidate
from autonomic_kb.leases import LeaseStore
from autonomic_kb.mcp_server import MCPServer
from autonomic_kb.models import TaskContext
from autonomic_kb.read_policy import memory_read_gate
from autonomic_kb.replay import MatchedReplayHarness
from autonomic_kb.retrieval import Retriever
from autonomic_kb.scoring import scope_gate, temporal_gate
from autonomic_kb.security import trust_gate
from autonomic_kb.shadow import ShadowEvaluator
from autonomic_kb.storage import recover_transactions, semantic_files, semantic_transaction
from autonomic_kb.telemetry import TaskOutcome, TelemetryStore
from autonomic_kb.tokenizer import TOKENIZERS
from autonomic_kb.util import sha256_file, stable_json
from autonomic_kb.validation import ValidatorRegistry
from tests.support import make_vault, write_memory


class V3Contracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.config = make_vault(self.root, self.repo)
        self.index = KnowledgeIndex(self.config)
        self.addCleanup(self.index.close)

    def note(self, name="one", **metadata):
        return write_memory(self.config, f"{name}.md", name, name, "unittest build procedure", **metadata)

    def retrieve(self, task="unittest build procedure", **kwargs):
        return Retriever(self.config, self.index).retrieve(task, **kwargs)

    def test_small_budgets_are_rejected_not_increased(self):
        for budget in (0, 1, 79, -2):
            with self.assertRaises(ValueError):
                self.retrieve(budget=budget)

    def test_both_delivered_formats_fit_declared_counter(self):
        self.note(memory_type="command")
        for budget in (80, 100, 160, 300):
            manifest = self.retrieve(budget=budget)
            for text in (manifest.to_markdown(), stable_json(manifest.to_agent_dict())):
                self.assertLessEqual(TOKENIZERS.count(text).tokens, budget)
            self.assertFalse(manifest.token_count_exact)

    def test_exact_adapter_is_used_for_final_payload(self):
        TOKENIZERS.register("test-byte-counter", lambda text: len(text.encode("utf-8")))
        self.addCleanup(TOKENIZERS._adapters.pop, "test-byte-counter")
        self.note()
        manifest = self.retrieve(budget=300, agent="test-byte-counter")
        self.assertTrue(manifest.token_count_exact)
        self.assertLessEqual(len(stable_json(manifest.to_agent_dict()).encode()), 300)

    def test_single_identifier_can_retrieve(self):
        write_memory(self.config, "WAL.md", "WAL", "WAL", "write ahead logging")
        self.assertIn("WAL", {item.id for item in self.retrieve("WAL").items})

    def test_relevant_procedure_is_not_sufficient_without_checks(self):
        self.note(memory_type="command", validators=[{"kind": "file-exists", "path": "a.py"}])
        result = self.retrieve()
        self.assertTrue(result.items)
        self.assertEqual(result.state, "partial_context")
        self.assertIn("verification", result.missing_evidence)

    def test_compiler_retains_whole_command_and_preconditions(self):
        note = {
            "summary": "run tests",
            "l2": "```sh\npython -m unittest\n```",
            "metadata": {"preconditions": ["Use the isolated test environment"], "verification": "All tests pass"},
        }
        _, text = compile_task_view(note, "unittest", 2, 300)
        self.assertIn("Use the isolated test environment", text)
        self.assertIn("```sh\npython -m unittest\n```", text)
        self.assertIn("All tests pass", text)
        self.assertEqual(compile_task_view(note, "unittest", 2, 1)[1], "")

    def test_ineligible_flood_cannot_exhaust_candidate_limit(self):
        self.config.max_candidates = 2
        for number in range(12):
            self.note(f"bad-{number}", status="quarantined")
        self.note("good")
        self.assertEqual([item.id for item in self.retrieve().items], ["good"])

    def test_rank_examples_preserve_retrieval_features(self):
        self.note()
        result = self.retrieve()
        row = self.index.connection.execute(
            "SELECT feature_json FROM rank_examples WHERE retrieval_id=? AND selected=1", (result.retrieval_id,)
        ).fetchone()
        self.assertGreater(json.loads(row[0])["lexical"], 0)

    def test_shadow_does_not_record_usage_or_rank_examples(self):
        self.note()
        ShadowEvaluator(self.config).compare("unittest build procedure", ["lexical", "hybrid"])
        for table in ("usage", "rank_examples"):
            self.assertEqual(self.index.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_mcp_uses_one_bounded_content_representation(self):
        self.note()
        reply = MCPServer(self.config).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "kb_retrieve", "arguments": {"task": "unittest build procedure", "budget": 160}},
            }
        )
        self.assertNotIn("structuredContent", reply["result"])
        self.assertLessEqual(TOKENIZERS.count(reply["result"]["content"][0]["text"]).tokens, 160)

    def test_mcp_rejects_undeclared_authority(self):
        with self.assertRaises(ValueError):
            MCPServer(self.config).call_tool(
                "kb_remember",
                {
                    "title": "claim",
                    "summary": "ordinary claim",
                    "authority": "source-of-truth",
                    "authorized_instruction": True,
                },
            )
        self.assertEqual(len(list(self.config.vault.rglob("*.md"))), 0)

    def test_direct_reads_do_not_bypass_policy(self):
        self.note(status="quarantined")
        self.note("foreign", repository_id="git:https://example.invalid/other/repo")
        server = MCPServer(self.config)
        for identity in ("one", "foreign"):
            self.assertEqual(server.call_tool("kb_why", {"id": identity}), {"error": "not-found"})
            self.assertEqual(server.read_resource(f"kb://memory/{identity}"), {"error": "not-found"})
        self.assertEqual(server.read_resource("kb://memories")["memories"], [])

    def test_uncertain_does_not_override_privileged_or_hostile_gates(self):
        self.assertFalse(trust_gate("active", "verified", 1, True, memory_type="agent-instruction")[0])
        self.assertFalse(trust_gate("active", "verified", 1, True, taint="hostile")[0])

    def test_secret_is_rejected_at_every_ingestion_sink(self):
        secret = "password=" + "EXAMPLESECRET0123" * 2
        learner = Learner(self.config, self.index)
        calls = [
            lambda: learner.remember(LearningCandidate(title="x", summary=secret)),
            lambda: EpisodeStore(self.config).capture(secret),
            lambda: EvidenceStore(self.config).put("observation", "clean", metadata={"detail": secret}),
            lambda: TelemetryStore(self.config).feedback("r", "m", "helpful", notes=secret),
        ]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
        for path in self.config.vault.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".json", ".jsonl"}:
                self.assertNotIn(secret, path.read_text())

    def test_evidence_binds_provenance_and_requested_identity(self):
        store = EvidenceStore(self.config)
        one = store.put("observation", "equal text", repository_id="one")
        two = store.put("observation", "equal text", repository_id="two")
        self.assertNotEqual(one.evidence_id, two.evidence_id)
        digest = one.evidence_id.rsplit(":", 1)[1]
        path = self.config.evidence_dir / digest[:2] / f"{digest}.json"
        row = json.loads(path.read_text())
        row["evidence_id"] = two.evidence_id
        path.write_text(json.dumps(row))
        self.assertFalse(store.verify(one.evidence_id))
        self.assertIsNone(store.get("../../outside"))

    def test_missing_evidence_blocks_retrieval(self):
        self.note(evidence=["evidence:sha256:" + "0" * 64])
        self.assertEqual(self.retrieve().items, [])

    def test_changed_source_invalidates_only_dependent_memory(self):
        source = self.repo / "code.py"
        source.write_text("value = 1")
        self.note("dependent", dependencies=[{"path": "code.py", "sha256": sha256_file(source)}])
        self.note("independent")
        source.write_text("value = 2")
        self.assertEqual({item.id for item in self.retrieve().items}, {"independent"})

    def test_temporal_and_version_gates_are_explicit(self):
        context = TaskContext("old", at="2020-06-01", versions={"demo": "2.1"})
        note = {
            "valid_from": "2020-01-01",
            "valid_to": "2021-01-01",
            "version_range": ">=2,<3",
            "metadata": {"version_package": "demo"},
        }
        self.assertTrue(temporal_gate(note, context, self.repo)[0])
        context.versions = {}
        self.assertFalse(temporal_gate(note, context, self.repo)[0])
        context.at = "not-a-date"
        self.assertFalse(temporal_gate(note, context, self.repo)[0])

    def test_module_prefix_is_not_directory_boundary(self):
        note = {"scope": "module", "metadata": {"applies_to": ["src/api"]}}
        self.assertFalse(scope_gate(note, TaskContext("x", requested_paths=["src/apiv2/x.py"]))[0])

    def test_duplicate_detection_respects_prerequisites_and_body(self):
        learner = Learner(self.config, self.index)
        one = learner.remember(
            LearningCandidate("recipe", "run the check", detail="step one", preconditions=["linux"]), True
        )
        two = learner.remember(
            LearningCandidate("recipe", "run the check", detail="step one", preconditions=["windows"]), True
        )
        three = learner.remember(
            LearningCandidate("recipe", "run the check", detail="step two", preconditions=["linux"]), True
        )
        self.assertEqual(len({one["memory_id"], two["memory_id"], three["memory_id"]}), 3)

    def test_duplicate_adds_corroborating_evidence(self):
        learner = Learner(self.config, self.index)
        first = learner.remember(LearningCandidate("claim", "stable claim"), True)
        evidence = EvidenceStore(self.config).put("observation", "independent support")
        learner.remember(LearningCandidate("claim", "stable claim", evidence=[evidence.evidence_id]), True)
        self.assertIn(evidence.evidence_id, self.index.get(first["memory_id"])["metadata"]["evidence"])

    def test_recurrence_is_scoped_and_not_substring_based(self):
        store = EpisodeStore(self.config)
        store.capture("same task", observations=["check failed"], repository_id="one")
        store.capture("same task", observations=["check failed"], repository_id="one")
        store.capture("different task", observations=["check failed"], repository_id="two")
        self.assertEqual(store.recurrence("check failed", "one"), 1)
        self.assertEqual(store.recurrence("failed", "one"), 0)

    def test_verified_episode_yields_procedure(self):
        evidence = EvidenceStore(self.config).put("evaluation", "independent tests passed")
        episode = EpisodeStore(self.config).capture(
            "run project checks",
            outcome="success",
            successful_actions=["python -m unittest"],
            preconditions=["test environment"],
            verification="all tests passed",
            evidence=[evidence.evidence_id],
        )
        results = Learner(self.config, self.index).consolidate_episode(episode)
        self.assertTrue(results)
        self.assertEqual(self.index.get(results[0]["memory_id"])["type"], "procedure")

    def test_semantic_rollback_restores_renames_and_events(self):
        path = self.note()
        before = semantic_files(self.config.vault)
        with self.assertRaises(RuntimeError), semantic_transaction(self.config.vault):
            path.rename(self.config.vault / "moved.md")
            OperationLedger(self.config).append("AMEND", "one", new_digest="changed")
            raise RuntimeError("injected failure")
        self.assertEqual(semantic_files(self.config.vault), before)

    def test_interrupted_transaction_requires_reconciliation(self):
        journal = self.config.vault / ".kb-transactions" / "interrupted.json"
        journal.parent.mkdir()
        journal.write_text('{"state":"prepared","before":{}}')
        with self.assertRaises(ValueError):
            recover_transactions(self.config.vault)

    def test_ledger_compare_and_swap_is_atomic(self):
        store = OperationLedger(self.config)
        store.append("ADD", "one", new_digest="initial")

        def attempt(value):
            try:
                store.append("AMEND", "one", new_digest=value, require_previous="initial")
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, ["a", "b"])), 1)
        self.assertEqual(store.last_for("one").sequence, 2)

    def test_lease_acquisition_has_one_winner(self):
        def attempt(agent):
            try:
                LeaseStore(self.config).acquire("same task", agent)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, ["a", "b"])), 1)

    def test_command_validators_never_execute(self):
        with patch("subprocess.run") as run:
            issues = ValidatorRegistry(self.config).run(
                {"validators": [{"kind": "command", "argv": ["python"]}]}, "x.md"
            )
        run.assert_not_called()
        self.assertEqual(issues[0].code, "validator-execution-disabled")

    def test_model_and_epoch_change_restore_context(self):
        self.note()
        server = MCPServer(self.config)
        first = self.retrieve(session="s", epoch="1")
        server.call_tool("kb_context_ack", {"retrieval_id": first.retrieval_id, "session": "s", "epoch": "1"})
        self.assertEqual(self.retrieve(session="s", epoch="1").state, "unchanged_context")
        self.assertTrue(self.retrieve(session="s", epoch="2").items)
        self.assertTrue(self.retrieve(session="s", epoch="1", agent="another-model").items)

    def test_receipt_detects_source_edit(self):
        path = self.note()
        first = self.retrieve()
        path.write_text(path.read_text() + "\nchanged\n")
        self.assertFalse(
            MCPServer(self.config).call_tool("kb_receipt_check", {"retrieval_id": first.retrieval_id})["valid"]
        )

    def test_context_session_names_do_not_collide(self):
        store = ContextStateStore(self.config.runtime_dir / "context")
        store.acknowledge("a/b", "first", {})
        store.acknowledge("a_b", "second", {})
        self.assertEqual(store.get("a/b").epoch, "first")

    def test_code_graph_refreshes_new_files_and_qualified_symbols(self):
        (self.repo / "a.py").write_text("class A:\n def run(self): pass\nclass B:\n def run(self): pass\n")
        graph = RepositoryCodeGraph(self.repo, self.config.runtime_dir / "graph.json")
        self.assertEqual(len(graph.search_symbols("run")), 2)
        (self.repo / "b.py").write_text("def new_function(): pass\n")
        self.assertIn("new_function", graph.symbols_for_paths(["b.py"]))

    def test_tokens_count_cache_once_and_preserve_unknowns(self):
        outcome = TaskOutcome(
            "t", "h", "kb", True, input_tokens=100, output_tokens=20, cached_tokens=80, maintenance_tokens=3
        )
        self.assertEqual(outcome.total_tokens, 123)
        self.assertIsNone(TaskOutcome("t", "h", "kb", True).total_tokens)
        with self.assertRaises(ValueError):
            TelemetryStore(self.config).record_outcome(TaskOutcome("t", "h", "kb", True, input_tokens=-1))

    def test_ambiguous_pairs_are_reported_not_overwritten(self):
        store = TelemetryStore(self.config)
        for identity in ("a", "b"):
            store.record_outcome(TaskOutcome(identity, "h", "kb", True, pair_id="pair"))
        store.record_outcome(TaskOutcome("c", "h", "no-kb", True, pair_id="pair"))
        self.assertEqual(store.paired_summary()["pairs"], 0)
        self.assertTrue(store.paired_summary()["rejected"])

    def test_replay_isolates_workspaces_vaults_and_requires_evaluation(self):
        command = [
            sys.executable,
            "-c",
            "import json,os; from pathlib import Path; "
            "assert not Path('marker').exists(); Path('marker').write_text('done'); "
            "Path(os.environ['KB_VAULT'],'written').write_text('memory'); "
            "print(json.dumps(dict(input_tokens=10,output_tokens=2,maintenance_tokens=0)))",
        ]
        evaluator = [sys.executable, "-c", "from pathlib import Path; assert Path('marker').read_text() == 'done'"]
        spec = {
            "tasks": [
                {
                    "task": "isolated task",
                    "baseline_command": command,
                    "kb_command": command,
                    "evaluator_command": evaluator,
                    "model_id": "test",
                    "agent_version": "test",
                    "tool_config": "test",
                }
            ]
        }
        path = self.root / "replay.json"
        path.write_text(json.dumps(spec))
        results = MatchedReplayHarness(self.config).run(path)
        self.assertTrue(results["results"][0]["kb"]["success"])
        self.assertTrue(results["summary"]["items"][0]["matched"])
        self.assertFalse((self.repo / "marker").exists())
        self.assertFalse((self.config.vault / "written").exists())
        spec["tasks"][0].pop("evaluator_command")
        path.write_text(json.dumps(spec))
        with self.assertRaises(ValueError):
            MatchedReplayHarness(self.config).run(path)

    def test_replay_exit_zero_does_not_override_failed_evaluator(self):
        outcome = MatchedReplayHarness(self.config)._run_one(
            "wrong result",
            "p",
            "kb",
            [sys.executable, "-c", "pass"],
            self.repo,
            3,
            [sys.executable, "-c", "raise SystemExit(1)"],
            self.config.vault,
        )
        self.assertFalse(outcome.success)
        self.assertIsNone(outcome.total_tokens)

    def test_policy_training_is_disabled_in_release(self):
        for number in range(80):
            features = {key: 0.0 for key in RankPolicy().weights}
            features["lexical"] = float(number % 2)
            self.index.record_rank_example(f"r{number}", "m", features, True)
            self.index.label_rank_example(f"r{number}", "m", float(number % 2))
        for promote in (False, True):
            result = train_rank_policy(self.config, self.index, promote=promote)
            self.assertFalse(result["trained"])
            self.assertFalse(result["promoted"])
            self.assertIn("disabled", result["reason"])
        policy = self.config.runtime_dir / "rank-policy.json"
        policy.write_text('{"version":"rank-v3-learned","weights":{"lexical":2}}')
        self.assertEqual(RankPolicy.load(self.config).version, "rank-v3-static")
        self.assertEqual(RankPolicy.load(self.config).weights, RankPolicy().weights)
        self.assertFalse((self.config.runtime_dir / "rank-policy-candidate.json").exists())

    def test_hostile_schema_does_not_become_a_readable_memory(self):
        self.note(confidence=float("nan"))
        self.index.index_vault()
        self.assertFalse(memory_read_gate(self.index.get("one"), TaskContext("x"), repo_path=self.repo)[0])

    def test_interrupted_journal_blocks_reads(self):
        self.note()
        self.index.index_vault()
        directory = self.config.vault / ".kb-transactions"
        directory.mkdir()
        (directory / "interrupted.json").write_text('{"state":"prepared","before":{}}')
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            self.retrieve()

    def test_symlink_replacement_evicts_previously_indexed_note(self):
        path = self.note()
        self.index.index_vault()
        external = self.root / "outside.md"
        external.write_text(path.read_text())
        path.unlink()
        path.symlink_to(external)
        self.assertEqual(self.retrieve().items, [])

    def test_conflicting_applicable_claims_are_blocked_on_all_reads(self):
        self.note("left", claim_key="backend", claim_value="sqlite")
        self.note("right", claim_key="backend", claim_value="other")
        result = self.retrieve()
        self.assertEqual(result.items, [])
        self.assertEqual(result.state, "conflicting_evidence")
        server = MCPServer(self.config)
        self.assertEqual(server.call_tool("kb_why", {"id": "left"}), {"error": "not-found"})
        self.assertEqual(server.read_resource("kb://memories")["memories"], [])

    def test_learning_detects_differently_worded_claim_conflict(self):
        learner = Learner(self.config, self.index)
        learner.remember(
            LearningCandidate("first", "value is one", detail="detail one", claim_key="value", claim_value=1), True
        )
        second = learner.remember(
            LearningCandidate("second", "value is two", detail="detail two", claim_key="value", claim_value=2), True
        )
        self.assertEqual(second["status"], "conflicted")

    def test_legacy_telemetry_survives_runtime_deletion(self):
        legacy = self.config.runtime_dir / "outcomes.jsonl"
        legacy.write_text(
            json.dumps(
                {
                    "task_id": "legacy",
                    "task_hash": "h",
                    "kb_mode": "kb",
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 190,
                }
            )
            + "\n"
        )
        store = TelemetryStore(self.config)
        legacy.unlink()
        imported = store.outcomes()
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["task_id"], "legacy")
        self.assertIsNone(imported[0]["total_tokens"])
        self.assertFalse(imported[0]["usage_complete"])
        self.assertEqual(len(TelemetryStore(self.config).outcomes()), 1)

    def test_receipt_requires_fresh_version_context(self):
        self.note(validity={"version_range": ">=2,<3"}, version_package="demo")
        result = self.retrieve(versions={"demo": "2.1"})
        self.assertTrue(result.items)
        server = MCPServer(self.config)
        args = {"retrieval_id": result.retrieval_id}
        self.assertFalse(server.call_tool("kb_receipt_check", args)["valid"])
        args["versions"] = {"demo": "2.1"}
        self.assertTrue(server.call_tool("kb_receipt_check", args)["valid"])

    def test_schema_enum_lists_are_reported_not_crashes(self):
        from autonomic_kb.validation import Validator

        self.note(schema_version=2, type=["fact"])
        report = Validator(self.config, self.index).validate()
        self.assertGreater(report.errors, 0)

    def test_index_enforces_packaged_schema_before_delivery(self):
        path = write_memory(
            self.config, "typed.md", "kb:repository:fact:typed", "Typed", "unittest build procedure", schema_version=2
        )
        self.assertTrue(self.retrieve().items)
        from autonomic_kb.markdown import dump_frontmatter, parse_markdown

        parsed = parse_markdown(path.read_text())
        parsed.metadata["summary"] = ["not", "a", "string"]
        path.write_text(dump_frontmatter(parsed.metadata) + parsed.body)
        self.assertEqual(self.retrieve().items, [])

    def test_cli_version_context_is_available(self):
        from autonomic_kb.cli import _versions, build_parser

        parser = build_parser()
        args = parser.parse_args(["retrieve", "task", "--version", "demo=2.1"])
        self.assertEqual(_versions(args.versions), {"demo": "2.1"})
        with self.assertRaises(ValueError):
            _versions(["bad-input"])

    def test_summary_is_not_an_executable_procedure(self):
        from autonomic_kb.sufficiency import assess_sufficiency

        context = TaskContext("run the test", task_types=["command"])
        note = {
            "type": "command",
            "l2": "python -m unittest",
            "metadata": {"preconditions": ["isolated environment"], "verification": "tests pass"},
            "delivered_text": "isolated environment. This is a recipe. tests pass",
        }
        self.assertEqual(assess_sufficiency(context, [note]), ("partial_context", ["steps"]))
        note["delivered_text"] += "\npython -m unittest"
        self.assertEqual(assess_sufficiency(context, [note]), ("sufficient_context", []))

    def test_runtime_symlinks_cannot_redirect_durable_writes(self):
        outside = self.root / "outside-state"
        outside.mkdir()
        import shutil

        shutil.rmtree(self.config.evidence_dir)
        self.config.evidence_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            EvidenceStore(self.config).put("observation", "safe content")
        self.assertEqual(list(outside.iterdir()), [])

    def test_remote_credentials_are_not_repository_identity(self):
        from autonomic_kb.git_context import canonical_remote

        result = canonical_remote("https://user:private-value@example.test/org/repo?credential=private-value")
        self.assertEqual(result, "https://example.test/org/repo")

    def test_native_yaml_dates_are_canonicalized(self):
        from autonomic_kb.markdown import parse_frontmatter

        metadata, _ = parse_frontmatter("---\nupdated: 2026-09-09\n---\nbody")
        self.assertEqual(metadata["updated"], "2026-09-09")
        path = self.note()
        text = path.read_text()
        import re

        path.write_text(re.sub(r"updated: .*", "updated: 2026-09-09", text))
        self.assertTrue(self.retrieve().items)

    def test_ambiguous_yaml_strings_round_trip(self):
        from autonomic_kb.markdown import dump_frontmatter, parse_frontmatter

        metadata = {"title": "off", "summary": "false", "id": "2026-09-09"}
        self.assertEqual(parse_frontmatter(dump_frontmatter(metadata))[0], metadata)

    def test_parser_upgrade_revalidates_unchanged_source(self):
        self.note()
        self.index.index_vault()
        self.index.connection.execute("UPDATE notes SET schema_valid=0")
        self.index.connection.execute("UPDATE meta SET value='obsolete' WHERE key='parser_format'")
        self.index.connection.commit()
        with KnowledgeIndex(self.config) as upgraded:
            upgraded.index_vault()
            self.assertTrue(upgraded.get("one")["schema_valid"])

    def test_mcp_candidates_can_bind_actual_source_dependencies(self):
        source = self.repo / "code.py"
        source.write_text("value = 1")
        result = MCPServer(self.config).call_tool(
            "kb_remember",
            {
                "title": "value source",
                "summary": "code declares the value",
                "confidence": 0.95,
                "provenance": [{"path": "code.py"}],
                "preconditions": ["repository checkout"],
                "verification": "inspect code.py",
            },
        )
        self.index.index_vault()
        note = self.index.get(result["memory_id"])
        self.assertEqual(note["metadata"]["dependencies"], [{"path": "code.py", "sha256": sha256_file(source)}])
        self.assertEqual(note["authority"], "agent")


if __name__ == "__main__":
    unittest.main()
