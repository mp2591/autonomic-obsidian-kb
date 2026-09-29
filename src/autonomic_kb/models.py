from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .markdown import ParsedMarkdown, parse_markdown
from .util import estimate_tokens, sha256_text


@lru_cache(maxsize=2)
def metadata_validator(version: int) -> Draft202012Validator:
    schema = json.loads(files("autonomic_kb").joinpath("data/memory.schema.json").read_text(encoding="utf-8"))
    if version == 1:
        schema["required"] = ["id", "title", "type", "scope"]
        schema["properties"]["schema_version"] = {"const": 1}
        schema["properties"]["id"] = {"type": "string", "minLength": 1}
        # Legacy instructions still go through the non-bypassable instruction gate.
        schema.pop("allOf", None)
    return Draft202012Validator(schema)


SCHEMA_VERSION = 2
VALID_TYPES = {
    "agent-instruction",
    "api",
    "architecture",
    "command",
    "convention",
    "decision",
    "dependency",
    "domain",
    "environment",
    "fact",
    "file-map",
    "hypothesis",
    "interface",
    "invariant",
    "known-failure",
    "negative-result",
    "procedure",
    "repository-map",
    "solution",
    "terminology",
    "workflow",
}
VALID_KINDS = {
    "episodic",
    "semantic",
    "procedural",
    "decision",
    "constraint",
    "failure",
    "negative",
    "summary",
    "instruction",
}
VALID_SCOPES = {"global", "user", "repository", "project", "module", "branch", "task", "session"}
VALID_STATUSES = {"active", "inbox", "stale", "conflicted", "superseded", "archived", "quarantined", "retracted"}

TYPE_KIND = {
    "command": "procedural",
    "workflow": "procedural",
    "procedure": "procedural",
    "solution": "procedural",
    "decision": "decision",
    "invariant": "constraint",
    "known-failure": "failure",
    "negative-result": "negative",
    "repository-map": "summary",
    "file-map": "summary",
    "architecture": "summary",
    "agent-instruction": "instruction",
}


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
    schema_version: int = SCHEMA_VERSION
    kind: str = "semantic"
    repo: str = ""
    repository_id: str = ""
    project: str = ""
    module: str = ""
    branch: str = ""
    created: str = ""
    updated: str = ""
    validated: str = ""
    freshness: str = ""
    valid_from: str = ""
    valid_to: str = ""
    as_of_commit: str = ""
    version_range: str = ""
    taint: str = "unknown"
    authorized_instruction: bool = False
    token_cost: int = 0
    utility: float = 0.5
    layers: dict[int, str] = field(default_factory=dict)
    body: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""
    schema_valid: bool = True

    @classmethod
    def from_text(cls, path: str, text: str) -> MemoryRecord:
        parsed: ParsedMarkdown = parse_markdown(text)
        metadata = parsed.metadata
        declared_id = str(metadata.get("id", "")).strip()
        fallback = f"path:{Path(path).with_suffix('').as_posix()}"
        memory_id = declared_id or fallback
        memory_type = str(metadata.get("type", "fact"))
        try:
            schema_version = int(metadata.get("schema_version", 1) or 1)
        except (TypeError, ValueError):
            schema_version = 0
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
        validity = metadata.get("validity", {})
        if not isinstance(validity, dict):
            validity = {}
        required = (
            ("id", "title", "type", "scope")
            if schema_version < 2
            else ("id", "title", "type", "scope", "status", "summary", "confidence", "authority", "updated")
        )
        schema_valid = all(metadata.get(key) not in (None, "") for key in required)
        schema_valid = (
            schema_valid and schema_version in {1, 2} and math.isfinite(confidence) and math.isfinite(utility)
        )
        schema_valid = schema_valid and metadata_validator(schema_version).is_valid(metadata)
        if not math.isfinite(confidence):
            confidence = 0.0
        if not math.isfinite(utility):
            utility = 0.0
        try:
            token_cost = max(0, int(metadata.get("token_cost") or estimate_tokens(text)))
        except (ValueError, TypeError):
            token_cost = estimate_tokens(text)
            schema_valid = False
        return cls(
            id=memory_id,
            path=path,
            title=title,
            type=memory_type,
            scope=str(metadata.get("scope", "repository")),
            status=str(metadata.get("status", "active")),
            summary=summary,
            confidence=max(0.0, min(1.0, confidence)),
            authority=str(metadata.get("authority", "agent")),
            schema_version=schema_version,
            kind=str(metadata.get("kind") or TYPE_KIND.get(memory_type, "semantic")),
            repo=str(metadata.get("repo", "")),
            repository_id=str(metadata.get("repository_id", "")),
            project=str(metadata.get("project", "")),
            module=str(metadata.get("module", "")),
            branch=str(metadata.get("branch", "")),
            created=str(metadata.get("created", "")),
            updated=str(metadata.get("updated", "")),
            validated=str(metadata.get("validated", "")),
            freshness=str(metadata.get("freshness", "")),
            valid_from=str(validity.get("valid_from", "")),
            valid_to=str(validity.get("valid_to", "")),
            as_of_commit=str(validity.get("as_of_commit", "")),
            version_range=str(validity.get("version_range", "")),
            taint=str(metadata.get("taint", "unknown")),
            authorized_instruction=bool(metadata.get("authorized_instruction", False)),
            token_cost=token_cost,
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
    repository_id: str = ""
    project: str = ""
    module: str = ""
    branch: str = ""
    head: str = ""
    merge_base: str = ""
    changed_paths: list[str] = field(default_factory=list)
    requested_paths: list[str] = field(default_factory=list)
    changed_symbols: list[str] = field(default_factory=list)
    agent: str = "generic"
    session: str = ""
    task_types: list[str] = field(default_factory=list)
    risk: str = "normal"
    at: str = ""
    versions: dict[str, str] = field(default_factory=dict)

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
    evidence: list[str] = field(default_factory=list)
    uncertainty: float = 0.0
    route_sources: list[str] = field(default_factory=list)
    revision: str = ""

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
    route: str = "lexical"
    state: str = "sufficient_context"
    trace_id: str = ""
    missing_evidence: list[str] = field(default_factory=list)
    token_count_exact: bool = False
    delivery: str | None = None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.budget - self.used_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_id": self.retrieval_id,
            "trace_id": self.trace_id,
            "task": self.task,
            "budget": self.budget,
            "used_tokens": self.used_tokens,
            "remaining_tokens": self.remaining_tokens,
            "route": self.route,
            "state": self.state,
            "missing_evidence": self.missing_evidence,
            "token_count_exact": self.token_count_exact,
            "context": asdict(self.context),
            "items": [item.to_dict() for item in self.items],
            "excluded": self.excluded,
        }

    def to_markdown(self) -> str:
        if self.delivery is not None:
            return self.delivery
        lines = [f"State: {self.state}", f"Route: {self.route}"]
        if self.missing_evidence:
            lines.append("Missing: " + ", ".join(self.missing_evidence))
        for item in self.items:
            lines.extend(["", f"## {item.title} [{item.id}]", item.text.strip()])
        return "\n".join(lines).rstrip() + "\n"

    def to_agent_dict(self) -> dict[str, Any]:
        """Minimal model-visible JSON; to_dict remains explicit diagnostic output."""
        return {
            "text": self.to_markdown(),
            "authorizes_action": False,
            "retrieval_id": self.retrieval_id,
            "state": self.state,
            "token_count_exact": self.token_count_exact,
        }


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
