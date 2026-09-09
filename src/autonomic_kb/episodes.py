from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import KBConfig
from .evidence import EvidenceStore
from .security import scan_content
from .util import atomic_write, sha256_text, utc_now


@dataclass(slots=True)
class Episode:
    episode_id: str
    task: str
    repository_id: str = ""
    branch: str = ""
    commit: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    failed_hypotheses: list[str] = field(default_factory=list)
    successful_actions: list[str] = field(default_factory=list)
    outcome: str = "unknown"
    correction: str = ""
    created_at: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EpisodeStore:
    def __init__(self, config: KBConfig):
        self.config = config
        self.evidence = EvidenceStore(config)

    def capture(self, task: str, **values: Any) -> Episode:
        raw_candidate = json.dumps({"task": task, **values}, sort_keys=True, ensure_ascii=False, default=str)
        if any(item.category == "secret" for item in scan_content(raw_candidate)):
            raise ValueError("refusing to persist secret-bearing episode")
        episode = Episode(episode_id=f"episode:{uuid.uuid4().hex}", task=task, created_at=utc_now(), **values)
        raw = json.dumps(episode.to_dict(), sort_keys=True, ensure_ascii=False)
        evidence = self.evidence.put(
            "episode",
            raw,
            subject=episode.episode_id,
            repository_id=episode.repository_id,
            commit=episode.commit,
            producer="episode-store",
        )
        episode.evidence.append(evidence.evidence_id)
        destination = self.config.episode_dir / episode.created_at[:10] / f"{episode.episode_id.split(':', 1)[1]}.json"
        atomic_write(destination, json.dumps(episode.to_dict(), indent=2, sort_keys=True) + "\n")
        return episode

    def all(self) -> list[Episode]:
        result: list[Episode] = []
        for path in sorted(self.config.episode_dir.rglob("*.json")):
            try:
                result.append(Episode(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return result

    def recurrence(self, signature: str) -> int:
        target = sha256_text(signature.lower())[:16]
        count = 0
        for episode in self.all():
            corpus = "\n".join(
                [episode.task, *episode.observations, *episode.failed_hypotheses, *episode.successful_actions]
            )
            if sha256_text(corpus.lower())[:16] == target or signature.lower() in corpus.lower():
                count += 1
        return count
