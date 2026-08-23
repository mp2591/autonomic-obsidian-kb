from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import KBConfig
from .util import atomic_write, parse_time, sha256_text


@dataclass(slots=True)
class TaskLease:
    lease_id: str
    task_signature: str
    agent: str
    scope: str
    started: str
    expires: str
    artifacts: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LeaseStore:
    def __init__(self, config: KBConfig):
        self.config = config
        config.ensure_runtime()

    def acquire(self, task: str, agent: str, scope: str = "repository", ttl_minutes: int = 30) -> TaskLease:
        signature = sha256_text(task.strip().lower())[:20]
        existing = self.find(signature)
        if existing:
            raise ValueError(f"task lease already held by {existing.agent} until {existing.expires}")
        now = datetime.now(UTC)
        lease = TaskLease(
            lease_id=f"lease:{uuid.uuid4().hex}",
            task_signature=signature,
            agent=agent,
            scope=scope,
            started=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            expires=(now + timedelta(minutes=max(1, ttl_minutes)))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            artifacts=[],
        )
        atomic_write(self.config.lease_dir / f"{signature}.json", json.dumps(lease.to_dict(), indent=2) + "\n")
        return lease

    def find(self, task_or_signature: str) -> TaskLease | None:
        signature = (
            task_or_signature
            if len(task_or_signature) == 20 and " " not in task_or_signature
            else sha256_text(task_or_signature.strip().lower())[:20]
        )
        path = self.config.lease_dir / f"{signature}.json"
        if not path.exists():
            return None
        try:
            lease = TaskLease(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        expiry = parse_time(lease.expires)
        if not expiry or expiry <= datetime.now(UTC):
            path.unlink(missing_ok=True)
            return None
        return lease

    def release(self, task_or_signature: str, agent: str = "") -> bool:
        lease = self.find(task_or_signature)
        if not lease:
            return False
        if agent and lease.agent != agent:
            raise ValueError(f"lease belongs to {lease.agent}")
        (self.config.lease_dir / f"{lease.task_signature}.json").unlink(missing_ok=True)
        return True
