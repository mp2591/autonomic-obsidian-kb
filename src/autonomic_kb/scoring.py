from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from .git_context import is_ancestor
from .models import TaskContext
from .security import trust_gate
from .util import age_days, is_time_active, jaccard, terms

POLICY_VERSION = "rank-v2.1"
TYPE_TERMS = {
    "architecture": {"architecture", "design", "component", "boundary", "structure", "why"},
    "repository-map": {"repository", "repo", "where", "file", "module", "layout", "orient"},
    "command": {"build", "test", "run", "deploy", "command", "install", "lint", "verify"},
    "workflow": {"workflow", "process", "release", "deploy", "review", "ci"},
    "known-failure": {"error", "failure", "failed", "bug", "debug", "crash", "exception", "locked"},
    "solution": {"fix", "solve", "workaround", "repair", "debug"},
    "dependency": {"dependency", "package", "library", "version", "upgrade"},
    "api": {"api", "endpoint", "request", "response", "interface", "schema"},
    "convention": {"convention", "style", "pattern", "naming", "format"},
    "decision": {"decision", "why", "tradeoff", "adr", "rationale"},
    "invariant": {"invariant", "must", "never", "guarantee", "constraint"},
    "environment": {"environment", "shell", "os", "path", "credential", "quirk"},
    "agent-instruction": {"agent", "codex", "claude", "gemini", "instruction"},
    "negative-result": {"failed", "didn't", "doesn't", "avoid", "dead end", "negative"},
}

AUTHORITY = {
    "source-of-truth": 1.0, "authoritative": 0.95, "verified": 0.9, "user-corrected": 0.95,
    "derived": 0.68, "agent": 0.52, "external": 0.45, "untrusted": 0.08,
}


def classify_task(task: str) -> list[str]:
    task_terms = set(terms(task))
    scored: list[tuple[int, str]] = []
    for memory_type, clues in TYPE_TERMS.items():
        overlap = len(task_terms & clues)
        if overlap:
            scored.append((overlap, memory_type))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [memory_type for _, memory_type in scored[:6]] or ["fact", "repository-map"]


def classify_risk(task: str) -> str:
    lower = task.lower()
    if re.search(r"\b(delete|deploy|release|production|credential|secret|security|migration|payment|database drop)\b", lower):
        return "high"
    if re.search(r"\b(write|modify|change|fix|install|upgrade|execute)\b", lower):
        return "elevated"
    return "normal"


def _matches_path(pattern: str, paths: list[str]) -> bool:
    normalized = pattern.replace("\\", "/").lstrip("./")
    return any(
        fnmatch.fnmatch(path.replace("\\", "/").lstrip("./"), normalized)
        or path.replace("\\", "/").lstrip("./").startswith(normalized.rstrip("*/"))
        for path in paths
    )


def temporal_gate(note: dict[str, Any], context: TaskContext, repo_path: Path | None = None) -> tuple[bool, str, float]:
    if not is_time_active(str(note.get("valid_from", "")), str(note.get("valid_to", ""))):
        return False, "outside valid-time interval", 0.0
    as_of = str(note.get("as_of_commit", ""))
    if as_of and context.head:
        if as_of == context.head:
            return True, "commit validity exact", 1.0
        if repo_path and is_ancestor(repo_path, as_of, context.head):
            return True, "commit validity inherited through ancestry", 0.92
        return False, f"memory commit {as_of[:10]} is not in active lineage", 0.0
    return True, "temporally applicable", 0.82


def scope_gate(note: dict[str, Any], context: TaskContext, allow_cross_repo: bool = False) -> tuple[bool, str, float]:
    scope = str(note.get("scope", "repository"))
    metadata = note.get("metadata", {})
    note_repo_id = str(note.get("repository_id") or metadata.get("repository_id") or "")
    note_repo = str(note.get("repo") or metadata.get("repo") or "")
    note_project = str(note.get("project") or metadata.get("project") or "")
    note_module = str(note.get("module") or metadata.get("module") or "")
    note_branch = str(note.get("branch") or metadata.get("branch") or "")
    if scope == "global":
        return True, "global scope", 0.52
    if scope == "user":
        return True, "user scope", 0.6
    if note_repo_id and context.repository_id and note_repo_id != context.repository_id and not allow_cross_repo:
        return False, "repository identity mismatch", 0.0
    if not note_repo_id and note_repo and context.repo and note_repo not in {context.repo, Path(context.repo).name} and not allow_cross_repo:
        return False, f"legacy repo scope mismatch ({note_repo} != {context.repo})", 0.0
    if scope in {"repository", "project"}:
        if scope == "project" and note_project and context.project and note_project != context.project:
            return False, "project scope mismatch", 0.0
        return True, f"{scope} scope matched", 0.83 if scope == "repository" else 0.77
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


def feature_vector(note: dict[str, Any], context: TaskContext, repo_path: Path | None = None) -> dict[str, float]:
    task_types = set(context.task_types)
    active_paths = context.requested_paths + context.changed_paths
    metadata = note.get("metadata", {})
    applies = metadata.get("applies_to", [])
    if isinstance(applies, str):
        applies = [applies]
    path_score = 0.0
    for pattern in applies:
        if active_paths and _matches_path(str(pattern), active_paths):
            path_score = 1.0
            break
    if not path_score and active_paths:
        note_path = str(note.get("path", ""))
        if any(Path(path).stem and Path(path).stem in note_path for path in active_paths):
            path_score = 0.65
    validation = str(metadata.get("validation", note.get("freshness", "unknown")))
    validation_score = {"verified": 1.0, "valid": 0.92, "fresh": 0.9, "unvalidated": 0.42, "unknown": 0.4, "stale": 0.08}.get(validation, 0.4)
    days = age_days(str(note.get("validated") or note.get("updated") or note.get("created") or ""))
    freshness = max(0.0, 1.0 - min(days, 730.0) / 730.0)
    evidence = metadata.get("evidence", [])
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence_strength = min(1.0, 0.25 * len(evidence) + (0.3 if note.get("authority") in {"source-of-truth", "verified", "user-corrected"} else 0.0))
    return {
        "lexical": max(0.0, min(1.0, float(note.get("lexical", 0.0)))),
        "exact": max(0.0, min(1.0, float(note.get("exact", 0.0)))),
        "rrf": max(0.0, min(1.0, float(note.get("rrf_norm", 0.0)))),
        "type": 1.0 if note.get("type") in task_types else 0.22,
        "path": path_score,
        "confidence": max(0.0, min(1.0, float(note.get("confidence", 0.5)))),
        "authority": AUTHORITY.get(str(note.get("authority", "agent")), 0.42),
        "validation": validation_score,
        "freshness": freshness,
        "utility": max(0.0, min(1.0, float(note.get("utility", 0.5)))),
        "evidence": evidence_strength,
        "query_overlap": jaccard(context.task, f"{note.get('title','')} {note.get('summary','')}")
    }


def score_note(
    note: dict[str, Any], context: TaskContext, allow_untrusted: bool, allow_cross_repo: bool,
    repo_path: Path | None = None, weights: dict[str, float] | None = None, intercept: float = 0.0,
) -> tuple[float, list[str], str | None]:
    accepted, trust_reason = trust_gate(
        str(note.get("status", "active")), str(note.get("authority", "agent")), float(note.get("confidence", 0.5)),
        allow_untrusted, memory_type=str(note.get("type", "fact")),
        authorized_instruction=bool(note.get("authorized_instruction", False)), taint=str(note.get("taint", "unknown")),
    )
    if not accepted:
        return 0.0, [], trust_reason
    scope_ok, scope_reason, scope_score = scope_gate(note, context, allow_cross_repo)
    if not scope_ok:
        return 0.0, [], scope_reason
    temporal_ok, temporal_reason, temporal_score = temporal_gate(note, context, repo_path)
    if not temporal_ok:
        return 0.0, [], temporal_reason
    f = feature_vector(note, context, repo_path)
    # Versioned interpretable calibration policy. Hard constraints are never learned.
    learned = weights or {"lexical": 0.18, "exact": 0.16, "rrf": 0.12, "type": 0.09, "path": 0.08,
                          "confidence": 0.06, "authority": 0.05, "validation": 0.05, "freshness": 0.03,
                          "utility": 0.03, "evidence": 0.03, "query_overlap": 0.02}
    soft = intercept + sum(float(learned.get(key, 0.0)) * float(f.get(key, 0.0)) for key in learned)
    score = soft + 0.10 * scope_score + 0.02 * temporal_score
    if note.get("graph_relation"):
        score += 0.035
    if note.get("status") == "stale":
        score -= 0.28
    if context.risk == "high" and f["evidence"] < 0.3:
        score -= 0.12
    reasons = [f"policy={POLICY_VERSION}", f"lexical={f['lexical']:.2f}", f"rrf={f['rrf']:.2f}", scope_reason, temporal_reason, trust_reason]
    if note.get("type") in set(context.task_types):
        reasons.append(f"task type matched {note.get('type')}")
    if f["path"]:
        reasons.append(f"path={f['path']:.2f}")
    if note.get("graph_relation"):
        reasons.append(f"graph {note['graph_relation']} from {note.get('graph_source')}")
    reasons.append(f"confidence={f['confidence']:.2f}; authority={note.get('authority')}; evidence={f['evidence']:.2f}")
    return max(0.0, min(1.0, score)), reasons, None
