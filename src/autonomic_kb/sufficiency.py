from __future__ import annotations

from typing import Any

from .models import TaskContext
from .query_plan import build_query_plan

_REQUIRED = {
    "debug": {"failure", "fix", "verification"},
    "procedure": {"preconditions", "steps", "verification"},
    "explain": {"claim", "rationale"},
    "locate": {"location"},
    "retrieve": {"claim"},
}


def evidence_roles(note: dict[str, Any]) -> set[str]:
    """Roles describe delivered content, not the existence of unused validators."""
    text = str(note.get("delivered_text", ""))
    metadata = note.get("metadata", {})
    mapping = {
        "known-failure": {"failure"},
        "solution": set(),
        "procedure": set(),
        "command": set(),
        "workflow": set(),
        "decision": {"claim"},
        "architecture": {"claim"},
        "repository-map": {"location"},
        "file-map": {"location"},
        "fact": {"claim"},
        "invariant": {"claim"},
    }
    result = set(mapping.get(str(note.get("type", "")), set())) if text else set()
    detail = str(note.get("l2", "")).strip()
    if detail and detail in text:
        if note.get("type") in {"procedure", "command", "workflow"}:
            result.add("steps")
        elif note.get("type") == "solution":
            result.add("fix")
    for role, field in (("verification", "verification"), ("rationale", "rationale")):
        value = metadata.get(field)
        if isinstance(value, str) and value.strip() and value.strip() in text:
            result.add(role)
    conditions = metadata.get("preconditions", [])
    if isinstance(conditions, str):
        conditions = [conditions]
    if conditions and all(str(value) in text for value in conditions):
        result.add("preconditions")
    return result


def assess_sufficiency(context: TaskContext, selected_notes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    required = _REQUIRED[build_query_plan(context).intent]
    covered = set().union(*(evidence_roles(note) for note in selected_notes))
    missing = sorted(required - covered)
    if not selected_notes:
        return "insufficient_evidence", sorted(required)
    return ("partial_context", missing) if missing else ("sufficient_context", [])


def recorded_roles(note: dict[str, Any]) -> set[str]:
    """Roles the note could supply if it were delivered in full."""
    metadata = note.get("metadata", {})
    parts = [str(note.get(f"l{level}") or "") for level in range(5)] + [str(note.get("summary") or "")]
    for field in ("verification", "rationale"):
        value = metadata.get(field)
        if isinstance(value, str):
            parts.append(value)
    conditions = metadata.get("preconditions", [])
    if isinstance(conditions, str):
        conditions = [conditions]
    if isinstance(conditions, list):
        parts.extend(str(value) for value in conditions)
    return evidence_roles(dict(note, delivered_text="\n".join(parts)))


def unrecorded_roles(missing: list[str], selected_notes: list[dict[str, Any]]) -> list[str]:
    """Missing roles no selected memory records; expanding the delivered notes cannot supply them."""
    if not selected_notes:
        return []
    available = set().union(*(recorded_roles(note) for note in selected_notes))
    return [role for role in missing if role not in available]
