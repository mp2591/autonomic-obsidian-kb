from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_WORD_RE = re.compile(r"[A-Za-z0-9_./:+-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def age_days(value: str | None) -> float:
    parsed = parse_time(value)
    if not parsed:
        return 10_000.0
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86_400)


def slugify(value: str, max_length: int = 72) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (value or "memory")[:max_length].rstrip("-")


def estimate_tokens(text: str) -> int:
    """Conservative model-independent token estimate.

    English prose is usually near four characters per token, while source code
    and metadata are denser. The mixed estimator intentionally rounds upward.
    """
    if not text:
        return 0
    words = len(_WORD_RE.findall(text))
    chars = len(text)
    return max(1, int(max(chars / 3.7, words * 1.25) + 0.999))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def terms(text: str, minimum: int = 2) -> list[str]:
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from", "how",
        "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "use",
        "we", "what", "when", "where", "which", "with", "you", "your",
    }
    seen: set[str] = set()
    result: list[str] = []
    for token in _WORD_RE.findall(text.lower()):
        token = token.strip("./:+-")
        if len(token) < minimum or token in stop or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
