from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .markdown import parse_markdown
from .models import VALID_SCOPES, VALID_STATUSES, VALID_TYPES, ValidationIssue
from .security import scan_content
from .util import age_days, sha256_file, utc_now


@dataclass(slots=True)
class ValidationReport:
    created_at: str
    scanned: int
    issues: list[ValidationIssue]

    @property
    def errors(self) -> int:
        return sum(issue.severity in {"error", "critical"} for issue in self.issues)

    @property
    def warnings(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "scanned": self.scanned,
            "errors": self.errors,
            "warnings": self.warnings,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class Validator:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def validate(self) -> ValidationReport:
        self.index.index_vault(force=True)
        issues: list[ValidationIssue] = []
        notes: list[tuple[Path, dict[str, Any], str]] = []
        basename_map: dict[str, list[str]] = {}
        path_set: set[str] = set()
        id_paths: dict[str, list[str]] = {}
        for path in self._paths():
            relative = path.relative_to(self.config.vault).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_markdown(text)
            notes.append((path, parsed.metadata, text))
            path_set.add(relative)
            basename_map.setdefault(path.stem, []).append(relative)
            memory_id = str(parsed.metadata.get("id", ""))
            if memory_id:
                id_paths.setdefault(memory_id, []).append(relative)
            issues.extend(self._schema_issues(relative, parsed.metadata))
            issues.extend(self._security_issues(relative, memory_id, text))
        for memory_id, paths in id_paths.items():
            if len(paths) > 1:
                for relative in paths:
                    issues.append(ValidationIssue(
                        "error", "duplicate-id", relative,
                        f"Canonical id {memory_id!r} is declared by {len(paths)} notes: {', '.join(paths)}",
                        memory_id, False, {"paths": paths},
                    ))
        claims: dict[str, dict[str, list[str]]] = {}
        for path, metadata, text in notes:
            relative = path.relative_to(self.config.vault).as_posix()
            memory_id = str(metadata.get("id", ""))
            parsed = parse_markdown(text)
            for target in parsed.links:
                if not self._resolve_link(target, path_set, basename_map):
                    issues.append(ValidationIssue(
                        "warning", "broken-link", relative, f"Unresolved wikilink [[{target}]]",
                        memory_id, True, {"target": target},
                    ))
            issues.extend(self._invalidation_issues(path, metadata))
            claim_key = str(metadata.get("claim_key", ""))
            if claim_key and metadata.get("status", "active") == "active":
                value = json.dumps(metadata.get("claim_value"), sort_keys=True)
                claims.setdefault(claim_key, {}).setdefault(value, []).append(relative)
        for claim_key, values in claims.items():
            if len(values) > 1:
                paths = sorted({path for group in values.values() for path in group})
                for relative in paths:
                    issues.append(ValidationIssue(
                        "error", "contradiction", relative,
                        f"Active memories disagree on claim {claim_key!r}; conflict requires provenance-based resolution",
                        details={"claim_key": claim_key, "values": values},
                    ))
        report = ValidationReport(utc_now(), len(notes), issues)
        self.index.save_validation(report.to_dict())
        self.index.events.emit(
            "validate.completed", scanned=report.scanned, errors=report.errors,
            warnings=report.warnings, issue_codes=sorted({issue.code for issue in issues}),
        )
        return report

    def _paths(self):
        ignored = {".git", ".obsidian", ".kb", "__pycache__", ".venv"}
        for path in self.config.vault.rglob("*.md"):
            relative = path.relative_to(self.config.vault)
            if any(part in ignored for part in relative.parts):
                continue
            yield path

    @staticmethod
    def _schema_issues(path: str, metadata: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        memory_id = str(metadata.get("id", ""))
        for key in ("id", "title", "type", "scope", "status", "confidence", "updated"):
            if key not in metadata or metadata.get(key) in {None, ""}:
                issues.append(ValidationIssue(
                    "error" if key in {"id", "type", "scope"} else "warning",
                    "missing-metadata", path, f"Required metadata field {key!r} is missing",
                    memory_id, True, {"field": key},
                ))
        if metadata.get("type") and metadata["type"] not in VALID_TYPES:
            issues.append(ValidationIssue(
                "error", "invalid-type", path, f"Unknown memory type {metadata['type']!r}", memory_id,
            ))
        if metadata.get("scope") and metadata["scope"] not in VALID_SCOPES:
            issues.append(ValidationIssue(
                "error", "invalid-scope", path, f"Unknown scope {metadata['scope']!r}", memory_id,
            ))
        if metadata.get("status") and metadata["status"] not in VALID_STATUSES:
            issues.append(ValidationIssue(
                "error", "invalid-status", path, f"Unknown status {metadata['status']!r}", memory_id,
            ))
        try:
            confidence = float(metadata.get("confidence", 0.5))
            if not 0 <= confidence <= 1:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(ValidationIssue(
                "error", "invalid-confidence", path, "confidence must be a number between 0 and 1",
                memory_id, True,
            ))
        if int(metadata.get("token_cost", 0) or 0) > 1600:
            issues.append(ValidationIssue(
                "warning", "oversized-memory", path,
                "Memory exceeds 1,600 estimated tokens; split or compress it unless detail is rarely expanded",
                memory_id,
            ))
        return issues

    @staticmethod
    def _security_issues(path: str, memory_id: str, text: str) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                finding.severity, finding.category, path,
                f"{finding.pattern}: {finding.excerpt}", memory_id, True,
                finding.to_dict(),
            )
            for finding in scan_content(text)
        ]

    def _invalidation_issues(self, path: Path, metadata: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        relative = path.relative_to(self.config.vault).as_posix()
        memory_id = str(metadata.get("id", ""))
        invalidation = metadata.get("invalidation", {})
        if not isinstance(invalidation, dict):
            return issues
        source_hashes = invalidation.get("source_hashes", {})
        if isinstance(source_hashes, dict) and self.config.repo:
            for source, expected in source_hashes.items():
                absolute = self.config.repo / str(source)
                if not absolute.exists():
                    issues.append(ValidationIssue(
                        "warning", "source-missing", relative,
                        f"Validation source {source!r} no longer exists", memory_id, True,
                        {"source": source},
                    ))
                elif expected and sha256_file(absolute) != str(expected):
                    issues.append(ValidationIssue(
                        "warning", "source-changed", relative,
                        f"Validation source {source!r} changed after this memory was validated",
                        memory_id, True, {"source": source},
                    ))
        expires = str(invalidation.get("expires", "") or "")
        if expires and age_days(expires) > 0:
            # age_days is positive for both future and past; compare lexical ISO via parsed timestamps instead.
            from .util import parse_time
            from datetime import datetime, timezone
            parsed = parse_time(expires)
            if parsed and parsed < datetime.now(timezone.utc):
                issues.append(ValidationIssue(
                    "warning", "expired", relative, f"Memory expired at {expires}", memory_id, True,
                ))
        patterns = invalidation.get("paths", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        if patterns and self.config.repo:
            repo_files = [item.relative_to(self.config.repo).as_posix() for item in self.config.repo.rglob("*") if item.is_file() and ".git" not in item.parts]
            for pattern in patterns:
                if not any(fnmatch.fnmatch(item, str(pattern)) for item in repo_files):
                    issues.append(ValidationIssue(
                        "info", "obsolete-path", relative,
                        f"Applicable path pattern {pattern!r} currently matches no repository files",
                        memory_id, True, {"pattern": pattern},
                    ))
        if metadata.get("freshness") == "verified" and age_days(str(metadata.get("validated", ""))) > self.config.stale_after_days:
            issues.append(ValidationIssue(
                "warning", "validation-aged", relative,
                f"Last validation is older than {self.config.stale_after_days} days", memory_id, True,
            ))
        return issues

    @staticmethod
    def _resolve_link(target: str, paths: set[str], basenames: dict[str, list[str]]) -> bool:
        normalized = target.strip("/")
        candidates = {normalized, normalized + ".md"}
        if any(candidate in paths for candidate in candidates):
            return True
        return len(basenames.get(Path(normalized).stem, [])) == 1
