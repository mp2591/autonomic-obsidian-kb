from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import atomic_write, stable_json, utc_now


@dataclass(slots=True)
class ContextInventory:
    epoch: str
    revisions: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "revisions": self.revisions, "updated_at": self.updated_at}


class ContextStateStore:
    """Host-acknowledged context inventory. Never assumes delivery implies retention."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session)[:120] or "default"
        return self.root / f"{safe}.json"

    def get(self, session: str) -> ContextInventory | None:
        path = self._path(session)
        if not path.exists():
            return None
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            return ContextInventory(str(row.get("epoch", "")), dict(row.get("revisions", {})), str(row.get("updated_at", "")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def acknowledge(self, session: str, epoch: str, revisions: dict[str, str]) -> ContextInventory:
        value = ContextInventory(epoch=epoch, revisions=dict(revisions), updated_at=utc_now())
        atomic_write(self._path(session), stable_json(value.to_dict()) + "\n")
        return value
