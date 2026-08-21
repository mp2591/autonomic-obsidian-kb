from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .retrieval import Retriever
from .util import estimate_tokens, utc_now


@dataclass(slots=True)
class BenchmarkCaseResult:
    name: str
    task: str
    budget: int
    baseline_tokens: int
    retrieval_tokens: int
    injected_tokens: int
    maintenance_tokens: int
    assisted_tokens: int
    net_savings: int
    reduction: float
    expected_ids: list[str]
    retrieved_ids: list[str]
    precision: float
    expected_coverage: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkRunner:
    def __init__(self, config: KBConfig):
        self.config = config

    def run(self, tasks_path: str | Path) -> dict[str, Any]:
        tasks = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
        with KnowledgeIndex(self.config) as index:
            index.rebuild()
            retriever = Retriever(self.config, index)
            results: list[BenchmarkCaseResult] = []
            corpus = index.all_notes({"active", "inbox", "stale", "conflicted"})
            total_corpus_tokens = sum(max(1, int(note["token_cost"])) for note in corpus)
            for case in tasks:
                budget = int(case.get("budget", self.config.default_budget))
                manifest = retriever.retrieve(
                    str(case["task"]), budget=budget, paths=list(case.get("paths", [])),
                    agent="benchmark",
                )
                expected = list(case.get("expected_ids", []))
                retrieved = [item.id for item in manifest.items]
                hits = len(set(expected) & set(retrieved))
                precision = hits / max(1, len(retrieved))
                coverage = hits / max(1, len(expected))
                baseline_paths = list(case.get("baseline_paths", []))
                if baseline_paths:
                    baseline_tokens = sum(
                        max(1, int(note["token_cost"])) for note in corpus
                        if any(note["path"].startswith(prefix) for prefix in baseline_paths)
                    )
                    baseline_tokens = max(baseline_tokens, total_corpus_tokens // 2)
                else:
                    baseline_tokens = total_corpus_tokens
                retrieval_tokens = max(24, estimate_tokens(case["task"]) + len(corpus) * 2)
                injected_tokens = manifest.used_tokens
                maintenance_tokens = int(case.get("maintenance_tokens", 18))
                assisted = retrieval_tokens + injected_tokens + maintenance_tokens
                savings = baseline_tokens - assisted
                reduction = savings / baseline_tokens if baseline_tokens else 0.0
                results.append(BenchmarkCaseResult(
                    name=str(case.get("name", case["task"][:40])), task=str(case["task"]), budget=budget,
                    baseline_tokens=baseline_tokens, retrieval_tokens=retrieval_tokens,
                    injected_tokens=injected_tokens, maintenance_tokens=maintenance_tokens,
                    assisted_tokens=assisted, net_savings=savings, reduction=round(reduction, 4),
                    expected_ids=expected, retrieved_ids=retrieved,
                    precision=round(precision, 4), expected_coverage=round(coverage, 4),
                ))
        cases = [result.to_dict() for result in results]
        baseline = sum(result.baseline_tokens for result in results)
        assisted = sum(result.assisted_tokens for result in results)
        summary = {
            "created_at": utc_now(),
            "cases": len(results),
            "baseline_tokens": baseline,
            "assisted_tokens": assisted,
            "net_savings": baseline - assisted,
            "net_reduction": round((baseline - assisted) / baseline, 4) if baseline else 0.0,
            "mean_precision": round(sum(result.precision for result in results) / max(1, len(results)), 4),
            "mean_expected_coverage": round(sum(result.expected_coverage for result in results) / max(1, len(results)), 4),
            "method": "deterministic proxy benchmark; token counts are model-independent estimates",
        }
        return {"summary": summary, "cases": cases}
