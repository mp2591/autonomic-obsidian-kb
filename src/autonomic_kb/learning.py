from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import KBConfig
from .episodes import Episode, EpisodeStore
from .evidence import EvidenceStore, OperationLedger
from .git_context import inspect_git, recent_commit_summary
from .index import KnowledgeIndex
from .markdown import render_note
from .security import combine_taint, instruction_authorized, scan_content
from .util import estimate_tokens, jaccard, sha256_text, slugify, utc_now


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
    provenance: list[dict[str, Any]] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=list)
    relations: dict[str, list[str]] = field(default_factory=dict)
    claim_key: str = ""
    claim_value: Any = None
    taint: str = "agent"
    authorized_instruction: bool = False
    evidence: list[str] = field(default_factory=list)
    validators: list[dict[str, Any]] = field(default_factory=list)
    valid_from: str = ""
    valid_to: str = ""
    version_range: str = ""

    def score(self) -> float:
        benefit = (
            0.17 * self.reuse_likelihood + 0.18 * self.rediscovery_cost + 0.16 * self.confidence +
            0.12 * self.stability + 0.10 * self.uniqueness + 0.19 * self.token_savings
        )
        evidence_bonus = min(0.08, 0.02 * len(self.evidence) + 0.02 * len(self.validators))
        return max(0.0, min(1.0, benefit + evidence_bonus - 0.08 * self.maintenance_cost))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LearningCandidate":
        aliases = {"type": "memory_type", "body": "detail", "source": "provenance"}
        normalized = {aliases.get(key, key): item for key, item in value.items()}
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in normalized.items() if key in allowed})


class Learner:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.evidence = EvidenceStore(config)
        self.operations = OperationLedger(config)
        self.episodes = EpisodeStore(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def _near_duplicate(self, candidate: LearningCandidate) -> dict[str, Any] | None:
        for note in self.index.all_notes({"active", "inbox", "stale", "conflicted"}):
            if note.get("scope") != candidate.scope or note.get("type") != candidate.memory_type:
                continue
            similarity = jaccard(candidate.summary, str(note.get("summary", "")))
            metadata = note.get("metadata", {})
            if candidate.claim_key and metadata.get("claim_key") == candidate.claim_key:
                if metadata.get("claim_value") == candidate.claim_value:
                    return {"note": note, "kind": "same-claim", "similarity": max(similarity, 0.95)}
                return {"note": note, "kind": "conflict", "similarity": similarity}
            if similarity >= 0.94:
                return {"note": note, "kind": "paraphrase", "similarity": similarity}
        return None

    def remember(self, candidate: LearningCandidate, force: bool = False) -> dict[str, Any]:
        self.index.index_vault()
        score = candidate.score()
        text = candidate.title + "\n" + candidate.summary + "\n" + candidate.detail
        findings = scan_content(text)
        authorized, auth_reason = instruction_authorized(
            candidate.memory_type, candidate.authority, candidate.authorized_instruction, candidate.taint,
        )
        duplicate = self._near_duplicate(candidate)
        if duplicate and duplicate["kind"] != "conflict":
            note = duplicate["note"]
            return {"action": "duplicate", "memory_id": note["declared_id"] or note["id"], "path": note["path"],
                    "score": score, "duplicate_kind": duplicate["kind"], "similarity": round(duplicate["similarity"], 3)}
        if findings:
            status = "quarantined"
        elif candidate.memory_type == "agent-instruction" and not authorized:
            status = "inbox"
        elif duplicate and duplicate["kind"] == "conflict":
            status = "conflicted"
        else:
            status = "active" if force or score >= self.config.promotion_threshold else "inbox"
        directory = {"quarantined": self.config.quarantine_dir, "inbox": self.config.inbox_dir,
                     "conflicted": self.config.inbox_dir}.get(status, self._directory_for_type(candidate.memory_type))
        fingerprint = sha256_text(candidate.scope + "\n" + candidate.memory_type + "\n" + candidate.summary.lower())[:8]
        memory_id = f"kb:{candidate.scope}:{candidate.memory_type}:{slugify(candidate.title, 48)}-{fingerprint}"
        existing = self.index.get(memory_id)
        if existing:
            return {"action": "duplicate", "memory_id": memory_id, "path": existing["path"], "score": score}
        git = inspect_git(self.config.repo or self.config.vault)
        now = utc_now()
        evidence_ids = list(candidate.evidence)
        if not evidence_ids:
            record = self.evidence.put(
                "candidate-observation", text, subject=memory_id, repository_id=git.repository_id,
                commit=git.head, producer=candidate.authority, metadata={"provenance": candidate.provenance},
            )
            evidence_ids.append(record.evidence_id)
        token_cost = estimate_tokens(candidate.summary + "\n" + candidate.detail)
        metadata: dict[str, Any] = {
            "schema_version": 2, "id": memory_id, "title": candidate.title, "type": candidate.memory_type,
            "scope": candidate.scope, "repo": Path(git.root).name if git.root else "",
            "repository_id": git.repository_id, "branch": git.branch if candidate.scope == "branch" else "",
            "status": status, "summary": candidate.summary, "confidence": round(max(0.0, min(1.0, candidate.confidence)), 3),
            "authority": candidate.authority, "taint": candidate.taint, "authorized_instruction": candidate.authorized_instruction,
            "created": now, "updated": now, "validated": "", "freshness": "unvalidated",
            "validity": {"valid_from": candidate.valid_from, "valid_to": candidate.valid_to,
                         "as_of_commit": git.head if candidate.scope in {"branch", "module"} else "",
                         "version_range": candidate.version_range},
            "token_cost": token_cost, "utility": round(score, 3), "applies_to": candidate.applies_to,
            "agents": [], "provenance": candidate.provenance, "evidence": evidence_ids,
            "validators": candidate.validators, "relations": candidate.relations,
            "invalidation": {"paths": candidate.applies_to, "branch": bool(candidate.scope == "branch")},
        }
        if candidate.claim_key:
            metadata["claim_key"] = candidate.claim_key
            metadata["claim_value"] = candidate.claim_value
        if duplicate and duplicate["kind"] == "conflict":
            metadata["relations"] = dict(metadata["relations"])
            metadata["relations"].setdefault("contradicts", []).append(duplicate["note"]["declared_id"] or duplicate["note"]["id"])
        layers = {
            0: candidate.summary[:140], 1: candidate.summary, 2: candidate.detail or candidate.summary,
            3: candidate.detail,
            4: "\n".join([*(f"- evidence: `{value}`" for value in evidence_ids),
                              *(f"- source: `{json.dumps(source, sort_keys=True)}`" for source in candidate.provenance)]),
        }
        filename = f"{slugify(candidate.title)}-{fingerprint}.md"
        relative = Path(directory) / filename
        absolute = self.config.vault / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_note(metadata, layers)
        absolute.write_text(rendered, encoding="utf-8")
        self.operations.append("ADD", memory_id, actor=candidate.authority, basis=evidence_ids,
                               new_digest=sha256_text(rendered), reason="candidate promotion", confidence_after=candidate.confidence,
                               metadata={"status": status, "authorization": auth_reason})
        self.index.index_vault(force=True)
        self.index.events.emit("memory.created", memory_id=memory_id, path=relative.as_posix(), status=status,
                               score=score, security_findings=[finding.to_dict() for finding in findings])
        return {"action": "created", "memory_id": memory_id, "path": relative.as_posix(), "status": status,
                "candidate_score": round(score, 3), "evidence": evidence_ids, "authorization": auth_reason,
                "security_findings": [finding.to_dict() for finding in findings]}

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
        return self.remember(LearningCandidate(
            title="Recent repository change map", summary="A short-lived map of recent commits and currently changed paths for agent orientation.",
            detail=detail, memory_type="repository-map", scope="branch" if git.branch else "repository",
            confidence=0.75, reuse_likelihood=0.45, rediscovery_cost=0.5, stability=0.25, uniqueness=0.6,
            token_savings=0.65, maintenance_cost=0.7, provenance=[{"kind": "git", "head": git.head, "branch": git.branch}],
            applies_to=changed, taint="derived", authority="derived",
        ))

    def capture_episode(self, task: str, **values: Any) -> Episode:
        git = inspect_git(self.config.repo or self.config.vault)
        values.setdefault("repository_id", git.repository_id)
        values.setdefault("branch", git.branch)
        values.setdefault("commit", git.head)
        return self.episodes.capture(task, **values)

    def consolidate_episode(self, episode: Episode, force: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        candidates: list[LearningCandidate] = []
        for observation in episode.observations:
            if force or self.episodes.recurrence(observation) >= self.config.recurrence_threshold:
                candidates.append(LearningCandidate(
                    title=observation[:80], summary=observation, memory_type="fact", scope="repository",
                    confidence=0.7, reuse_likelihood=0.7, rediscovery_cost=0.65, stability=0.55, token_savings=0.7,
                    authority="derived", taint="derived", evidence=list(episode.evidence),
                    provenance=[{"kind": "episode", "id": episode.episode_id}],
                ))
        for failed in episode.failed_hypotheses:
            if force or self.episodes.recurrence(failed) >= self.config.recurrence_threshold:
                candidates.append(LearningCandidate(
                    title=f"Negative result: {failed[:60]}", summary=failed, memory_type="negative-result", scope="repository",
                    confidence=0.75, reuse_likelihood=0.55, rediscovery_cost=0.75, stability=0.45, token_savings=0.8,
                    authority="derived", taint="derived", evidence=list(episode.evidence),
                    provenance=[{"kind": "episode", "id": episode.episode_id}],
                ))
        for candidate in candidates:
            results.append(self.remember(candidate))
        return results

    @staticmethod
    def _directory_for_type(memory_type: str) -> str:
        mapping = {
            "architecture": "10-architecture", "repository-map": "11-maps", "file-map": "11-maps", "decision": "12-decisions",
            "command": "20-commands", "workflow": "21-workflows", "procedure": "21-workflows",
            "known-failure": "30-debugging", "solution": "30-debugging", "negative-result": "31-negative-results",
            "dependency": "40-dependencies", "api": "50-interfaces", "interface": "50-interfaces",
            "convention": "60-conventions", "agent-instruction": "70-agent-instructions", "domain": "80-domain",
            "terminology": "80-domain", "hypothesis": "00-inbox",
        }
        return mapping.get(memory_type, "81-facts")
