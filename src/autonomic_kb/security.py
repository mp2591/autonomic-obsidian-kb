from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "generic-secret": re.compile(
        # An optional closing quote after the key also covers dict and JSON renderings.
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{12,}"
    ),
}
_INJECTION_PATTERNS = {
    "instruction-override": re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override).{0,32}"
        r"(?:previous|prior|system|developer|safety|policy) instructions?\b"
    ),
    "prompt-exfiltration": re.compile(
        r"(?i)\b(?:reveal|print|dump|show|exfiltrate|send).{0,45}"
        r"(?:system prompt|developer message|secrets?|credentials?|tokens?)\b"
    ),
    "role-forgery": re.compile(r"(?im)^\s*(?:system|developer|assistant|tool)\s*:\s*"),
    "tool-coercion": re.compile(
        r"(?i)\b(?:must|always|immediately|silently) "
        r"(?:run|call|invoke|execute).{0,40}(?:shell|tool|command|curl|powershell|bash)\b"
    ),
    "memory-persistence": re.compile(
        r"(?i)\b(?:remember|persist|store|write).{0,40}(?:instruction|rule|secret|credential|prompt).{0,30}(?:future|later|subsequent|permanent)"
    ),
}

TRUSTED_AUTHORITIES = {"source-of-truth", "authoritative", "verified", "user-corrected"}
TAINT_ORDER = {"trusted": 0, "derived": 1, "external": 2, "agent": 2, "untrusted": 3, "hostile": 4, "unknown": 2}


@dataclass(slots=True)
class SecurityFinding:
    category: str
    pattern: str
    excerpt: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "pattern": self.pattern, "excerpt": self.excerpt, "severity": self.severity}


def scan_content(text: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for name, pattern in _SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            excerpt = text[max(0, match.start() - 16) : match.end() + 16]
            findings.append(SecurityFinding("secret", name, _redact(excerpt), "critical"))
    for name, pattern in _INJECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            excerpt = text[max(0, match.start() - 24) : match.end() + 50]
            findings.append(SecurityFinding("prompt-injection", name, excerpt.replace("\n", " ")[:200], "high"))
    entropy = _high_entropy_assignments(text)
    findings.extend(entropy)
    return findings


def _high_entropy_assignments(text: str) -> list[SecurityFinding]:
    result: list[SecurityFinding] = []
    pattern = re.compile(r"(?i)\b(?:key|token|secret|password)\w*['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9+/=_-]{20,})")
    for match in pattern.finditer(text):
        value = match.group(1)
        probs = [value.count(char) / len(value) for char in set(value)]
        entropy = -sum(p * math.log2(p) for p in probs)
        if entropy >= 3.5:
            result.append(SecurityFinding("secret", "high-entropy-assignment", "[REDACTED]", "critical"))
    return result


_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |PGP )?)PRIVATE KEY-----"
    r".*?(?:-----END (?P=kind)PRIVATE KEY-----|\Z)",
    re.S,
)
_KEYED_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_./+\-=]{12,})"
)
_KEYED_ENTROPY = re.compile(r"(?i)(\b(?:key|token|secret|password)\w*['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9+/=_-]{20,})")


def _entropy(value: str) -> float:
    return -sum((value.count(char) / len(value)) * math.log2(value.count(char) / len(value)) for char in set(value))


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace every secret the scanner recognizes; keep key names so the note stays reviewable."""
    count = 0

    def replace(match: re.Match[str], keep_key: bool = True, entropy: bool = False) -> str:
        nonlocal count
        if entropy and _entropy(match.group(2)) < 3.5:
            return match.group(0)
        count += 1
        return (match.group(1) if keep_key else "") + "[REDACTED]"

    text = _PRIVATE_KEY_BLOCK.sub(lambda match: replace(match, keep_key=False), text)
    for name in ("github-token", "aws-access-key"):
        text = _SECRET_PATTERNS[name].sub(lambda match: replace(match, keep_key=False), text)
    text = _KEYED_SECRET.sub(replace, text)
    text = _KEYED_ENTROPY.sub(lambda match: replace(match, entropy=True), text)
    return text, count


def redact_value(value: Any) -> tuple[Any, int]:
    """Redact every string leaf of a nested value, including a secret recognizable only beside its key."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        items = [redact_value(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        total = 0
        for key, child in value.items():
            if isinstance(key, str) and isinstance(child, str):
                pair, count = redact_secrets(f"{key}: {child}")
                if count:
                    result[key] = pair.split(": ", 1)[1] if ": " in pair else "[REDACTED]"
                    total += count
                    continue
            result[key], count = redact_value(child)
            total += count
        return result, total
    return value, 0


def _redact(value: str) -> str:
    if len(value) <= 12:
        return "[REDACTED]"
    return value[:6] + "…[REDACTED]…" + value[-4:]


def combine_taint(values: Iterable[str]) -> str:
    values = list(values)
    if not values:
        return "unknown"
    return max(values, key=lambda value: TAINT_ORDER.get(str(value), 2))


def instruction_authorized(memory_type: str, authority: str, authorized: bool, taint: str) -> tuple[bool, str]:
    if memory_type != "agent-instruction":
        return True, "not privileged instruction"
    if not authorized:
        return False, "agent-instruction lacks explicit authorization"
    if authority not in TRUSTED_AUTHORITIES:
        return False, f"agent-instruction authority {authority!r} is not trusted"
    if TAINT_ORDER.get(taint, 2) >= TAINT_ORDER["external"]:
        return False, f"agent-instruction taint={taint} is not eligible for execution"
    return True, "instruction authorization passed"


def trust_gate(
    status: str,
    authority: str,
    confidence: float,
    allow_untrusted: bool = False,
    *,
    memory_type: str = "fact",
    authorized_instruction: bool = False,
    taint: str = "unknown",
) -> tuple[bool, str]:
    if status in {"quarantined", "archived", "superseded", "retracted"}:
        return False, f"status={status}"
    if status == "conflicted":
        return False, "unresolved contradiction"
    authorized, reason = instruction_authorized(memory_type, authority, authorized_instruction, taint)
    if not authorized:
        return False, reason
    if authority == "untrusted" and not allow_untrusted:
        return False, "untrusted provenance"
    if taint == "hostile":
        return False, "hostile provenance taint"
    if confidence < 0.35 and not allow_untrusted:
        return False, f"confidence {confidence:.2f} below trust gate"
    return True, "passed trust gate"


def _string_leaves(value: object) -> Iterable[str]:
    """Yield every string in a nested value exactly as it will be written.

    Scanning a JSON dump is unsafe: escaping turns a newline before ``api_key=...`` into
    ``\\napi_key``, which defeats word-boundary patterns.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.extend((key, child))
                if isinstance(key, str) and isinstance(child, str | int | float) and not isinstance(child, bool):
                    yield f"{key}: {child}"  # a secret may be recognizable only next to its key
        elif isinstance(item, list | tuple | set | frozenset):
            stack.extend(item)
        elif item is not None and not isinstance(item, bool | int | float):
            yield str(item)


def reject_secrets(value: object) -> None:
    """Check the complete object before persistence; never include its secret in errors."""
    for text in _string_leaves(value):
        if any(finding.category == "secret" for finding in scan_content(text)):
            raise ValueError("refusing to persist secret-bearing content")
