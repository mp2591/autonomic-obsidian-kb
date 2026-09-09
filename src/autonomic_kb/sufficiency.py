from __future__ import annotations

from typing import Any

from .models import TaskContext


_REQUIRED = {
    "debug": {"failure", "fix", "verification"},
    "procedure": {"steps", "verification"},
    "explain": {"claim", "rationale"},
    "locate": {"location"},
    "retrieve": {"claim"},
}


def evidence_roles(note: dict[str, Any]) -> set[str]:
    metadata = note.get("metadata", {})
    roles = metadata.get("evidence_roles", [])
    if isinstance(roles, str):
        roles = [roles]
    result = {str(role) for role in roles if str(role)}
    memory_type = str(note.get("type", ""))
    mapping = {
        "known-failure": {"failure"},
        "solution": {"fix"},
        "procedure": {"steps"},
        "command": {"steps"},
        "workflow": {"steps"},
        "decision": {"claim", "rationale"},
        "architecture": {"claim", "rationale"},
        "repository-map": {"location"},
        "file-map": {"location"},
        "fact": {"claim"},
        "invariant": {"claim"},
    }
    result.update(mapping.get(memory_type, set()))
    validators = metadata.get("validators", [])
    if validators:
        result.add("verification")
    if metadata.get("verification") or metadata.get("postcondition"):
        result.add("verification")
    return result


def assess_sufficiency(context: TaskContext, selected_notes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    intent = "retrieve"
    task_types = set(context.task_types)
    if task_types & {"known-failure", "solution"}:
        intent = "debug"
    elif task_types & {"command", "workflow", "procedure"}:
        intent = "procedure"
    elif task_types & {"architecture", "decision"}:
        intent = "explain"
    elif context.requested_paths or context.changed_symbols:
        intent = "locate"
    required = set(_REQUIRED[intent])
    covered: set[str] = set()
    for note in selected_notes:
        covered.update(evidence_roles(note))
    missing = sorted(required - covered)
    if not selected_notes:
        return "insufficient_evidence", sorted(required)
    if missing:
        return "partial_context", missing
    return "sufficient_context", []
