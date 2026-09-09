from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import KBConfig
from .evidence import EvidenceStore
from .index import KnowledgeIndex
from .markdown import parse_markdown
from .models import VALID_KINDS, VALID_SCOPES, VALID_STATUSES, VALID_TYPES, ValidationIssue
from .security import scan_content
from .util import age_days, parse_time, sha256_file, utc_now

try:
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover - dependency-free fallback remains functional
    jsonschema = None


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


class ValidatorRegistry:
    def __init__(self, config: KBConfig):
        self.config = config
        self.handlers: dict[str, Callable[[dict[str, Any], dict[str, Any], str], list[ValidationIssue]]] = {
            "file-exists": self._file_exists,
            "source-hash": self._source_hash,
        }

    def run(self, metadata: dict[str, Any], relative: str) -> list[ValidationIssue]:
        result: list[ValidationIssue] = []
        validators = metadata.get("validators", [])
        if isinstance(validators, dict):
            validators = [validators]
        if not isinstance(validators, list):
            return result
        for spec in validators:
            if not isinstance(spec, dict):
                continue
            kind = str(spec.get("kind", ""))
            if kind == "command":
                result.append(
                    ValidationIssue(
                        "error",
                        "validator-execution-disabled",
                        relative,
                        "Memory cannot authorize command execution; use an explicit host evaluator.",
                        str(metadata.get("id", "")),
                        False,
                    )
                )
                continue
            handler = self.handlers.get(kind)
            if handler:
                result.extend(handler(metadata, spec, relative))
            else:
                result.append(
                    ValidationIssue(
                        "warning",
                        "unknown-validator",
                        relative,
                        f"Unknown validator kind {kind!r}",
                        str(metadata.get("id", "")),
                        False,
                        {"validator": spec},
                    )
                )
        return result

    def _safe_repo_path(self, value: str) -> Path | None:
        if not self.config.repo:
            return None
        root = self.config.repo.resolve()
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _file_exists(self, metadata: dict[str, Any], spec: dict[str, Any], relative: str) -> list[ValidationIssue]:
        path = self._safe_repo_path(str(spec.get("path", "")))
        if not path:
            return [
                ValidationIssue(
                    "error",
                    "validator-path-escape",
                    relative,
                    "file-exists validator escaped repository root",
                    str(metadata.get("id", "")),
                    False,
                    spec,
                )
            ]
        if not path.exists():
            return [
                ValidationIssue(
                    "warning",
                    "validator-file-missing",
                    relative,
                    f"Validator path does not exist: {spec.get('path')}",
                    str(metadata.get("id", "")),
                    True,
                    spec,
                )
            ]
        return []

    def _source_hash(self, metadata: dict[str, Any], spec: dict[str, Any], relative: str) -> list[ValidationIssue]:
        path = self._safe_repo_path(str(spec.get("path", "")))
        expected = str(spec.get("sha256", ""))
        if not path:
            return [
                ValidationIssue(
                    "error",
                    "validator-path-escape",
                    relative,
                    "source-hash validator escaped repository root",
                    str(metadata.get("id", "")),
                    False,
                    spec,
                )
            ]
        if not path.exists():
            return [
                ValidationIssue(
                    "warning",
                    "validator-file-missing",
                    relative,
                    f"Validator source is missing: {spec.get('path')}",
                    str(metadata.get("id", "")),
                    True,
                    spec,
                )
            ]
        if expected and sha256_file(path) != expected:
            return [
                ValidationIssue(
                    "warning",
                    "validator-hash-mismatch",
                    relative,
                    f"Validator source hash changed: {spec.get('path')}",
                    str(metadata.get("id", "")),
                    True,
                    spec,
                )
            ]
        return []


class Validator:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.evidence = EvidenceStore(config)
        self.registry = ValidatorRegistry(config)
        self.schema = self._load_schema()

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def _load_schema(self) -> dict[str, Any]:
        from importlib.resources import files

        return json.loads(files("autonomic_kb").joinpath("data/memory.schema.json").read_text(encoding="utf-8"))

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
            issues.extend(self._evidence_issues(relative, parsed.metadata))
            issues.extend(self.registry.run(parsed.metadata, relative))
        for memory_id, paths in id_paths.items():
            if len(paths) > 1:
                for relative in paths:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "duplicate-id",
                            relative,
                            f"Canonical id {memory_id!r} is declared by {len(paths)} notes: {', '.join(paths)}",
                            memory_id,
                            False,
                            {"paths": paths},
                        )
                    )
        claims: dict[str, list[tuple[Any, str, dict[str, Any]]]] = {}
        for path, metadata, text in notes:
            relative = path.relative_to(self.config.vault).as_posix()
            memory_id = str(metadata.get("id", ""))
            parsed = parse_markdown(text)
            for target in parsed.links:
                if not self._resolve_link(target, path_set, basename_map):
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "broken-link",
                            relative,
                            f"Unresolved wikilink [[{target}]]",
                            memory_id,
                            True,
                            {"target": target},
                        )
                    )
            issues.extend(self._invalidation_issues(path, metadata))
            claim_key = str(metadata.get("claim_key", ""))
            if claim_key and metadata.get("status", "active") == "active":
                claims.setdefault(
                    json.dumps(
                        [claim_key, metadata.get("repository_id"), metadata.get("branch"), metadata.get("scope")]
                    ),
                    [],
                ).append((metadata.get("claim_value"), relative, metadata))
        for claim_key, values in claims.items():
            distinct = {json.dumps(value, sort_keys=True, default=str) for value, _, _ in values}
            if len(distinct) <= 1:
                continue
            for i, (value_a, path_a, meta_a) in enumerate(values):
                for value_b, path_b, meta_b in values[i + 1 :]:
                    if json.dumps(value_a, sort_keys=True, default=str) == json.dumps(
                        value_b, sort_keys=True, default=str
                    ):
                        continue
                    if self._validity_disjoint(meta_a, meta_b):
                        continue  # temporal succession, not an active contradiction
                    issues.append(
                        ValidationIssue(
                            "error",
                            "contradiction",
                            path_a,
                            f"Active memories disagree on claim {claim_key!r}; "
                            "conflict requires provenance-based resolution",
                            str(meta_a.get("id", "")),
                            False,
                            {"claim_key": claim_key, "other_path": path_b, "left": value_a, "right": value_b},
                        )
                    )
        report = ValidationReport(utc_now(), len(notes), issues)
        self.index.save_validation(report.to_dict())
        self.index.events.emit(
            "validate.completed",
            scanned=report.scanned,
            errors=report.errors,
            warnings=report.warnings,
            issue_codes=sorted({issue.code for issue in issues}),
        )
        return report

    def validation_priorities(self) -> list[dict[str, Any]]:
        self.index.index_vault()
        result: list[dict[str, Any]] = []
        risk_weight = {
            "agent-instruction": 1.0,
            "command": 0.8,
            "workflow": 0.75,
            "invariant": 0.9,
            "dependency": 0.7,
            "fact": 0.45,
        }
        usage = dict(self.index.connection.execute("SELECT note_id,COUNT(*) FROM usage GROUP BY note_id"))
        for note in self.index.all_notes({"active", "stale"}):
            days = age_days(str(note.get("validated") or note.get("updated") or ""))
            uses = int(usage.get(note["id"], 0))
            stale_prob = min(1.0, days / max(1.0, self.config.stale_after_days))
            reuse = min(1.0, 0.15 + uses * 0.12)
            harm = risk_weight.get(str(note.get("type")), 0.5)
            validators = note.get("metadata", {}).get("validators", [])
            validation_cost = max(0.2, 0.25 * max(1, len(validators)))
            priority = stale_prob * reuse * harm / validation_cost
            result.append(
                {
                    "id": note["declared_id"] or note["id"],
                    "path": note["path"],
                    "priority": round(priority, 4),
                    "days": round(days, 1),
                    "uses": uses,
                }
            )
        return sorted(result, key=lambda item: (-item["priority"], item["id"]))

    def _paths(self):
        ignored = {
            ".git",
            ".obsidian",
            ".kb",
            ".kb-evidence",
            ".kb-memory-events",
            ".kb-episodes",
            "__pycache__",
            ".venv",
        }
        for path in self.config.vault.rglob("*.md"):
            relative = path.relative_to(self.config.vault)
            if any(part in ignored for part in relative.parts):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.config.vault.resolve()):
                continue
            yield path

    def _schema_issues(self, path: str, metadata: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        memory_id = str(metadata.get("id", ""))
        required = ("id", "title", "type", "scope", "status", "summary", "confidence", "authority", "updated")
        for key in required:
            if key not in metadata or metadata.get(key) in (None, ""):
                issues.append(
                    ValidationIssue(
                        "error" if key in {"id", "type", "scope", "summary"} else "warning",
                        "missing-metadata",
                        path,
                        f"Required metadata field {key!r} is missing",
                        memory_id,
                        True,
                        {"field": key},
                    )
                )
        if metadata.get("type") and (not isinstance(metadata["type"], str) or metadata["type"] not in VALID_TYPES):
            issues.append(
                ValidationIssue("error", "invalid-type", path, f"Unknown memory type {metadata['type']!r}", memory_id)
            )
        if metadata.get("kind") and (not isinstance(metadata["kind"], str) or metadata["kind"] not in VALID_KINDS):
            issues.append(
                ValidationIssue("error", "invalid-kind", path, f"Unknown memory kind {metadata['kind']!r}", memory_id)
            )
        if metadata.get("scope") and (not isinstance(metadata["scope"], str) or metadata["scope"] not in VALID_SCOPES):
            issues.append(
                ValidationIssue("error", "invalid-scope", path, f"Unknown scope {metadata['scope']!r}", memory_id)
            )
        if metadata.get("status") and (
            not isinstance(metadata["status"], str) or metadata["status"] not in VALID_STATUSES
        ):
            issues.append(
                ValidationIssue("error", "invalid-status", path, f"Unknown status {metadata['status']!r}", memory_id)
            )
        try:
            confidence = float(metadata.get("confidence", 0.5))
            if not 0 <= confidence <= 1:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(
                ValidationIssue(
                    "error", "invalid-confidence", path, "confidence must be between 0 and 1", memory_id, True
                )
            )
        try:
            schema_version = int(metadata.get("schema_version", 1) or 1)
        except (ValueError, TypeError):
            schema_version = 2
        if schema_version < 2:
            issues.append(
                ValidationIssue(
                    "info",
                    "legacy-schema",
                    path,
                    "Memory uses schema v1; migrate when semantically edited",
                    memory_id,
                    True,
                )
            )
        elif self.schema and jsonschema is not None:
            try:
                jsonschema.Draft202012Validator(self.schema).validate(metadata)
            except jsonschema.ValidationError as error:
                issues.append(
                    ValidationIssue(
                        "error",
                        "json-schema",
                        path,
                        error.message,
                        memory_id,
                        True,
                        {"json_path": list(error.absolute_path)},
                    )
                )
        try:
            oversized = int(metadata.get("token_cost", 0) or 0) > 1600
        except (ValueError, TypeError):
            oversized = False
        if oversized:
            issues.append(
                ValidationIssue(
                    "warning",
                    "oversized-memory",
                    path,
                    "Memory exceeds 1,600 estimated tokens; split or compile a smaller task view",
                    memory_id,
                )
            )
        return issues

    @staticmethod
    def _security_issues(path: str, memory_id: str, text: str) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                finding.severity,
                finding.category,
                path,
                f"{finding.pattern}: {finding.excerpt}",
                memory_id,
                True,
                finding.to_dict(),
            )
            for finding in scan_content(text)
        ]

    def _evidence_issues(self, path: str, metadata: dict[str, Any]) -> list[ValidationIssue]:
        raw = metadata.get("evidence", [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return [
                ValidationIssue(
                    "error", "invalid-evidence", path, "evidence must be a list", str(metadata.get("id", ""))
                )
            ]
        issues = []
        for evidence_id in raw:
            if not self.evidence.verify(str(evidence_id)):
                issues.append(
                    ValidationIssue(
                        "warning",
                        "evidence-missing-or-tampered",
                        path,
                        f"Evidence {evidence_id!r} is missing or failed digest verification",
                        str(metadata.get("id", "")),
                        True,
                        {"evidence_id": evidence_id},
                    )
                )
        return issues

    def _invalidation_issues(self, path: Path, metadata: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        relative = path.relative_to(self.config.vault).as_posix()
        memory_id = str(metadata.get("id", ""))
        invalidation = metadata.get("invalidation", {})
        if not isinstance(invalidation, dict):
            return issues
        source_hashes = invalidation.get("source_hashes", {})
        if isinstance(source_hashes, dict) and self.config.repo:
            root = self.config.repo.resolve()
            for source, expected in source_hashes.items():
                absolute = (root / str(source)).resolve()
                try:
                    absolute.relative_to(root)
                except ValueError:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "source-path-escape",
                            relative,
                            f"Validation source {source!r} escapes repository root",
                            memory_id,
                            False,
                        )
                    )
                    continue
                if not absolute.exists():
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "source-missing",
                            relative,
                            f"Validation source {source!r} no longer exists",
                            memory_id,
                            True,
                            {"source": source},
                        )
                    )
                elif expected and sha256_file(absolute) != str(expected):
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "source-changed",
                            relative,
                            f"Validation source {source!r} changed after validation",
                            memory_id,
                            True,
                            {"source": source},
                        )
                    )
        expires = str(invalidation.get("expires", "") or "")
        parsed = parse_time(expires)
        if parsed and parsed < datetime.now(UTC):
            issues.append(
                ValidationIssue("warning", "expired", relative, f"Memory expired at {expires}", memory_id, True)
            )
        patterns = invalidation.get("paths", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        if patterns and self.config.repo:
            repo_files = [
                item.relative_to(self.config.repo).as_posix()
                for item in self.config.repo.rglob("*")
                if item.is_file() and ".git" not in item.parts
            ]
            for pattern in patterns:
                if not any(fnmatch.fnmatch(item, str(pattern)) for item in repo_files):
                    issues.append(
                        ValidationIssue(
                            "info",
                            "obsolete-path",
                            relative,
                            f"Applicable path pattern {pattern!r} currently matches no repository files",
                            memory_id,
                            True,
                            {"pattern": pattern},
                        )
                    )
        # Time is only a fallback trigger. Source/version events should dominate when available.
        if (
            metadata.get("freshness") == "verified"
            and not source_hashes
            and age_days(str(metadata.get("validated", ""))) > self.config.stale_after_days
        ):
            issues.append(
                ValidationIssue(
                    "warning",
                    "validation-aged",
                    relative,
                    f"Unbound validation is older than {self.config.stale_after_days} days",
                    memory_id,
                    True,
                )
            )
        return issues

    @staticmethod
    def _validity_disjoint(left: dict[str, Any], right: dict[str, Any]) -> bool:
        lv = left.get("validity", {}) if isinstance(left.get("validity", {}), dict) else {}
        rv = right.get("validity", {}) if isinstance(right.get("validity", {}), dict) else {}
        l_start, l_end = parse_time(str(lv.get("valid_from", ""))), parse_time(str(lv.get("valid_to", "")))
        r_start, r_end = parse_time(str(rv.get("valid_from", ""))), parse_time(str(rv.get("valid_to", "")))
        if l_end and r_start and l_end <= r_start:
            return True
        return bool(r_end and l_start and r_end <= l_start)

    @staticmethod
    def _resolve_link(target: str, paths: set[str], basenames: dict[str, list[str]]) -> bool:
        normalized = target.strip("/")
        if normalized in paths or normalized + ".md" in paths:
            return True
        return len(basenames.get(Path(normalized).stem, [])) == 1
