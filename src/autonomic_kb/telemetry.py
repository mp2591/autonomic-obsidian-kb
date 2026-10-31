from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import KBConfig
from .security import reject_secrets
from .storage import vault_lock
from .util import atomic_write, stable_json, utc_now

ACCOUNTING_VERSION = 2

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
    cached_tokens: int | None = None
    searches: int | None = None
    file_reads: int | None = None
    commands: int | None = None
    retries: int | None = None
    corrections: int | None = None
    latency_ms: float | None = None
    maintenance_tokens: int | None = None
    unsafe: bool = False
    retrieved_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    pair_id: str = ""
    usage_complete: bool = True

    @property
    def total_tokens(self) -> int | None:
        if not self.usage_complete or any(
            value is None for value in (self.input_tokens, self.output_tokens, self.maintenance_tokens)
        ):
            return None
        # cached_tokens is a subset/accounting dimension for many providers; do not double-count it.
        return self.input_tokens + self.output_tokens + self.maintenance_tokens

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["total_tokens"] = self.total_tokens
        value["usage_complete"] = self.total_tokens is not None
        value["accounting_version"] = ACCOUNTING_VERSION
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
        if outcome.kb_mode not in {"kb", "no-kb"}:
            raise ValueError("outcome mode must be kb or no-kb")
        for name in ("success", "unsafe", "usage_complete"):
            if not isinstance(getattr(outcome, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in ("task_id", "task_hash"):
            if not isinstance(getattr(outcome, name), str) or not getattr(outcome, name).strip():
                raise ValueError(f"{name} must be a nonempty string")
        if not isinstance(outcome.pair_id, str) or not isinstance(outcome.metadata, dict):
            raise ValueError("pair_id must be a string and metadata must be an object")
        if outcome.latency_ms is not None and (
            isinstance(outcome.latency_ms, bool)
            or not isinstance(outcome.latency_ms, int | float)
            or not math.isfinite(outcome.latency_ms)
            or outcome.latency_ms < 0
        ):
            raise ValueError("latency must be finite, nonnegative, or unknown")
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
        for number, line in enumerate(self.outcome_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid outcome record at line {number}; repair before benchmarking") from error
            if not isinstance(row, dict):
                raise ValueError(f"outcome record at line {number} must be an object")
            result.append(row)
        return result

    @staticmethod
    def _counter(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    @classmethod
    def _delta(cls, a: Any, b: Any) -> int | None:
        left, right = cls._counter(a), cls._counter(b)
        return right - left if left is not None and right is not None else None

    @classmethod
    def _row_total(cls, row: dict[str, Any]) -> int | None:
        # Earlier records could default omitted maintenance to zero. Retain them,
        # but do not silently upgrade them into usage-complete measurements.
        if row.get("accounting_version") != ACCOUNTING_VERSION or row.get("usage_complete") is not True:
            return None
        values = [cls._counter(row.get(key)) for key in ("input_tokens", "output_tokens", "maintenance_tokens")]
        if any(value is None for value in values):
            return None
        cached = row.get("cached_tokens")
        if cached is not None and (cls._counter(cached) is None or cached > values[0]):
            return None
        return sum(values)

    def paired_summary(self) -> dict[str, Any]:
        groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
        rejected = []
        for number, row in enumerate(self.outcomes(), 1):
            mode = row.get("kb_mode")
            if mode not in {"no-kb", "kb"} or not isinstance(row.get("success"), bool):
                rejected.append({"record": number, "reason": "invalid mode or success label"})
                continue
            pair, task = row.get("pair_id"), row.get("task_hash")
            if not isinstance(task, str) or not task or (pair and not isinstance(pair, str)):
                rejected.append({"record": number, "reason": "invalid experiment identity"})
                continue
            # Explicit pair IDs must not collide with legacy task-hash grouping.
            key = ("pair", pair) if pair else ("task", task)
            groups.setdefault(key, {}).setdefault(mode, []).append(row)
        paired: list[dict[str, Any]] = []
        for (group_kind, pair_id), modes in groups.items():
            if len(modes.get("no-kb", [])) != 1 or len(modes.get("kb", [])) != 1:
                rejected.append({"pair_id": pair_id, "reason": "missing or ambiguous experimental arm"})
                continue
            baseline, assisted = modes["no-kb"][0], modes["kb"][0]
            fields = ("repo_snapshot", "vault_snapshot", "model_id", "agent_version", "evaluator_id", "tool_config")
            left, right = baseline.get("metadata", {}), assisted.get("metadata", {})
            matched = group_kind == "pair" and baseline.get("task_hash") == assisted.get("task_hash")
            matched = matched and isinstance(left, dict) and isinstance(right, dict)
            matched = matched and all(left.get(field) and left.get(field) == right.get(field) for field in fields)
            matched = matched and left.get("evaluated") is True and right.get("evaluated") is True
            baseline_total, assisted_total = self._row_total(baseline), self._row_total(assisted)
            paired.append(
                {
                    "pair_id": pair_id,
                    "group_kind": group_kind,
                    "matched": bool(matched),
                    "task_hash": assisted["task_hash"],
                    "success_delta": int(assisted["success"]) - int(baseline["success"]),
                    "token_delta": self._delta(baseline_total, assisted_total),
                    "search_delta": self._delta(baseline.get("searches"), assisted.get("searches")),
                    "read_delta": self._delta(baseline.get("file_reads"), assisted.get("file_reads")),
                    "usage_complete": baseline_total is not None and assisted_total is not None,
                }
            )
        return {
            "pairs": len(paired),
            "complete_usage_pairs": sum(item["usage_complete"] for item in paired),
            "items": paired,
            "rejected": rejected,
        }
