from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from .models import TaskContext
from .security import trust_gate
from .util import age_days, terms

TYPE_TERMS = {
    "architecture": {"architecture", "design", "component", "boundary", "structure"},
    "repository-map": {"repository", "repo", "where", "file", "module", "layout"},
    "command": {"build", "test", "run", "deploy", "command", "install", "lint"},
    "workflow": {"workflow", "process", "release", "deploy", "review", "ci"},
    "known-failure": {"error", "failure", "failed", "bug", "debug", "crash", "exception"},
    "solution": {"fix", "solve", "workaround", "repair", "debug"},
    "dependency": {"dependency", "package", "library", "version", "upgrade"},
    "api": {"api", "endpoint", "request", "response", "interface", "schema"},
    "convention": {"convention", "style", "pattern", "naming", "format"},
    "decision": {"decision", "why", "tradeoff", "adr"},
    "invariant": {"invariant", "must", "never", "guarantee", "constraint"},
    "environment": {"environment", "shell", "os", "path", "credential", "quirk"},
    "agent-instruction": {"agent", "codex", "claude", "gemini", "instruction"},
}

AUTHORITY = {
    "source-of-truth": 1.0,
    "authoritative": 0.95,
    "verified": 0.9,
    "user-corrected": 0.9,
    "derived": 0.68,
    "agent": 0.52,
    "external": 0.48,
    "untrusted": 0.1,
}


def classify_task(task: str) -> list[str]:
    task_terms = set(terms(task))
    scored = []
    for memory_type, clues in TYPE_TERMS.items():
        overlap = len(task_terms & clues)
        if overlap:
            scored.append((overlap, memory_type))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [memory_type for _, memory_type in scored[:5]] or ["fact", "repository-map"]


def _matches_path(pattern: str, paths: list[str]) -> bool:
    normalized = pattern.replace("\\", "/").lstrip("./")
    return any(
        fnmatch.fnmatch(path.replace("\\", "/").lstrip("./"), normalized)
        or path.replace("\\", "/").lstrip("./").startswith(normalized.rstrip("*/"))
        for path in paths
    )


def scope_gate(note: dict[str, Any], context: TaskContext, allow_cross_repo: bool = False) -> tuple[bool, str, float]:
    scope = note.get("scope", "repository")
    metadata = note.get("metadata", {})
    note_repo = str(note.get("repo") or metadata.get("repo") or "")
    note_project = str(note.get("project") or metadata.get("project") or "")
    note_module = str(note.get("module") or metadata.get("module") or "")
    note_branch = str(note.get("branch") or metadata.get("branch") or "")
    if scope == "global":
        return True, "global scope", 0.55
    if scope == "user":
        return True, "user scope", 0.62
    if note_repo and context.repo and note_repo not in {context.repo, Path(context.repo).name}:
        if not allow_cross_repo:
            return False, f"repo scope mismatch ({note_repo} != {context.repo})", 0.0
    if scope in {"repository", "project"}:
        if scope == "project" and note_project and context.project and note_project != context.project:
            return False, "project scope mismatch", 0.0
        return True, f"{scope} scope matched", 0.82 if scope == "repository" else 0.78
    active_paths = context.requested_paths + context.changed_paths
    applies = metadata.get("applies_to", [])
    if isinstance(applies, str):
        applies = [applies]
    if scope == "module":
        if note_module and context.module and note_module == context.module:
            return True, "module matched", 1.0
        if active_paths and any(_matches_path(str(pattern), active_paths) for pattern in applies):
            return True, "applicable path matched", 0.98
        return False, "module memory has no active-path match", 0.0
    if scope == "branch":
        if note_branch and note_branch == context.branch:
            return True, "branch matched", 0.98
        return False, f"branch mismatch ({note_branch or 'unset'} != {context.branch or 'unset'})", 0.0
    if scope in {"task", "session"}:
        target = str(metadata.get(scope, ""))
        current = context.task_hash if scope == "task" else context.session
        if target and current and target == current:
            return True, f"{scope} matched", 1.0
        return False, f"{scope} memory is not active", 0.0
    return False, f"unknown scope {scope}", 0.0


def score_note(note: dict[str, Any], context: TaskContext, allow_untrusted: bool, allow_cross_repo: bool) -> tuple[float, list[str], str | None]:
    accepted, trust_reason = trust_gate(
        str(note.get("status", "active")), str(note.get("authority", "agent")),
        float(note.get("confidence", 0.5)), allow_untrusted,
    )
    if not accepted:
        return 0.0, [], trust_reason
    scope_ok, scope_reason, scope_score = scope_gate(note, context, allow_cross_repo)
    if not scope_ok:
        return 0.0, [], scope_reason
    task_types = set(context.task_types)
    type_score = 1.0 if note.get("type") in task_types else 0.25
    active_paths = context.requested_paths + context.changed_paths
    metadata = note.get("metadata", {})
    applies = metadata.get("applies_to", [])
    if isinstance(applies, str):
        applies = [applies]
    path_score = 0.0
    matched_pattern = ""
    for pattern in applies:
        if active_paths and _matches_path(str(pattern), active_paths):
            path_score = 1.0
            matched_pattern = str(pattern)
            break
    if not path_score and active_paths:
        note_path = str(note.get("path", ""))
        if any(Path(path).stem in note_path for path in active_paths):
            path_score = 0.65
    confidence = max(0.0, min(1.0, float(note.get("confidence", 0.5))))
    authority = AUTHORITY.get(str(note.get("authority", "agent")), 0.45)
    validation = str(metadata.get("validation", note.get("freshness", "unknown")))
    validation_score = {"verified": 1.0, "valid": 0.9, "fresh": 0.9, "unknown": 0.45, "stale": 0.1}.get(validation, 0.45)
    days = age_days(str(note.get("validated") or note.get("updated") or note.get("created") or ""))
    freshness = max(0.0, 1.0 - min(days, 365.0) / 365.0)
    utility = max(0.0, min(1.0, float(note.get("utility", 0.5))))
    lexical = max(0.0, min(1.0, float(note.get("lexical", 0.0))))
    score = (
        0.30 * lexical + 0.10 * type_score + 0.17 * scope_score + 0.12 * path_score
        + 0.10 * confidence + 0.08 * authority + 0.06 * validation_score
        + 0.04 * freshness + 0.03 * utility
    )
    if note.get("graph_relation"):
        score += 0.045
    if note.get("status") == "stale":
        score -= 0.24
    reasons = [f"lexical={lexical:.2f}", scope_reason, trust_reason]
    if note.get("type") in task_types:
        reasons.append(f"task type matched {note.get('type')}")
    if matched_pattern:
        reasons.append(f"path matched {matched_pattern}")
    if note.get("graph_relation"):
        reasons.append(f"graph {note['graph_relation']} from {note.get('graph_source')}")
    reasons.append(f"confidence={confidence:.2f}; authority={note.get('authority')}")
    return max(0.0, min(1.0, score)), reasons, None
