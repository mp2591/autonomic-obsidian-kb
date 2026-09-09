from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import KBConfig
from .security import reject_secrets
from .storage import vault_lock
from .util import atomic_write, sha256_text, stable_json, utc_now

VALID_OPERATIONS = {
    "ADD",
    "AMEND",
    "SUPERSEDE",
    "RETRACT",
    "MERGE",
    "SPLIT",
    "REVALIDATE",
    "QUARANTINE",
    "ARCHIVE",
    "NOOP",
}


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
    sequence: int = 0
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
        self,
        kind: str,
        content: str,
        *,
        subject: str = "",
        repository_id: str = "",
        commit: str = "",
        path: str = "",
        producer: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        reject_secrets([content, metadata, subject, path, producer, repository_id])
        envelope = {
            "kind": kind,
            "subject": subject,
            "repository_id": repository_id,
            "commit": commit,
            "path": path,
            "content_digest": sha256_text(content),
            "producer": producer,
            "metadata": metadata or {},
        }
        digest = sha256_text(stable_json(envelope))
        evidence_id = f"evidence:sha256:{digest}"
        record = EvidenceRecord(
            evidence_id=evidence_id,
            kind=kind,
            subject=subject,
            repository_id=repository_id,
            commit=commit,
            path=path,
            content=content,
            content_digest=sha256_text(content),
            observed_at=utc_now(),
            producer=producer,
            metadata=metadata or {},
        )
        destination = self.config.evidence_dir / digest[:2] / f"{digest}.json"
        if not destination.exists():
            atomic_write(destination, json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
        return record

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        if not re.fullmatch(r"evidence:sha256:[a-f0-9]{64}", evidence_id):
            return None
        digest = evidence_id.rsplit(":", 1)[-1]
        path = self.config.evidence_dir / digest[:2] / f"{digest}.json"
        if (
            not path.exists()
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.config.evidence_dir.resolve())
        ):
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record = EvidenceRecord(**data)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if record.evidence_id != evidence_id or record.content_digest != sha256_text(record.content):
            return None
        envelope = {
            "kind": record.kind,
            "subject": record.subject,
            "repository_id": record.repository_id,
            "commit": record.commit,
            "path": record.path,
            "content_digest": record.content_digest,
            "producer": record.producer,
            "metadata": record.metadata,
        }
        if digest != sha256_text(stable_json(envelope)):
            return None
        return record

    def verify(self, evidence_id: str) -> bool:
        return self.get(evidence_id) is not None

    def list_ids(self) -> list[str]:
        result: list[str] = []
        for path in self.config.evidence_dir.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                identity = str(data.get("evidence_id", ""))
                if self.verify(identity):
                    result.append(identity)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(result)


class OperationLedger:
    def __init__(self, config: KBConfig):
        self.config = config
        self.config.ensure_runtime()

    def append(self, operation: str, memory_id: str, **kwargs: Any) -> MemoryOperation:
        with vault_lock(self.config.vault):
            return self._append_locked(operation, memory_id, **kwargs)

    def _append_locked(
        self,
        operation: str,
        memory_id: str,
        *,
        actor: str = "system",
        basis: list[str] | None = None,
        previous_digest: str = "",
        new_digest: str = "",
        reason: str = "",
        policy_version: str = "v2",
        confidence_before: float | None = None,
        confidence_after: float | None = None,
        metadata: dict[str, Any] | None = None,
        require_previous: str | None = None,
    ) -> MemoryOperation:
        operation = operation.upper()
        if operation not in VALID_OPERATIONS:
            raise ValueError(f"unsupported memory operation: {operation}")
        last = self.last_for(memory_id)
        actual_previous = last.new_digest if last else ""
        if require_previous is not None and actual_previous != require_previous:
            raise ValueError(
                f"memory {memory_id} changed concurrently: expected {require_previous!r}, got {actual_previous!r}"
            )
        record = MemoryOperation(
            operation_id=f"op:{uuid.uuid4().hex}",
            sequence=(last.sequence + 1) if last else 1,
            operation=operation,
            memory_id=memory_id,
            actor=actor,
            observed_at=utc_now(),
            basis=list(basis or []),
            previous_digest=previous_digest or actual_previous,
            new_digest=new_digest,
            reason=reason,
            policy_version=policy_version,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            metadata=metadata or {},
        )
        reject_secrets(record.to_dict())
        day = record.observed_at[:10]
        destination = (
            self.config.operation_dir / day / f"{record.observed_at.replace(':', '')}-{record.operation_id[3:]}.json"
        )
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
        return sorted(result, key=lambda item: (item.sequence, item.observed_at, item.operation_id))

    def last_for(self, memory_id: str) -> MemoryOperation | None:
        values = self.iter_operations(memory_id)
        return values[-1] if values else None
