from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
