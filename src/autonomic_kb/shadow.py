from __future__ import annotations

from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .retrieval import Retriever


class ShadowEvaluator:
    """Compare retrieval policies without injecting their result into an agent."""

    def __init__(self, config: KBConfig):
        self.config = config

    def compare(
        self, task: str, routes: list[str], budget: int = 600, paths: list[str] | None = None
    ) -> dict[str, Any]:
        outputs = {}
        with KnowledgeIndex(self.config) as index:
            retriever = Retriever(self.config, index)
            for route in routes:
                manifest = retriever.retrieve(
                    task, budget=budget, paths=paths or [], agent="shadow", route_override=route, record=False
                )
                outputs[route] = {
                    "state": manifest.state,
                    "used_tokens": manifest.used_tokens,
                    "ids": [item.id for item in manifest.items],
                    "scores": [item.score for item in manifest.items],
                }
        return {"task": task, "budget": budget, "routes": outputs}
