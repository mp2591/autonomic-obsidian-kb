from __future__ import annotations

import re
from typing import Any

from .util import estimate_tokens, terms

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_CODE = re.compile(r"`[^`]+`|```.*?```", re.DOTALL)


def compile_task_view(
    note: dict[str, Any], task: str, preferred_layer: int, max_tokens: int, profile: str = "generic"
) -> tuple[int, str]:
    """Compile a compact task-specific view while preserving exact code/commands."""
    for layer in range(preferred_layer, -1, -1):
        text = str(note.get(f"l{layer}") or "").strip()
        if not text:
            continue
        if estimate_tokens(text, profile) <= max_tokens:
            return layer, text
        task_terms = set(terms(task))
        chunks = [chunk.strip() for chunk in _SENTENCE.split(text) if chunk.strip()]
        scored = []
        for position, chunk in enumerate(chunks):
            chunk_terms = set(terms(chunk))
            overlap = len(task_terms & chunk_terms) / max(1, len(task_terms))
            exact_bonus = 0.35 if _CODE.search(chunk) else 0.0
            identifier_bonus = (
                0.15 if any(token in chunk for token in task_terms if any(c in token for c in "_./:")) else 0.0
            )
            lead_bonus = max(0.0, 0.08 - position * 0.005)
            scored.append((overlap + exact_bonus + identifier_bonus + lead_bonus, position, chunk))
        selected = []
        used = 0
        for _, position, chunk in sorted(scored, key=lambda item: (-item[0], item[1])):
            cost = estimate_tokens(chunk, profile)
            if cost + used > max_tokens:
                continue
            selected.append((position, chunk))
            used += cost
            if used >= max_tokens * 0.8:
                break
        if selected:
            selected.sort()
            compiled = "\n\n".join(chunk for _, chunk in selected)
            if compiled.strip():
                return layer, compiled.strip()
    fallback = str(note.get("summary") or note.get("title") or "").strip()
    return 1, fallback
