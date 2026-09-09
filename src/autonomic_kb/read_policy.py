from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import TaskContext
from .scoring import scope_gate, temporal_gate
from .security import trust_gate


def memory_read_gate(
    note: dict[str, Any],
    context: TaskContext,
    *,
    allow_untrusted: bool = False,
    allow_cross_repo: bool = False,
    repo_path: Path | None = None,
) -> tuple[bool, str]:
    """Centralized hard policy gate for every agent-visible memory read."""
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
