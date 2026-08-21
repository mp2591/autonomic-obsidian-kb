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
from .benchmark import BenchmarkRunner
from .config import KBConfig, initialize_vault
from .git_context import inspect_git
from .graph import render_graph
from .healing import Compactor, Healer, forget
from .index import KnowledgeIndex
from .learning import Learner, LearningCandidate
from .mcp_server import serve
from .obsidian import ObsidianBridge
from .retrieval import Retriever
from .util import stable_json, utc_now
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
        prog="kb",
        description="Token-economical autonomic knowledge infrastructure for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--vault", help="Obsidian vault root (or KB_VAULT)")
    parser.add_argument("--repo", help="Repository whose state validates and scopes memories")
    parser.add_argument("--agent", default=os.environ.get("KB_AGENT", "generic"), help="agent/tool identity")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize a vault for autonomic-kb")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--force", action="store_true")

    index = commands.add_parser("index", help="build or refresh the disposable local index")
    index.add_argument("--rebuild", action="store_true")

    context = commands.add_parser("context", help="return compact repository orientation")
    context.add_argument("--budget", type=int)

    retrieve = commands.add_parser("retrieve", help="retrieve minimal sufficient context for a task")
    retrieve.add_argument("task")
    retrieve.add_argument("--budget", type=int)
    retrieve.add_argument("--depth", choices=["auto", "0", "1", "2", "3"], default="auto")
    retrieve.add_argument("--path", action="append", default=[], dest="paths")
    retrieve.add_argument("--session", default="")
    retrieve.add_argument("--include-uncertain", action="store_true")
    retrieve.add_argument("--explain-exclusions", action="store_true")

    remember = commands.add_parser("remember", help="submit a candidate memory through promotion/security gates")
    remember.add_argument("--title", required=True)
    remember.add_argument("--summary", required=True)
    remember.add_argument("--detail", default="")
    remember.add_argument("--type", default="fact", dest="memory_type")
    remember.add_argument("--scope", default="repository")
    remember.add_argument("--confidence", type=float, default=0.7)
    remember.add_argument("--authority", default="agent")
    remember.add_argument("--applies-to", action="append", default=[])
    remember.add_argument("--source", action="append", default=[])
    remember.add_argument("--claim-key", default="")
    remember.add_argument("--claim-value", default=None)
    remember.add_argument("--force", action="store_true", help="promote despite low economics score; security gate remains")

    learn = commands.add_parser("learn", help="ingest candidate memories from structured output or Git state")
    source = learn.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="JSON or JSONL candidate file")
    source.add_argument("--git", action="store_true", help="derive a short-lived candidate from current Git state")

    validate = commands.add_parser("validate", help="validate schema, sources, links, contradictions, and safety")

    heal = commands.add_parser("heal", help="plan or apply conservative self-healing actions")
    heal.add_argument("--apply", action="store_true")

    compact = commands.add_parser("compact", help="plan or apply archive/duplicate compaction")
    compact.add_argument("--apply", action="store_true")

    status = commands.add_parser("status", help="show index, vault, Git, and Obsidian status")

    stats = commands.add_parser("stats", help="show token and retrieval telemetry")

    graph = commands.add_parser("graph", help="export retrieval-relevant knowledge relationships")
    graph.add_argument("--format", choices=["json", "dot", "mermaid"], default="json")
    graph.add_argument("--include-archived", action="store_true")
    graph.add_argument("--output")

    scope = commands.add_parser("scope", help="explain active scope inputs")
    scope.add_argument("--path", action="append", default=[], dest="paths")

    why = commands.add_parser("why", help="explain why a memory was included or excluded")
    why.add_argument("id")

    forget_parser = commands.add_parser("forget", help="archive a memory; hard deletion must be explicit")
    forget_parser.add_argument("id")
    forget_parser.add_argument("--hard", action="store_true")
    forget_parser.add_argument("--yes", action="store_true")

    doctor = commands.add_parser("doctor", help="diagnose vault, index, Git, and optional integrations")

    benchmark = commands.add_parser("benchmark", help="compare proxy no-KB and KB-assisted token cost")
    benchmark.add_argument("--tasks", default="benchmarks/tasks.json")
    benchmark.add_argument("--output")

    obsidian = commands.add_parser("obsidian", help="probe the optional first-party Obsidian CLI bridge")
    obsidian.add_argument("--capabilities", action="store_true")

    mcp = commands.add_parser("mcp", help="serve MCP tools over newline-delimited JSON-RPC on stdio")

    return parser


def _normalize_global_options(argv: list[str]) -> list[str]:
    """Allow global options before or after the subcommand for shell-agent ergonomics."""
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
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_global_options(raw))
    try:
        if args.command == "init":
            config = initialize_vault(args.path, args.force)
            created = _create_starter_note(config)
            with KnowledgeIndex(config) as index:
                stats = index.rebuild()
            _emit({"vault": str(config.vault), "config": str(config.config_path), "starter_note": created, "index": stats.to_dict()}, args.json)
            return 0

        config = _config(args)
        if args.command == "index":
            with KnowledgeIndex(config) as index:
                result = index.rebuild() if args.rebuild else index.index_vault()
            _emit(result, args.json)
            return 0
        if args.command == "context":
            retriever = Retriever(config)
            try:
                result = retriever.context(args.budget, args.agent)
            finally:
                retriever.close()
            _emit(result, args.json)
            return 0
        if args.command == "retrieve":
            retriever = Retriever(config)
            try:
                result = retriever.retrieve(
                    args.task, args.budget, args.depth, args.paths, args.agent, args.session,
                    args.include_uncertain,
                )
            finally:
                retriever.close()
            if args.explain_exclusions and not args.json:
                print(result.to_markdown().rstrip())
                print("\n# Exclusions")
                for item in result.excluded:
                    print(f"- `{item['id']}`: {item['reason']}")
            else:
                _emit(result, args.json)
            return 0
        if args.command == "remember":
            provenance = [{"kind": "cli", "value": source} for source in args.source]
            candidate = LearningCandidate(
                title=args.title, summary=args.summary, detail=args.detail,
                memory_type=args.memory_type, scope=args.scope, confidence=args.confidence,
                authority=args.authority, provenance=provenance, applies_to=args.applies_to,
                claim_key=args.claim_key, claim_value=args.claim_value,
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
        if args.command == "validate":
            validator = Validator(config)
            try:
                report = validator.validate()
            finally:
                validator.close()
            _emit(report, args.json)
            return 1 if report.errors else 0
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
        if args.command in {"status", "stats"}:
            result = _status(config) if args.command == "status" else _stats(config)
            _emit(result, args.json)
            return 0
        if args.command == "graph":
            with KnowledgeIndex(config) as index:
                index.index_vault()
                rendered = render_graph(index, args.format, args.include_archived)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(rendered + "\n", encoding="utf-8")
                _emit({"output": str(output), "format": args.format}, args.json)
            else:
                _emit(rendered if not args.json or args.format != "json" else json.loads(rendered), args.json)
            return 0
        if args.command == "scope":
            retriever = Retriever(config)
            try:
                context = retriever.build_context("scope inspection", args.paths, args.agent)
            finally:
                retriever.close()
            _emit(asdict(context), args.json)
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
            result = BenchmarkRunner(config).run(args.tasks)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            _emit(result, args.json)
            return 0
        if args.command == "obsidian":
            bridge = ObsidianBridge(config.vault)
            _emit(bridge.capabilities() if args.capabilities else bridge.status(), args.json)
            return 0
        if args.command == "mcp":
            return serve(config)
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
        "---\n"
        "id: kb:repository:repository-map:knowledge-base\n"
        "title: Knowledge base map\n"
        "type: repository-map\n"
        "scope: repository\n"
        "status: active\n"
        "summary: Entry point for the smallest useful project context.\n"
        "confidence: 0.8\n"
        "authority: user-corrected\n"
        f"created: {now}\nupdated: {now}\n"
        "freshness: unvalidated\n"
        "token_cost: 70\nutility: 0.8\n"
        "applies_to: []\nprovenance: []\nrelations: {}\ninvalidation: {}\n"
        "---\n\n"
        "## L0 — Pointer\n\nProject knowledge entry point.\n\n"
        "## L1 — Fact\n\nAdd only durable facts that are cheaper to retrieve than rediscover.\n\n"
        "## L2 — Summary\n\nUse `kb remember`, validate before promotion, and retrieve under an explicit token budget.\n",
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
    stats["recent_retrievals"] = len(retrievals)
    stats["recent_selected_items"] = selected
    stats["recent_context_tokens"] = used
    stats["mean_items_per_retrieval"] = round(selected / max(1, len(retrievals)), 3)
    stats["mean_context_tokens"] = round(used / max(1, len(retrievals)), 3)
    return stats


def _doctor(config: KBConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append({"name": "python", "ok": sys.version_info >= (3, 11), "detail": platform.python_version()})
    checks.append({"name": "vault", "ok": config.vault.exists(), "detail": str(config.vault)})
    checks.append({"name": "vault-writable", "ok": os.access(config.vault, os.W_OK), "detail": str(config.vault)})
    checks.append({"name": "config", "ok": config.config_path.exists(), "detail": str(config.config_path)})
    checks.append({"name": "git", "ok": shutil.which("git") is not None, "detail": shutil.which("git") or "not found"})
    try:
        with KnowledgeIndex(config) as index:
            index.index_vault()
            checks.append({"name": "sqlite", "ok": True, "detail": sqlite3.sqlite_version})
            checks.append({"name": "fts5", "ok": index.fts5, "detail": "enabled" if index.fts5 else "LIKE fallback"})
    except sqlite3.Error as error:
        checks.append({"name": "sqlite", "ok": False, "detail": str(error)})
    obsidian = ObsidianBridge(config.vault).status()
    checks.append({
        "name": "obsidian-cli-optional", "ok": True,
        "detail": "responsive" if obsidian.responsive else f"not active ({obsidian.error}); filesystem mode remains functional",
    })
    return {"healthy": all(check["ok"] for check in checks if check["name"] != "fts5"), "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
