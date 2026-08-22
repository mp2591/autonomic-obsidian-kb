from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class IntegrationReport:
    obsidian_version: str = ""
    vault: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, name: str, detail: str = "") -> None:
        self.checks.append(Check(name=name, ok=True, detail=detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "obsidian_version": self.obsidian_version,
            "vault": self.vault,
            "checks": [asdict(check) for check in self.checks],
        }
