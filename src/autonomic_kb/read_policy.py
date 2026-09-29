from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import VALID_STATUSES, VALID_TYPES, TaskContext
from .scoring import scope_gate, temporal_gate
from .security import scan_content, trust_gate


def memory_read_gate(
    note: dict[str, Any],
    context: TaskContext,
    *,
    allow_untrusted: bool = False,
    allow_cross_repo: bool = False,
    repo_path: Path | None = None,
) -> tuple[bool, str]:
    """Centralized hard policy gate for every agent-visible memory read."""
    if (
        not note.get("schema_valid", True)
        or note.get("status") not in VALID_STATUSES
        or note.get("type") not in VALID_TYPES
    ):
        return False, "invalid memory schema"
    if note.get("status") == "inbox":
        return False, "inbox memory is not promoted"
    content = str(note.get("body", "")) + str(note.get("metadata", {}))
    if scan_content(content):
        return False, "unsafe memory content"
    accepted, reason = trust_gate(
        str(note.get("status", "active")),
        str(note.get("authority", "agent")),
        float(note.get("confidence", 0.5)),
        allow_untrusted,
        memory_type=str(note.get("type", "fact")),
        authorized_instruction=bool(note.get("authorized_instruction", False)),
        taint=str(note.get("taint", "unknown")),
    )
    if not accepted:
        return False, reason
    scope_ok, scope_reason, _ = scope_gate(note, context, allow_cross_repo)
    if not scope_ok:
        return False, scope_reason
    temporal_ok, temporal_reason, _ = temporal_gate(note, context, repo_path)
    if not temporal_ok:
        return False, temporal_reason
    return True, f"passed hard read gate: {scope_reason}; {temporal_reason}; {reason}"


def conflicting_claim_ids(notes: list[dict[str, Any]]) -> set[str]:
    """Identify conflicting values among notes already applicable to one query."""
    from .util import stable_json

    groups: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        metadata = note.get("metadata", {})
        claim = metadata.get("claim_key")
        if not claim:
            continue
        namespace = [
            claim,
            note.get("scope"),
            note.get("repository_id") or note.get("repo"),
            note.get("project"),
            note.get("module"),
            note.get("branch"),
            metadata.get("task"),
            metadata.get("session"),
            metadata.get("applies_to", []),
        ]
        groups.setdefault(stable_json(namespace), []).append(note)
    blocked: set[str] = set()
    for group in groups.values():
        if len({stable_json(note["metadata"].get("claim_value")) for note in group}) > 1:
            blocked.update(note["id"] for note in group)
    return blocked
