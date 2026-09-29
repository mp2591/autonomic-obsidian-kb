"""Conservative release policy: fixed ranking, no learned-policy activation.

This build does not load or train a replacement ranking policy. Feedback remains
available for offline evaluation; calibration requests return an explicit disabled
result rather than silently changing retrieval behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex

DEFAULT_WEIGHTS = {
    "lexical": 0.18,
    "exact": 0.16,
    "rrf": 0.12,
    "type": 0.09,
    "path": 0.08,
    "confidence": 0.06,
    "authority": 0.05,
    "validation": 0.05,
    "freshness": 0.03,
    "utility": 0.03,
    "evidence": 0.03,
    "query_overlap": 0.02,
}

@dataclass(slots=True)
class RankPolicy:
    version: str = "rank-v3-static"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    intercept: float = -0.1
    trained_examples: int = 0
    trained_at: str = ""

    @classmethod
    def load(cls, config: KBConfig) -> RankPolicy:
        # Persisted learned-policy files cannot activate in this release.
        return cls()


def train_rank_policy(
    config: KBConfig, index: KnowledgeIndex, *, epochs: int = 120, learning_rate: float = 0.08, promote: bool = False
) -> dict[str, Any]:
    return {
        "trained": False,
        "promoted": False,
        "version": "rank-v3-static",
        "reason": "Learned ranking is disabled in this release; no policy file was loaded or changed.",
        "examples": 0,
    }
