from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import KBConfig
from .util import atomic_write, sha256_text, stable_json, utc_now

VALID_OPERATIONS = {"ADD", "AMEND", "SUPERSEDE", "RETRACT", "MERGE", "SPLIT", "REVALIDATE", "QUARANTINE", "ARCHIVE", "NOOP"}


@dataclass(slots=True)
class EvidenceRecord:
    evidence_id: str
    kind: str
    subject: str = ""
    repository_id: str = ""
    commit: str = ""
    path: str = ""
    content: str = ""
    content_digest: str = ""
    observed_at: str = ""
    producer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryOperation:
    operation_id: str
    operation: str
    memory_id: str
    actor: str
    observed_at: str
    basis: list[str] = field(default_factory=list)
    previous_digest: str = ""
    new_digest: str = ""
    reason: str = ""
    policy_version: str = "v2"
    confidence_before: float | None = None
    confidence_after: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceStore:
    def __init__(self, config: KBConfig):
        self.config = config
        self.config.ensure_runtime()

    def put(
        self, kind: str, content: str, *, subject: str = "", repository_id: str = "", commit: str = "",
        path: str = "", producer: str = "", metadata: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        digest = sha256_text(content)
        evidence_id = f"evidence:sha256:{digest}"
        record = EvidenceRecord(
            evidence_id=evidence_id, kind=kind, subject=subject, repository_id=repository_id,
            commit=commit, path=path, content=content, content_digest=digest, observed_at=utc_now(),
            producer=producer, metadata=metadata or {},
        )
        destination = self.config.evidence_dir / digest[:2] / f"{digest}.json"
        if not destination.exists():
            atomic_write(destination, json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
        return record

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        digest = evidence_id.rsplit(":", 1)[-1]
        path = self.config.evidence_dir / digest[:2] / f"{digest}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record = EvidenceRecord(**data)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if record.content_digest != sha256_text(record.content):
            return None
        return record

    def verify(self, evidence_id: str) -> bool:
        return self.get(evidence_id) is not None

    def list_ids(self) -> list[str]:
        result: list[str] = []
        for path in self.config.evidence_dir.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("evidence_id"):
                    result.append(str(data["evidence_id"]))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(result)


class OperationLedger:
    def __init__(self, config: KBConfig):
        self.config = config
        self.config.ensure_runtime()

    def append(
        self, operation: str, memory_id: str, *, actor: str = "system", basis: list[str] | None = None,
        previous_digest: str = "", new_digest: str = "", reason: str = "", policy_version: str = "v2",
        confidence_before: float | None = None, confidence_after: float | None = None,
        metadata: dict[str, Any] | None = None, require_previous: str | None = None,
    ) -> MemoryOperation:
        operation = operation.upper()
        if operation not in VALID_OPERATIONS:
            raise ValueError(f"unsupported memory operation: {operation}")
        last = self.last_for(memory_id)
        actual_previous = last.new_digest if last else ""
        if require_previous is not None and actual_previous != require_previous:
            raise ValueError(f"memory {memory_id} changed concurrently: expected {require_previous!r}, got {actual_previous!r}")
        record = MemoryOperation(
            operation_id=f"op:{uuid.uuid4().hex}", operation=operation, memory_id=memory_id,
            actor=actor, observed_at=utc_now(), basis=list(basis or []), previous_digest=previous_digest or actual_previous,
            new_digest=new_digest, reason=reason, policy_version=policy_version,
            confidence_before=confidence_before, confidence_after=confidence_after, metadata=metadata or {},
        )
        day = record.observed_at[:10]
        destination = self.config.operation_dir / day / f"{record.observed_at.replace(':', '')}-{record.operation_id[3:]}.json"
        atomic_write(destination, json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
        return record

    def iter_operations(self, memory_id: str | None = None) -> list[MemoryOperation]:
        result: list[MemoryOperation] = []
        for path in sorted(self.config.operation_dir.rglob("*.json")):
            try:
                value = MemoryOperation(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if memory_id is None or value.memory_id == memory_id:
                result.append(value)
        return result

    def last_for(self, memory_id: str) -> MemoryOperation | None:
        values = self.iter_operations(memory_id)
        return values[-1] if values else None
