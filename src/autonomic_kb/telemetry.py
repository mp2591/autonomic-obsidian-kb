from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import KBConfig
from .security import reject_secrets
from .storage import vault_lock
from .util import atomic_write, stable_json, utc_now

FEEDBACK_KINDS = {
    "helpful",
    "irrelevant",
    "incorrect",
    "stale",
    "incomplete",
    "expanded",
    "caused-search",
    "caused-correction",
    "caused-failure",
    "unsafe",
    "missing-memory",
    "scope-too-broad",
    "scope-too-narrow",
}


@dataclass(slots=True)
class RetrievalFeedback:
    retrieval_id: str
    memory_id: str
    kind: str
    actor: str = "agent"
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    task_hash: str
    kb_mode: str
    success: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = 0
    searches: int | None = 0
    file_reads: int | None = 0
    commands: int | None = 0
    retries: int | None = 0
    corrections: int | None = 0
    latency_ms: float = 0.0
    maintenance_tokens: int | None = 0
    unsafe: bool = False
    retrieved_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    pair_id: str = ""
    usage_complete: bool = True

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None or self.maintenance_tokens is None:
            return None
        # cached_tokens is a subset/accounting dimension for many providers; do not double-count it.
        return self.input_tokens + self.output_tokens + self.maintenance_tokens

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["total_tokens"] = self.total_tokens
        return value


class TelemetryStore:
    def __init__(self, config: KBConfig):
        self.config = config
        config.ensure_runtime()
        self.outcome_path = config.vault / ".kb-outcomes.jsonl"
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        marker = self.config.vault / ".kb-telemetry-migration.json"
        with vault_lock(self.config.vault):
            if marker.exists():
                return
            pending = []
            for name, destination in (
                ("outcomes.jsonl", self.outcome_path),
                ("feedback.jsonl", self.config.feedback_path),
            ):
                legacy = self.config.runtime_dir / name
                if not legacy.exists() or legacy == destination:
                    continue
                imported = []
                for line in legacy.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("legacy telemetry requires dictionary records")
                    reject_secrets(row)
                    if name == "outcomes.jsonl":
                        row["pair_id"] = ""
                        row["usage_complete"] = False
                        row["total_tokens"] = None
                        row["legacy_import"] = True
                    imported.append(stable_json(row))
                current = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
                merged = list(dict.fromkeys(current + imported))
                pending.append((destination, "\n".join(merged) + "\n"))
            for destination, content in pending:
                atomic_write(destination, content)
            atomic_write(marker, stable_json({"version": 3, "migrated": [p.name for p, _ in pending]}))

    def feedback(
        self,
        retrieval_id: str,
        memory_id: str,
        kind: str,
        *,
        actor: str = "agent",
        notes: str = "",
    ) -> RetrievalFeedback:
        if kind not in FEEDBACK_KINDS:
            raise ValueError(f"unsupported feedback kind {kind!r}")
        value = RetrievalFeedback(retrieval_id, memory_id, kind, actor, notes, utc_now())
        reject_secrets(value.to_dict())
        with vault_lock(self.config.vault), self.config.feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json(value.to_dict()) + "\n")
        return value

    def record_outcome(self, outcome: TaskOutcome) -> TaskOutcome:
        counters = (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "maintenance_tokens",
            "searches",
            "file_reads",
            "commands",
            "retries",
            "corrections",
        )
        for name in counters:
            value = getattr(outcome, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("usage counters must be nonnegative integers or unknown")
        if (
            outcome.cached_tokens is not None
            and outcome.input_tokens is not None
            and outcome.cached_tokens > outcome.input_tokens
        ):
            raise ValueError("cached tokens must be a subset of normalized input tokens")
        outcome.usage_complete = outcome.total_tokens is not None
        reject_secrets(outcome.to_dict())
        if not outcome.created_at:
            outcome.created_at = utc_now()
        with vault_lock(self.config.vault), self.outcome_path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json(outcome.to_dict()) + "\n")
        return outcome

    def outcomes(self) -> list[dict[str, Any]]:
        if not self.outcome_path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in self.outcome_path.read_text(encoding="utf-8").splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    @staticmethod
    def _delta(a: Any, b: Any) -> int | None:
        if a is None or b is None:
            return None
        return int(b) - int(a)

    def paired_summary(self) -> dict[str, Any]:
        groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in self.outcomes():
            key = str(row.get("pair_id") or row.get("task_hash", ""))
            groups.setdefault(key, {}).setdefault(str(row.get("kb_mode", "")), []).append(row)
        paired: list[dict[str, Any]] = []
        rejected = []
        for pair_id, modes in groups.items():
            if len(modes.get("no-kb", [])) != 1 or len(modes.get("kb", [])) != 1:
                rejected.append({"pair_id": pair_id, "reason": "missing or ambiguous experimental arm"})
                continue
            baseline, assisted = modes["no-kb"][0], modes["kb"][0]
            fields = ("repo_snapshot", "vault_snapshot", "model_id", "agent_version", "evaluator_id", "tool_config")
            left, right = baseline.get("metadata", {}), assisted.get("metadata", {})
            matched = bool(baseline.get("pair_id") and assisted.get("pair_id"))
            matched = matched and baseline.get("task_hash") == assisted.get("task_hash")
            matched = matched and all(left.get(field) and left.get(field) == right.get(field) for field in fields)
            matched = matched and bool(left.get("evaluated") and right.get("evaluated"))
            paired.append(
                {
                    "pair_id": pair_id,
                    "matched": bool(matched),
                    "task_hash": assisted.get("task_hash") or baseline.get("task_hash"),
                    "success_delta": int(bool(assisted.get("success"))) - int(bool(baseline.get("success"))),
                    "token_delta": self._delta(baseline.get("total_tokens"), assisted.get("total_tokens")),
                    "search_delta": self._delta(baseline.get("searches"), assisted.get("searches")),
                    "read_delta": self._delta(baseline.get("file_reads"), assisted.get("file_reads")),
                    "usage_complete": bool(
                        baseline.get("usage_complete", True) and assisted.get("usage_complete", True)
                    ),
                }
            )
        return {
            "pairs": len(paired),
            "complete_usage_pairs": sum(bool(item["usage_complete"]) for item in paired),
            "items": paired,
            "rejected": rejected,
        }
