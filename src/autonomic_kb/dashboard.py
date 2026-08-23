from __future__ import annotations

from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .telemetry import TelemetryStore
from .validation import Validator


def dashboard_data(config: KBConfig) -> dict[str, Any]:
    with KnowledgeIndex(config) as index:
        index.index_vault()
        stats = index.stats()
        conflicts = index.all_notes({"conflicted"})
        stale = index.all_notes({"stale"})
        inbox = index.all_notes({"inbox"})
        quarantined = index.all_notes({"quarantined"})
        priorities = Validator(config, index).validation_priorities()[:20]
    feedback = []
    if config.feedback_path.exists():
        feedback = config.feedback_path.read_text(encoding="utf-8").splitlines()[-50:]
    return {
        "stats": stats,
        "queues": {
            "inbox": [{"id": x["declared_id"] or x["id"], "path": x["path"]} for x in inbox],
            "quarantine": [{"id": x["declared_id"] or x["id"], "path": x["path"]} for x in quarantined],
            "conflicts": [{"id": x["declared_id"] or x["id"], "path": x["path"]} for x in conflicts],
            "stale": [{"id": x["declared_id"] or x["id"], "path": x["path"]} for x in stale],
        },
        "validation_priorities": priorities,
        "paired_outcomes": TelemetryStore(config).paired_summary(),
        "recent_feedback_rows": len(feedback),
    }
