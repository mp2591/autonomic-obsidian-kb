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
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    searches: int = 0
    file_reads: int = 0
    commands: int = 0
    retries: int = 0
    corrections: int = 0
    latency_ms: float = 0.0
    maintenance_tokens: int = 0
    unsafe: bool = False
    retrieved_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens + self.maintenance_tokens

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
        self, retrieval_id: str, memory_id: str, kind: str, *, actor: str = "agent", notes: str = ""
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

    def paired_summary(self) -> dict[str, Any]:
        groups: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self.outcomes():
            groups.setdefault(str(row.get("task_hash", "")), {})[str(row.get("kb_mode", ""))] = row
        paired = []
        for task_hash, modes in groups.items():
            if "no-kb" in modes and "kb" in modes:
                baseline, assisted = modes["no-kb"], modes["kb"]
                paired.append(
                    {
                        "task_hash": task_hash,
                        "success_delta": int(bool(assisted.get("success"))) - int(bool(baseline.get("success"))),
                        "token_delta": int(assisted.get("total_tokens", 0)) - int(baseline.get("total_tokens", 0)),
                        "search_delta": int(assisted.get("searches", 0)) - int(baseline.get("searches", 0)),
                        "read_delta": int(assisted.get("file_reads", 0)) - int(baseline.get("file_reads", 0)),
                    }
                )
        return {"pairs": len(paired), "items": paired}
