from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_WORD_RE = re.compile(r"[A-Za-z0-9_./:+-]+")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def age_days(value: str | None) -> float:
    parsed = parse_time(value)
    if not parsed:
        return 10_000.0
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds() / 86_400)


def is_time_active(valid_from: str = "", valid_to: str = "", *, at: datetime | None = None) -> bool:
    at = at or datetime.now(UTC)
    start = parse_time(valid_from)
    end = parse_time(valid_to)
    return (not start or start <= at) and (not end or at < end)


def slugify(value: str, max_length: int = 72) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (value or "memory")[:max_length].rstrip("-")


def estimate_tokens(text: str, profile: str = "generic") -> int:
    """Deterministic tokenizer proxy with per-agent profiles.

    The core remains dependency-free. Deployments can replace this adapter with an
    exact tokenizer, while CI remains deterministic and comparable.
    """
    if not text:
        return 0
    ratios = {
        "generic": 3.7,
        "codex": 3.45,
        "claude": 3.65,
        "gemini": 3.6,
        "compact": 3.9,
    }
    chars_per_token = ratios.get(profile.lower(), ratios["generic"])
    words = len(_WORD_RE.findall(text))
    return max(1, math.ceil(max(len(text) / chars_per_token, words * 1.23)))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def terms(text: str, minimum: int = 2) -> list[str]:
    stop = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "use",
        "we",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "your",
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


def normalized_text(value: str) -> str:
    return " ".join(terms(value, minimum=1))


def jaccard(a: str, b: str) -> float:
    left, right = set(terms(a)), set(terms(b))
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))
