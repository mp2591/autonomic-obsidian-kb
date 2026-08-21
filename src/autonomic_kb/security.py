from __future__ import annotations

import re
from dataclasses import dataclass

_SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "generic-secret": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{12,}"
    ),
}
_INJECTION_PATTERNS = {
    "instruction-override": re.compile(r"(?i)\bignore (?:all |any )?(?:previous|prior|system|developer) instructions?\b"),
    "prompt-exfiltration": re.compile(r"(?i)\b(?:reveal|print|dump|exfiltrate).{0,30}(?:system prompt|developer message|secrets?|credentials?)\b"),
    "role-forgery": re.compile(r"(?im)^\s*(?:system|developer|assistant)\s*:\s*"),
    "tool-coercion": re.compile(r"(?i)\b(?:must|always) (?:run|call|invoke).{0,30}(?:shell|tool|command)\b"),
}


@dataclass(slots=True)
class SecurityFinding:
    category: str
    pattern: str
    excerpt: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "pattern": self.pattern,
            "excerpt": self.excerpt,
            "severity": self.severity,
        }


def scan_content(text: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for name, pattern in _SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            excerpt = text[max(0, match.start() - 16): match.end() + 16]
            findings.append(SecurityFinding("secret", name, _redact(excerpt), "critical"))
    for name, pattern in _INJECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            excerpt = text[max(0, match.start() - 24): match.end() + 24]
            findings.append(SecurityFinding("prompt-injection", name, excerpt.replace("\n", " ")[:180], "high"))
    return findings


def _redact(value: str) -> str:
    if len(value) <= 12:
        return "[REDACTED]"
    return value[:6] + "…[REDACTED]…" + value[-4:]


def trust_gate(status: str, authority: str, confidence: float, allow_untrusted: bool = False) -> tuple[bool, str]:
    if status in {"quarantined", "archived", "superseded"}:
        return False, f"status={status}"
    if status == "conflicted":
        return False, "unresolved contradiction"
    if authority == "untrusted" and not allow_untrusted:
        return False, "untrusted provenance"
    if confidence < 0.35 and not allow_untrusted:
        return False, f"confidence {confidence:.2f} below trust gate"
    return True, "passed trust gate"
