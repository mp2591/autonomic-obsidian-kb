from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import KBConfig
from .git_context import inspect_git, recent_commit_summary
from .index import KnowledgeIndex
from .markdown import render_note
from .security import scan_content
from .util import estimate_tokens, sha256_text, slugify, utc_now


@dataclass(slots=True)
class LearningCandidate:
    title: str
    summary: str
    detail: str = ""
    memory_type: str = "fact"
    scope: str = "repository"
    confidence: float = 0.7
    reuse_likelihood: float = 0.6
    rediscovery_cost: float = 0.6
    stability: float = 0.7
    uniqueness: float = 0.7
    token_savings: float = 0.6
    maintenance_cost: float = 0.25
    authority: str = "agent"
    provenance: list[dict[str, Any]] = None  # type: ignore[assignment]
    applies_to: list[str] = None  # type: ignore[assignment]
    relations: dict[str, list[str]] = None  # type: ignore[assignment]
    claim_key: str = ""
    claim_value: Any = None

    def __post_init__(self) -> None:
        self.provenance = self.provenance or []
        self.applies_to = self.applies_to or []
        self.relations = self.relations or {}

    def score(self) -> float:
        benefit = (
            0.19 * self.reuse_likelihood + 0.19 * self.rediscovery_cost + 0.18 * self.confidence
            + 0.13 * self.stability + 0.11 * self.uniqueness + 0.20 * self.token_savings
        )
        return max(0.0, min(1.0, benefit - 0.08 * self.maintenance_cost))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LearningCandidate":
        aliases = {
            "type": "memory_type",
            "body": "detail",
            "source": "provenance",
        }
        normalized = {aliases.get(key, key): item for key, item in value.items()}
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in normalized.items() if key in allowed})


class Learner:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def remember(self, candidate: LearningCandidate, force: bool = False) -> dict[str, Any]:
        score = candidate.score()
        findings = scan_content(candidate.title + "\n" + candidate.summary + "\n" + candidate.detail)
        status = "quarantined" if findings else "active" if force or score >= self.config.promotion_threshold else "inbox"
        directory = {
            "quarantined": self.config.quarantine_dir,
            "inbox": self.config.inbox_dir,
        }.get(status, self._directory_for_type(candidate.memory_type))
        fingerprint = sha256_text(candidate.scope + "\n" + candidate.memory_type + "\n" + candidate.summary.lower())[:8]
        memory_id = f"kb:{candidate.scope}:{candidate.memory_type}:{slugify(candidate.title, 48)}-{fingerprint}"
        existing = self.index.get(memory_id)
        if existing:
            return {"action": "duplicate", "memory_id": memory_id, "path": existing["path"], "score": score}
        git = inspect_git(self.config.repo or self.config.vault)
        now = utc_now()
        token_cost = estimate_tokens(candidate.summary + "\n" + candidate.detail)
        metadata: dict[str, Any] = {
            "id": memory_id,
            "title": candidate.title,
            "type": candidate.memory_type,
            "scope": candidate.scope,
            "repo": Path(git.root).name if git.root else "",
            "branch": git.branch if candidate.scope == "branch" else "",
            "status": status,
            "summary": candidate.summary,
            "confidence": round(max(0.0, min(1.0, candidate.confidence)), 3),
            "authority": candidate.authority,
            "created": now,
            "updated": now,
            "validated": "",
            "freshness": "unvalidated",
            "token_cost": token_cost,
            "utility": round(score, 3),
            "applies_to": candidate.applies_to,
            "agents": [],
            "provenance": candidate.provenance,
            "relations": candidate.relations,
            "invalidation": {"paths": candidate.applies_to, "branch": bool(candidate.scope == "branch")},
        }
        if candidate.claim_key:
            metadata["claim_key"] = candidate.claim_key
            metadata["claim_value"] = candidate.claim_value
        layers = {
            0: candidate.summary[:140],
            1: candidate.summary,
            2: candidate.detail or candidate.summary,
            3: candidate.detail,
            4: "\n".join(f"- {json.dumps(source, sort_keys=True)}" for source in candidate.provenance),
        }
        filename = f"{slugify(candidate.title)}-{fingerprint}.md"
        relative = Path(directory) / filename
        absolute = self.config.vault / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(render_note(metadata, layers), encoding="utf-8")
        self.index.index_vault(force=True)
        self.index.events.emit(
            "memory.created", memory_id=memory_id, path=relative.as_posix(), status=status,
            score=score, security_findings=[finding.to_dict() for finding in findings],
        )
        return {
            "action": "created",
            "memory_id": memory_id,
            "path": relative.as_posix(),
            "status": status,
            "candidate_score": round(score, 3),
            "security_findings": [finding.to_dict() for finding in findings],
        }

    def learn_json(self, path: str | Path) -> list[dict[str, Any]]:
        source = Path(path)
        raw = source.read_text(encoding="utf-8")
        values: Iterable[dict[str, Any]]
        if source.suffix == ".jsonl":
            values = [json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            parsed = json.loads(raw)
            values = parsed if isinstance(parsed, list) else [parsed]
        return [self.remember(LearningCandidate.from_dict(value)) for value in values]

    def learn_git(self) -> dict[str, Any]:
        git = inspect_git(self.config.repo or self.config.vault)
        if not git.root:
            return {"action": "skipped", "reason": "not inside a Git repository"}
        commits = recent_commit_summary(git.root, 8)
        changed = git.changed_paths[:30]
        detail = "Recent commits:\n\n```text\n" + commits + "\n```\n\nActive changed paths:\n" + "\n".join(f"- `{path}`" for path in changed)
        candidate = LearningCandidate(
            title="Recent repository change map",
            summary="A short-lived map of recent commits and currently changed paths for agent orientation.",
            detail=detail,
            memory_type="repository-map",
            scope="branch" if git.branch else "repository",
            confidence=0.75,
            reuse_likelihood=0.45,
            rediscovery_cost=0.5,
            stability=0.25,
            uniqueness=0.6,
            token_savings=0.65,
            maintenance_cost=0.7,
            provenance=[{"kind": "git", "head": git.head, "branch": git.branch}],
            applies_to=changed,
        )
        return self.remember(candidate)

    @staticmethod
    def _directory_for_type(memory_type: str) -> str:
        mapping = {
            "architecture": "10-architecture", "repository-map": "11-maps", "file-map": "11-maps",
            "decision": "12-decisions", "command": "20-commands", "workflow": "21-workflows",
            "known-failure": "30-debugging", "solution": "30-debugging", "dependency": "40-dependencies",
            "api": "50-interfaces", "interface": "50-interfaces", "convention": "60-conventions",
            "agent-instruction": "70-agent-instructions", "domain": "80-domain", "terminology": "80-domain",
            "hypothesis": "00-inbox",
        }
        return mapping.get(memory_type, "81-facts")
