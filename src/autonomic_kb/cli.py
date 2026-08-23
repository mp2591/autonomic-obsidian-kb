from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .benchmark import BenchmarkRunner, TraceBenchmarkRunner
from .config import KBConfig, initialize_vault
from .dashboard import dashboard_data
from .evidence import EvidenceStore
from .git_context import inspect_git
from .graph import personalized_pagerank, render_graph
from .healing import Compactor, Healer, forget
from .index import KnowledgeIndex
from .learning import Learner, LearningCandidate
from .leases import LeaseStore
from .mcp_server import serve
from .migrations import migrate_vault
from .obsidian import ObsidianBridge
from .retrieval import Retriever
from .shadow import ShadowEvaluator
from .telemetry import TaskOutcome, TelemetryStore
from .util import sha256_text, utc_now
from .validation import Validator


def _emit(value: Any, json_output: bool = False) -> None:
    if json_output:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    elif isinstance(value, str):
        print(value.rstrip())
    elif hasattr(value, "to_markdown"):
        print(value.to_markdown().rstrip())
    elif hasattr(value, "to_dict"):
        print(json.dumps(value.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _config(args: argparse.Namespace) -> KBConfig:
    return KBConfig.load(args.vault, args.repo)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb", description="Token-economical autonomic knowledge infrastructure for AI agents."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--vault", help="Obsidian vault root (or KB_VAULT)")
    parser.add_argument("--repo", help="Repository whose state validates and scopes memories")
    parser.add_argument("--agent", default=os.environ.get("KB_AGENT", "generic"), help="agent/tool identity")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--force", action="store_true")
    index = commands.add_parser("index")
    index.add_argument("--rebuild", action="store_true")
    context = commands.add_parser("context")
    context.add_argument("--budget", type=int)
    retrieve = commands.add_parser("retrieve")
    retrieve.add_argument("task")
    retrieve.add_argument("--budget", type=int)
    retrieve.add_argument("--depth", choices=["auto", "0", "1", "2", "3"], default="auto")
    retrieve.add_argument("--path", action="append", default=[], dest="paths")
    retrieve.add_argument("--session", default="")
    retrieve.add_argument("--include-uncertain", action="store_true")
    retrieve.add_argument("--route")
    retrieve.add_argument("--explain-exclusions", action="store_true")

    remember = commands.add_parser("remember")
    for name, kwargs in [
        ("--title", {"required": True}),
        ("--summary", {"required": True}),
        ("--detail", {"default": ""}),
        ("--type", {"default": "fact", "dest": "memory_type"}),
        ("--scope", {"default": "repository"}),
        ("--authority", {"default": "agent"}),
        ("--taint", {"default": "agent"}),
        ("--claim-key", {"default": ""}),
        ("--claim-value", {"default": None}),
        ("--valid-from", {"default": ""}),
        ("--valid-to", {"default": ""}),
        ("--version-range", {"default": ""}),
    ]:
        remember.add_argument(name, **kwargs)
    remember.add_argument("--confidence", type=float, default=0.7)
    remember.add_argument("--applies-to", action="append", default=[])
    remember.add_argument("--source", action="append", default=[])
    remember.add_argument("--evidence", action="append", default=[])
    remember.add_argument("--authorize-instruction", action="store_true")
    remember.add_argument("--force", action="store_true")

    learn = commands.add_parser("learn")
    group = learn.add_mutually_exclusive_group(required=True)
    group.add_argument("--file")
    group.add_argument("--git", action="store_true")
    episode = commands.add_parser("episode")
    episode.add_argument("--task", required=True)
    episode.add_argument("--file", action="append", default=[])
    episode.add_argument("--command-used", action="append", default=[])
    episode.add_argument("--observation", action="append", default=[])
    episode.add_argument("--failed", action="append", default=[])
    episode.add_argument("--success-action", action="append", default=[])
    episode.add_argument("--outcome", default="unknown")
    episode.add_argument("--correction", default="")
    consolidate = commands.add_parser("consolidate")
    consolidate.add_argument("episode_id")
    consolidate.add_argument("--force", action="store_true")

    commands.add_parser("validate")
    queue = commands.add_parser("validation-queue")
    queue.add_argument("--limit", type=int, default=20)
    heal = commands.add_parser("heal")
    heal.add_argument("--apply", action="store_true")
    compact = commands.add_parser("compact")
    compact.add_argument("--apply", action="store_true")
    commands.add_parser("status")
    commands.add_parser("stats")
    commands.add_parser("dashboard")
    graph = commands.add_parser("graph")
    graph.add_argument("--format", choices=["json", "dot", "mermaid"], default="json")
    graph.add_argument("--include-archived", action="store_true")
    graph.add_argument("--output")
    ppr = commands.add_parser("graph-rank")
    ppr.add_argument("seed", nargs="+")
    scope = commands.add_parser("scope")
    scope.add_argument("--path", action="append", default=[], dest="paths")
    why = commands.add_parser("why")
    why.add_argument("id")
    forget_parser = commands.add_parser("forget")
    forget_parser.add_argument("id")
    forget_parser.add_argument("--hard", action="store_true")
    forget_parser.add_argument("--yes", action="store_true")
    commands.add_parser("doctor")

    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--tasks", default="benchmarks/tasks.json")
    benchmark.add_argument("--output")
    benchmark.add_argument("--traces", action="store_true")
    obsidian = commands.add_parser("obsidian")
    obsidian.add_argument("--capabilities", action="store_true")
    commands.add_parser("mcp")

    evidence = commands.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_add = evidence_sub.add_parser("add")
    evidence_add.add_argument("--kind", required=True)
    evidence_add.add_argument("--content", required=True)
    evidence_add.add_argument("--subject", default="")
    evidence_add.add_argument("--path", default="")
    evidence_get = evidence_sub.add_parser("get")
    evidence_get.add_argument("id")
    evidence_sub.add_parser("list")

    feedback = commands.add_parser("feedback")
    feedback.add_argument("retrieval_id")
    feedback.add_argument("memory_id")
    feedback.add_argument("kind")
    feedback.add_argument("--notes", default="")
    outcome = commands.add_parser("outcome")
    outcome.add_argument("--task", required=True)
    outcome.add_argument("--mode", choices=["kb", "no-kb"], required=True)
    outcome.add_argument("--success", action="store_true")
    outcome.add_argument("--input-tokens", type=int, default=0)
    outcome.add_argument("--output-tokens", type=int, default=0)
    outcome.add_argument("--cached-tokens", type=int, default=0)
    outcome.add_argument("--searches", type=int, default=0)
    outcome.add_argument("--file-reads", type=int, default=0)
    outcome.add_argument("--commands", type=int, default=0)
    outcome.add_argument("--retries", type=int, default=0)
    outcome.add_argument("--corrections", type=int, default=0)
    outcome.add_argument("--latency-ms", type=float, default=0)
    outcome.add_argument("--maintenance-tokens", type=int, default=0)
    outcome.add_argument("--unsafe", action="store_true")
    outcome.add_argument("--retrieved", action="append", default=[])

    lease = commands.add_parser("lease")
    lease_sub = lease.add_subparsers(dest="lease_command", required=True)
    la = lease_sub.add_parser("acquire")
    la.add_argument("task")
    la.add_argument("--ttl", type=int, default=30)
    lr = lease_sub.add_parser("release")
    lr.add_argument("task")
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--apply", action="store_true")
    shadow = commands.add_parser("shadow")
    shadow.add_argument("task")
    shadow.add_argument("--route", action="append", required=True)
    shadow.add_argument("--budget", type=int, default=600)
    shadow.add_argument("--path", action="append", default=[])
    return parser


def _normalize_global_options(argv: list[str]) -> list[str]:
    flags = {"--json"}
    valued = {"--vault", "--repo", "--agent"}
    front: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in flags:
            front.append(value)
            index += 1
        elif value in valued and index + 1 < len(argv):
            front.extend([value, argv[index + 1]])
            index += 2
        else:
            rest.append(value)
            index += 1
    return front + rest


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_global_options(list(sys.argv[1:] if argv is None else argv)))
    try:
        if args.command == "init":
            config = initialize_vault(args.path, args.force)
            created = _create_starter_note(config)
            with KnowledgeIndex(config) as index:
                stats = index.rebuild()
            _emit(
                {
                    "vault": str(config.vault),
                    "config": str(config.config_path),
                    "starter_note": created,
                    "index": stats.to_dict(),
                },
                args.json,
            )
            return 0
        config = _config(args)
        if args.command == "index":
            with KnowledgeIndex(config) as index:
                result = index.rebuild() if args.rebuild else index.index_vault()
            _emit(result, args.json)
            return 0
        if args.command == "context":
            r = Retriever(config)
            try:
                result = r.context(args.budget, args.agent)
            finally:
                r.close()
            _emit(result, args.json)
            return 0
        if args.command == "retrieve":
            r = Retriever(config)
            try:
                result = r.retrieve(
                    args.task,
                    args.budget,
                    args.depth,
                    args.paths,
                    args.agent,
                    args.session,
                    args.include_uncertain,
                    args.route,
                )
            finally:
                r.close()
            if args.explain_exclusions and not args.json:
                print(result.to_markdown().rstrip())
                print("\n# Exclusions")
                for item in result.excluded:
                    print(f"- `{item['id']}`: {item['reason']}")
            else:
                _emit(result, args.json)
            return 0
        if args.command == "remember":
            candidate = LearningCandidate(
                title=args.title,
                summary=args.summary,
                detail=args.detail,
                memory_type=args.memory_type,
                scope=args.scope,
                confidence=args.confidence,
                authority=args.authority,
                taint=args.taint,
                authorized_instruction=args.authorize_instruction,
                provenance=[{"kind": "cli", "value": source} for source in args.source],
                evidence=args.evidence,
                applies_to=args.applies_to,
                claim_key=args.claim_key,
                claim_value=args.claim_value,
                valid_from=args.valid_from,
                valid_to=args.valid_to,
                version_range=args.version_range,
            )
            learner = Learner(config)
            try:
                result = learner.remember(candidate, force=args.force)
            finally:
                learner.close()
            _emit(result, args.json)
            return 0
        if args.command == "learn":
            learner = Learner(config)
            try:
                result = learner.learn_json(args.file) if args.file else learner.learn_git()
            finally:
                learner.close()
            _emit(result, args.json)
            return 0
        if args.command == "episode":
            learner = Learner(config)
            try:
                result = learner.capture_episode(
                    args.task,
                    files=args.file,
                    commands=args.command_used,
                    observations=args.observation,
                    failed_hypotheses=args.failed,
                    successful_actions=args.success_action,
                    outcome=args.outcome,
                    correction=args.correction,
                )
            finally:
                learner.close()
            _emit(result, args.json)
            return 0
        if args.command == "consolidate":
            learner = Learner(config)
            try:
                episode = next((ep for ep in learner.episodes.all() if ep.episode_id == args.episode_id), None)
                if not episode:
                    raise ValueError(f"episode not found: {args.episode_id}")
                result = learner.consolidate_episode(episode, force=args.force)
            finally:
                learner.close()
            _emit(result, args.json)
            return 0
        if args.command == "validate":
            validator = Validator(config)
            try:
                report = validator.validate()
            finally:
                validator.close()
            _emit(report, args.json)
            return 1 if report.errors else 0
        if args.command == "validation-queue":
            validator = Validator(config)
            try:
                result = validator.validation_priorities()[: args.limit]
            finally:
                validator.close()
            _emit(result, args.json)
            return 0
        if args.command == "heal":
            healer = Healer(config)
            try:
                result = healer.heal(args.apply)
            finally:
                healer.close()
            _emit(result, args.json)
            return 0
        if args.command == "compact":
            compactor = Compactor(config)
            try:
                result = compactor.compact(args.apply)
            finally:
                compactor.close()
            _emit(result, args.json)
            return 0
        if args.command == "status":
            _emit(_status(config), args.json)
            return 0
        if args.command == "stats":
            _emit(_stats(config), args.json)
            return 0
        if args.command == "dashboard":
            _emit(dashboard_data(config), args.json)
            return 0
        if args.command == "graph":
            with KnowledgeIndex(config) as index:
                index.index_vault()
                rendered = render_graph(index, args.format, args.include_archived)
            if args.output:
                Path(args.output).write_text(rendered + "\n", encoding="utf-8")
                _emit({"output": args.output, "format": args.format}, args.json)
            else:
                _emit(rendered if not args.json or args.format != "json" else json.loads(rendered), args.json)
            return 0
        if args.command == "graph-rank":
            with KnowledgeIndex(config) as index:
                index.index_vault()
                _emit(personalized_pagerank(index, args.seed), args.json)
            return 0
        if args.command == "scope":
            r = Retriever(config)
            try:
                result = r.build_context("scope inspection", args.paths, args.agent)
            finally:
                r.close()
            _emit(asdict(result), args.json)
            return 0
        if args.command == "why":
            with KnowledgeIndex(config) as index:
                index.index_vault()
                result = index.why(args.id)
            _emit(result, args.json)
            return 0 if result["memory"] else 1
        if args.command == "forget":
            if args.hard and not args.yes:
                parser.error("hard deletion requires --hard --yes")
            _emit(forget(config, args.id, args.hard), args.json)
            return 0
        if args.command == "doctor":
            _emit(_doctor(config), args.json)
            return 0
        if args.command == "benchmark":
            result = TraceBenchmarkRunner(config).run() if args.traces else BenchmarkRunner(config).run(args.tasks)
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            _emit(result, args.json)
            return 0
        if args.command == "obsidian":
            bridge = ObsidianBridge(config.vault)
            _emit(bridge.capabilities() if args.capabilities else bridge.status(), args.json)
            return 0
        if args.command == "mcp":
            return serve(config)
        if args.command == "evidence":
            store = EvidenceStore(config)
            if args.evidence_command == "add":
                git = inspect_git(config.repo or config.vault)
                result = store.put(
                    args.kind,
                    args.content,
                    subject=args.subject,
                    repository_id=git.repository_id,
                    commit=git.head,
                    path=args.path,
                    producer=args.agent,
                ).to_dict()
            elif args.evidence_command == "get":
                record = store.get(args.id)
                result = record.to_dict() if record else {"error": "not-found"}
            else:
                result = {"ids": store.list_ids()}
            _emit(result, args.json)
            return 0
        if args.command == "feedback":
            _emit(
                TelemetryStore(config).feedback(
                    args.retrieval_id, args.memory_id, args.kind, actor=args.agent, notes=args.notes
                ),
                args.json,
            )
            return 0
        if args.command == "outcome":
            task_hash = sha256_text(args.task)[:16]
            result = TelemetryStore(config).record_outcome(
                TaskOutcome(
                    task_id=f"task:{task_hash}:{utc_now()}",
                    task_hash=task_hash,
                    kb_mode=args.mode,
                    success=args.success,
                    input_tokens=args.input_tokens,
                    output_tokens=args.output_tokens,
                    cached_tokens=args.cached_tokens,
                    searches=args.searches,
                    file_reads=args.file_reads,
                    commands=args.commands,
                    retries=args.retries,
                    corrections=args.corrections,
                    latency_ms=args.latency_ms,
                    maintenance_tokens=args.maintenance_tokens,
                    unsafe=args.unsafe,
                    retrieved_ids=args.retrieved,
                )
            )
            _emit(result, args.json)
            return 0
        if args.command == "lease":
            store = LeaseStore(config)
            result = (
                store.acquire(args.task, args.agent, ttl_minutes=args.ttl).to_dict()
                if args.lease_command == "acquire"
                else {"released": store.release(args.task, args.agent)}
            )
            _emit(result, args.json)
            return 0
        if args.command == "migrate":
            _emit(migrate_vault(config, args.apply), args.json)
            return 0
        if args.command == "shadow":
            _emit(ShadowEvaluator(config).compare(args.task, args.route, args.budget, args.path), args.json)
            return 0
    except (FileNotFoundError, FileExistsError, ValueError, sqlite3.Error) as error:
        if args.json:
            _emit({"error": type(error).__name__, "message": str(error)}, True)
        else:
            print(f"kb: {error}", file=sys.stderr)
        return 2
    return 0


def _create_starter_note(config: KBConfig) -> str:
    path = config.vault / "00-index" / "knowledge-base.md"
    if path.exists():
        return path.relative_to(config.vault).as_posix()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    path.write_text(
        "---\nschema_version: 2\nid: kb:repository:repository-map:knowledge-base\ntitle: Knowledge base map\n"
        "kind: summary\ntype: repository-map\nscope: repository\nstatus: active\n"
        "summary: Entry point for the smallest useful project context.\n"
        "confidence: 0.8\nauthority: user-corrected\ntaint: trusted\n"
        f"created: {now}\nupdated: {now}\nfreshness: unvalidated\n"
        'validity: {"valid_from":"","valid_to":"","as_of_commit":"","version_range":""}\n'
        "token_cost: 70\nutility: 0.8\napplies_to: []\nprovenance: []\nevidence: []\n"
        "validators: []\nrelations: {}\ninvalidation: {}\n---\n\n"
        "## L0 — Pointer\n\nProject knowledge entry point.\n\n## L1 — Fact\n\n"
        "Add only durable facts that are cheaper to retrieve than rediscover.\n\n"
        "## L2 — Summary\n\nUse `kb remember`, retain evidence, validate before promotion, "
        "and retrieve under an explicit token budget.\n",
        encoding="utf-8",
    )
    return path.relative_to(config.vault).as_posix()


def _status(config: KBConfig) -> dict[str, Any]:
    git = inspect_git(config.repo or config.vault)
    with KnowledgeIndex(config) as index:
        indexed = index.index_vault()
        stats = index.stats()
    return {
        "version": __version__,
        "vault": str(config.vault),
        "repo": str(config.repo or ""),
        "git": git.to_dict(),
        "index_refresh": indexed.to_dict(),
        "index": stats,
        "obsidian": ObsidianBridge(config.vault).status().to_dict(),
    }


def _stats(config: KBConfig) -> dict[str, Any]:
    with KnowledgeIndex(config) as index:
        index.index_vault()
        stats = index.stats()
        recent = index.events.tail(100)
    retrievals = [event for event in recent if event.get("event") == "retrieve.completed"]
    selected = sum(len(event.get("selected", [])) for event in retrievals)
    used = sum(int(event.get("used_tokens", 0)) for event in retrievals)
    stats.update(
        {
            "recent_retrievals": len(retrievals),
            "recent_selected_items": selected,
            "recent_context_tokens": used,
            "mean_items_per_retrieval": round(selected / max(1, len(retrievals)), 3),
            "mean_context_tokens": round(used / max(1, len(retrievals)), 3),
            "paired_outcomes": TelemetryStore(config).paired_summary(),
        }
    )
    return stats


def _doctor(config: KBConfig) -> dict[str, Any]:
    checks = []
    checks.append({"check": "vault", "ok": config.vault.exists(), "detail": str(config.vault)})
    try:
        with KnowledgeIndex(config) as index:
            index.index_vault()
            integrity = index.connection.execute("PRAGMA integrity_check").fetchone()[0]
            checks.append({"check": "sqlite", "ok": integrity == "ok", "detail": integrity})
    except sqlite3.Error as error:
        checks.append({"check": "sqlite", "ok": False, "detail": str(error)})
    try:
        import jsonschema as _jsonschema  # noqa: F401

        checks.append({"check": "jsonschema", "ok": True, "detail": "Draft 2020-12 validation available"})
    except ImportError:
        checks.append(
            {"check": "jsonschema", "ok": False, "detail": "install project dependencies for full schema validation"}
        )
    obsidian = ObsidianBridge(config.vault).status()
    checks.append(
        {
            "check": "obsidian",
            "ok": obsidian.vault_responsive,
            "optional": True,
            "detail": obsidian.error or obsidian.version,
        }
    )
    evidence = EvidenceStore(config)
    ids = evidence.list_ids()
    checks.append(
        {"check": "evidence", "ok": all(evidence.verify(value) for value in ids), "detail": f"{len(ids)} objects"}
    )
    return {
        "ok": all(item["ok"] for item in checks if not item.get("optional")),
        "checks": checks,
        "python": platform.python_version(),
        "git": bool(shutil.which("git")),
    }
