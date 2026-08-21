from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .markdown import ParsedMarkdown, parse_markdown
from .util import estimate_tokens, sha256_text

VALID_TYPES = {
    "agent-instruction", "api", "architecture", "command", "convention", "decision",
    "dependency", "domain", "environment", "fact", "file-map", "hypothesis", "interface",
    "invariant", "known-failure", "repository-map", "solution", "terminology", "workflow",
}
VALID_SCOPES = {"global", "user", "repository", "project", "module", "branch", "task", "session"}
VALID_STATUSES = {"active", "inbox", "stale", "conflicted", "superseded", "archived", "quarantined"}


@dataclass(slots=True)
class MemoryRecord:
    id: str
    path: str
    title: str
    type: str
    scope: str
    status: str
    summary: str
    confidence: float
    authority: str
    repo: str = ""
    project: str = ""
    module: str = ""
    branch: str = ""
    created: str = ""
    updated: str = ""
    validated: str = ""
    freshness: str = ""
    token_cost: int = 0
    utility: float = 0.5
    layers: dict[int, str] = field(default_factory=dict)
    body: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""
    schema_valid: bool = True

    @classmethod
    def from_text(cls, path: str, text: str) -> "MemoryRecord":
        parsed: ParsedMarkdown = parse_markdown(text)
        metadata = parsed.metadata
        declared_id = str(metadata.get("id", "")).strip()
        fallback = f"path:{Path(path).with_suffix('').as_posix()}"
        memory_id = declared_id or fallback
        title = str(metadata.get("title") or Path(path).stem.replace("-", " ").title())
        summary = str(metadata.get("summary") or parsed.layers.get(1, ""))
        try:
            confidence = float(metadata.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        try:
            utility = float(metadata.get("utility", 0.5))
        except (TypeError, ValueError):
            utility = 0.5
        schema_valid = bool(declared_id and metadata.get("title") and metadata.get("type") and metadata.get("scope"))
        return cls(
            id=memory_id,
            path=path,
            title=title,
            type=str(metadata.get("type", "fact")),
            scope=str(metadata.get("scope", "repository")),
            status=str(metadata.get("status", "active")),
            summary=summary,
            confidence=max(0.0, min(1.0, confidence)),
            authority=str(metadata.get("authority", "agent")),
            repo=str(metadata.get("repo", "")),
            project=str(metadata.get("project", "")),
            module=str(metadata.get("module", "")),
            branch=str(metadata.get("branch", "")),
            created=str(metadata.get("created", "")),
            updated=str(metadata.get("updated", "")),
            validated=str(metadata.get("validated", "")),
            freshness=str(metadata.get("freshness", "")),
            token_cost=int(metadata.get("token_cost") or estimate_tokens(text)),
            utility=max(0.0, min(1.0, utility)),
            layers=parsed.layers,
            body=parsed.body,
            metadata=metadata,
            source_hash=sha256_text(text),
            schema_valid=schema_valid,
        )

    def to_dict(self, include_body: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_body:
            value.pop("body", None)
            value.pop("metadata", None)
        return value


@dataclass(slots=True)
class TaskContext:
    task: str
    cwd: str = ""
    repo: str = ""
    project: str = ""
    module: str = ""
    branch: str = ""
    changed_paths: list[str] = field(default_factory=list)
    requested_paths: list[str] = field(default_factory=list)
    agent: str = "generic"
    session: str = ""
    task_types: list[str] = field(default_factory=list)

    @property
    def task_hash(self) -> str:
        return sha256_text(self.task)[:16]


@dataclass(slots=True)
class RetrievalItem:
    id: str
    path: str
    title: str
    layer: int
    text: str
    score: float
    tokens: int
    reasons: list[str]
    provenance: Any = None
    validation: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrievalManifest:
    task: str
    budget: int
    used_tokens: int
    items: list[RetrievalItem]
    excluded: list[dict[str, Any]]
    context: TaskContext
    retrieval_id: str

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.budget - self.used_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_id": self.retrieval_id,
            "task": self.task,
            "budget": self.budget,
            "used_tokens": self.used_tokens,
            "remaining_tokens": self.remaining_tokens,
            "context": asdict(self.context),
            "items": [item.to_dict() for item in self.items],
            "excluded": self.excluded,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# KB context manifest ({self.used_tokens}/{self.budget} estimated tokens)",
            "",
            f"Task: {self.task}",
            f"Retrieval: `{self.retrieval_id}`",
            "",
        ]
        if not self.items:
            lines.append("No memory cleared the relevance, scope, trust, and token-cost gates.")
            return "\n".join(lines) + "\n"
        for item in self.items:
            lines.extend([
                f"## {item.title} (`{item.id}` · L{item.layer} · {item.tokens} tokens · score {item.score:.3f})",
                "",
                item.text.strip(),
                "",
                f"Why: {'; '.join(item.reasons)}",
                "",
            ])
        return "\n".join(lines).rstrip() + "\n"


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    code: str
    path: str
    message: str
    memory_id: str = ""
    repairable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
