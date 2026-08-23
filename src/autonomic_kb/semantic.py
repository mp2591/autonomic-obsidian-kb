from __future__ import annotations

import json
import math
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .util import sha256_text


class LocalEmbeddingBackend:
    """Optional local dense candidate generator.

    The backend is intentionally absent from the default route. It uses sentence-transformers
    only when installed and explicitly enabled, and persists a rebuildable cache under `.kb`.
    Scope/trust/temporal gates remain downstream and authoritative.
    """

    def __init__(self, config: KBConfig, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.config = config
        self.model_name = model_name
        self.cache_path = config.runtime_dir / f"embeddings-{sha256_text(model_name)[:10]}.json"
        self._model = None

    @property
    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401

            return True
        except ImportError:
            return False

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / max(na * nb, 1e-12)

    def search(self, index: KnowledgeIndex, query: str, limit: int = 50) -> list[dict[str, Any]]:
        if not self.available:
            return []
        notes = index.all_notes({"active", "stale", "conflicted"})
        cache: dict[str, Any] = {}
        if self.cache_path.exists():
            try:
                cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache = {}
        model = self._load_model()
        missing = []
        texts = []
        for note in notes:
            digest = note["source_hash"]
            key = f"{note['id']}:{digest}"
            if key not in cache:
                missing.append((key, note))
                texts.append(f"{note['title']}\n{note['summary']}\n{note['l2']}")
        if texts:
            vectors = model.encode(texts, normalize_embeddings=True).tolist()
            for (key, _), vector in zip(missing, vectors, strict=True):
                cache[key] = vector
            self.cache_path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        query_vector = model.encode([query], normalize_embeddings=True)[0].tolist()
        scored = []
        for note in notes:
            key = f"{note['id']}:{note['source_hash']}"
            vector = cache.get(key)
            if vector:
                scored.append((self._cosine(query_vector, vector), note))
        scored.sort(key=lambda value: (-value[0], value[1]["id"]))
        result = []
        for rank, (score, note) in enumerate(scored[:limit], 1):
            item = dict(note)
            item["dense"] = max(0.0, min(1.0, (score + 1) / 2))
            item["rank_dense"] = rank
            result.append(item)
        return result
