from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import KBConfig
from .util import stable_json, utc_now

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
    input_tokens: int | None = 0
    output_tokens: int | None = 0
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
        if self.input_tokens is None or self.output_tokens is None:
            return None
        # cached_tokens is a subset/accounting dimension for many providers; do not double-count it.
        return self.input_tokens + self.output_tokens + (self.maintenance_tokens or 0)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["total_tokens"] = self.total_tokens
        return value


class TelemetryStore:
    def __init__(self, config: KBConfig):
        self.config = config
        config.ensure_runtime()
        self.outcome_path = config.runtime_dir / "outcomes.jsonl"

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
        with self.config.feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json(value.to_dict()) + "\n")
        return value

    def record_outcome(self, outcome: TaskOutcome) -> TaskOutcome:
        if not outcome.created_at:
            outcome.created_at = utc_now()
        with self.outcome_path.open("a", encoding="utf-8") as handle:
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
        for pair_id, modes in groups.items():
            if len(modes.get("no-kb", [])) != 1 or len(modes.get("kb", [])) != 1:
                continue
            baseline, assisted = modes["no-kb"][0], modes["kb"][0]
            paired.append(
                {
                    "pair_id": pair_id,
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
        }
