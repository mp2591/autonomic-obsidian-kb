from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .util import stable_json, utc_now

DEFAULT_WEIGHTS = {
    "lexical": 0.18, "exact": 0.16, "rrf": 0.12, "type": 0.09, "path": 0.08,
    "confidence": 0.06, "authority": 0.05, "validation": 0.05, "freshness": 0.03,
    "utility": 0.03, "evidence": 0.03, "query_overlap": 0.02,
}
NONNEGATIVE = {"lexical", "exact", "rrf", "type", "path", "confidence", "authority", "validation", "freshness", "evidence", "query_overlap"}


@dataclass(slots=True)
class RankPolicy:
    version: str = "rank-v2.1"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    intercept: float = -0.1
    trained_examples: int = 0
    trained_at: str = ""

    @classmethod
    def load(cls, config: KBConfig) -> "RankPolicy":
        path = config.runtime_dir / "rank-policy.json"
        if not path.exists(): return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8")); return cls(**data)
        except (json.JSONDecodeError, TypeError, ValueError): return cls()

    def save(self, config: KBConfig) -> None:
        (config.runtime_dir / "rank-policy.json").write_text(stable_json({
            "version": self.version, "weights": self.weights, "intercept": self.intercept,
            "trained_examples": self.trained_examples, "trained_at": self.trained_at,
        }) + "\n", encoding="utf-8")

    def probability(self, features: dict[str, float]) -> float:
        z = self.intercept + sum(self.weights.get(key, 0.0) * float(features.get(key, 0.0)) for key in self.weights)
        if z >= 0: return 1.0 / (1.0 + math.exp(-min(z, 40)))
        exp = math.exp(max(z, -40)); return exp / (1 + exp)


def apply_feedback_labels(config: KBConfig, index: KnowledgeIndex) -> int:
    if not config.feedback_path.exists(): return 0
    mapping = {"helpful": 1.0, "expanded": 0.8, "irrelevant": 0.0, "incorrect": 0.0, "stale": 0.0,
               "caused-search": 0.15, "caused-correction": 0.0, "caused-failure": 0.0, "unsafe": 0.0}
    updated = 0
    for line in config.feedback_path.read_text(encoding="utf-8").splitlines():
        try: row = json.loads(line)
        except json.JSONDecodeError: continue
        if row.get("kind") not in mapping: continue
        index.label_rank_example(str(row.get("retrieval_id", "")), str(row.get("memory_id", "")), mapping[row["kind"]]); updated += 1
    return updated


def train_rank_policy(config: KBConfig, index: KnowledgeIndex, *, epochs: int = 120, learning_rate: float = 0.08) -> dict[str, Any]:
    apply_feedback_labels(config, index)
    rows = index.connection.execute("SELECT feature_json,label FROM rank_examples WHERE label IS NOT NULL").fetchall()
    if len(rows) < 4:
        return {"trained": False, "reason": "at least 4 labeled retrieval examples are required", "examples": len(rows)}
    examples = []
    for row in rows:
        try: features = json.loads(row["feature_json"])
        except json.JSONDecodeError: continue
        examples.append((features, float(row["label"])))
    policy = RankPolicy.load(config)
    weights = dict(policy.weights); intercept = policy.intercept
    for _ in range(max(1, epochs)):
        grad = {key: 0.0 for key in weights}; grad_i = 0.0
        for features, label in examples:
            z = intercept + sum(weights[key] * float(features.get(key, 0.0)) for key in weights)
            pred = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            error = pred - label; grad_i += error
            for key in weights: grad[key] += error * float(features.get(key, 0.0))
        n = len(examples); intercept -= learning_rate * grad_i / n
        for key in weights:
            weights[key] -= learning_rate * grad[key] / n
            if key in NONNEGATIVE: weights[key] = max(0.0, weights[key])
            weights[key] = max(-2.0, min(2.0, weights[key]))
    policy.weights = weights; policy.intercept = intercept; policy.trained_examples = len(examples); policy.trained_at = utc_now(); policy.version = "rank-v2.1-learned"
    policy.save(config)
    return {"trained": True, "examples": len(examples), "version": policy.version, "weights": weights, "intercept": intercept}
