from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from .util import stable_json, utc_now


class EventLog:
    def __init__(self, path: Path):
        self.path = path

    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        record = {"timestamp": utc_now(), "event": event, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json(record) + "\n")
        return record

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result


@dataclass(slots=True)
class Span:
    trace_id: str
    span_id: str
    name: str
    started_at: str
    ended_at: str = ""
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_trace_id() -> str:
        return uuid.uuid4().hex

    @contextmanager
    def span(self, trace_id: str, name: str, **attributes: Any) -> Iterator[Span]:
        start = perf_counter()
        span = Span(trace_id, uuid.uuid4().hex[:16], name, utc_now(), attributes=attributes)
        try:
            yield span
        finally:
            span.ended_at = utc_now()
            span.duration_ms = round((perf_counter() - start) * 1000, 3)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(stable_json(span.to_dict()) + "\n")

    def emit(self, trace_id: str, name: str, **attributes: Any) -> None:
        span = Span(trace_id, uuid.uuid4().hex[:16], name, utc_now(), utc_now(), 0.0, attributes)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json(span.to_dict()) + "\n")
