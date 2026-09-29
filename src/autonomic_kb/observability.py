from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from .util import stable_json, utc_now

# Runtime logs under .kb/ are derived; keep one rotated generation so they stay bounded.
DEFAULT_MAX_LOG_BYTES = 5_000_000


def append_line(path: Path, line: str, max_bytes: int = DEFAULT_MAX_LOG_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size >= max_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def tail_lines(path: Path, limit: int, block: int = 65536) -> list[str]:
    """Last ``limit`` lines, reading backwards from the end instead of the whole file."""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return []
    with handle:
        end = handle.seek(0, os.SEEK_END)
        data = b""
        position = end
        while position > 0 and data.count(b"\n") <= limit:
            position = max(0, position - block)
            handle.seek(position)
            data = handle.read(end - position)
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


class EventLog:
    def __init__(self, path: Path, max_bytes: int = DEFAULT_MAX_LOG_BYTES):
        self.path = path
        self.max_bytes = max_bytes

    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        record = {"timestamp": utc_now(), "event": event, **payload}
        append_line(self.path, stable_json(record), self.max_bytes)
        return record

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        lines = tail_lines(self.path, limit)
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
            append_line(self.path, stable_json(span.to_dict()))

    def emit(self, trace_id: str, name: str, **attributes: Any) -> None:
        span = Span(trace_id, uuid.uuid4().hex[:16], name, utc_now(), utc_now(), 0.0, attributes)
        append_line(self.path, stable_json(span.to_dict()))
