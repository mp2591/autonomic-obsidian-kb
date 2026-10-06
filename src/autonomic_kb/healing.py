from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import KBConfig
from .evidence import OperationLedger
from .index import KnowledgeIndex
from .markdown import dump_frontmatter, parse_markdown
from .security import redact_secrets, redact_value
from .storage import move_into, semantic_transaction, vault_markdown_paths
from .util import age_days, atomic_write, jaccard, remove_file, sha256_text, slugify, utc_now
from .validation import INACTIVE_STATUSES, ValidationReport, Validator

# Review states: freshness maintenance may annotate them but never changes their status,
# because a status change here would move a candidate past review (``stale`` is retrievable).
UNREVIEWED_STATUSES = {"inbox", "conflicted"}


@dataclass(slots=True)
class HealingAction:
    action: str
    path: str
    reason: str
    safe: bool
    applied: bool = False
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.details = self.details or {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _HealingRegression(ValueError):
    pass


class Healer:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.operations = OperationLedger(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def heal(self, apply: bool = False) -> dict[str, Any]:
        validator = Validator(self.config, self.index)
        actions: list[HealingAction] = []
        rolled_back = False
        rollback: dict[str, Any] | None = None
        if not apply:
            before = validator.validate()
            actions = self.plan(before)
        else:
            try:
                with semantic_transaction(self.config.vault):
                    # Validate and plan under the writer lock, so the plan matches what is applied.
                    before = validator.validate()
                    actions = self.plan(before)
                    for action in actions:
                        if action.safe:
                            self._apply(action)
                    self.index.index_vault()
                    after = validator.validate()
                    # A quarantined note keeps its findings at its new path; that is not a new error.
                    moved = {
                        str(action.details["destination"]): action.path
                        for action in actions
                        if action.applied and action.details and action.details.get("destination")
                    }
                    old_errors = {
                        (issue.code, issue.path) for issue in before.issues if issue.severity in {"error", "critical"}
                    }
                    new_errors = {
                        (issue.code, moved.get(issue.path, issue.path))
                        for issue in after.issues
                        if issue.severity in {"error", "critical"}
                    }
                    if new_errors - old_errors:
                        raise _HealingRegression("healing introduced a new validation error")
            except _HealingRegression as error:
                rolled_back = True
                # A rollback can preserve a version another program saved meanwhile; say so here too.
                rollback = getattr(error, "kb_rollback", None)
                for action in actions:
                    action.applied = False
            finally:
                self.index.index_vault()
        final = validator.validate() if apply else before
        result = {
            "mode": "apply" if apply else "dry-run",
            "validation": final.to_dict(),
            "actions": [action.to_dict() for action in actions],
            "applied": sum(action.applied for action in actions),
            "rolled_back": rolled_back,
            "rollback": rollback,
        }
        self.index.events.emit(
            "heal.completed", mode=result["mode"], applied=result["applied"], rolled_back=rolled_back
        )
        return result

    def plan(self, report: ValidationReport) -> list[HealingAction]:
        actions: list[HealingAction] = []
        seen: set[tuple[str, str]] = set()
        missing_by_path: dict[str, list[str]] = {}
        for issue in report.issues:
            if issue.code == "missing-metadata":
                missing_by_path.setdefault(issue.path, []).append(str(issue.details.get("field", "")))
                continue
            key = (issue.path, issue.code)
            if key in seen:
                continue
            seen.add(key)
            if issue.code in {
                "source-changed",
                "source-missing",
                "expired",
                "validation-aged",
                "validator-command-failed",
                "validator-hash-mismatch",
            }:
                actions.append(HealingAction("mark-stale", issue.path, issue.message, True, details=issue.details))
            elif issue.code in {"secret", "prompt-injection"}:
                actions.append(HealingAction("quarantine", issue.path, issue.message, True, details=issue.details))
            elif issue.code == "broken-link":
                replacement = self._unique_link_target(str(issue.details.get("target", "")))
                actions.append(
                    HealingAction(
                        "repair-link" if replacement else "report-broken-link",
                        issue.path,
                        issue.message,
                        bool(replacement),
                        details={"target": issue.details.get("target"), "replacement": replacement},
                    )
                )
            elif issue.code == "obsolete-path":
                actions.append(HealingAction("mark-stale", issue.path, issue.message, True, details=issue.details))
            elif issue.code in {"dependency-changed", "dependency-missing"}:
                actions.append(
                    HealingAction("require-revalidation", issue.path, issue.message, False, details=issue.details)
                )
            elif issue.code in {"duplicate-id", "contradiction", "evidence-missing-or-tampered"}:
                actions.append(
                    HealingAction("require-human-resolution", issue.path, issue.message, False, details=issue.details)
                )
        for path, fields in missing_by_path.items():
            actions.append(
                HealingAction(
                    "repair-metadata",
                    path,
                    f"Add missing metadata: {', '.join(filter(None, fields))}",
                    True,
                    details={"fields": [field for field in fields if field]},
                )
            )
        return actions

    def _backup(self, relative: str) -> Path:
        source = self.config.vault / relative
        timestamp = utc_now().replace(":", "").replace("-", "")
        destination = self.config.runtime_dir / "backups" / timestamp / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, destination)
        return destination

    def _apply(self, action: HealingAction) -> tuple[Path, Path] | None:
        path = self.config.vault / action.path
        if not path.exists():
            return None
        before = path.read_text(encoding="utf-8")
        parsed = parse_markdown(before)
        metadata = dict(parsed.metadata)
        memory_id = str(metadata.get("id", ""))
        status = str(metadata.get("status", "active"))
        if action.action == "mark-stale":
            if status in INACTIVE_STATUSES:
                action.details["skipped"] = f"{status} memories are retired; freshness is not maintained"
                return None
            if metadata.get("freshness") == "stale" and (status in UNREVIEWED_STATUSES or status == "stale"):
                # Marking is idempotent: re-applying it on every heal would only keep lowering
                # confidence and appending ledger operations for the same finding.
                action.details["skipped"] = "already marked stale"
                return None
        backup = self._backup(action.path)
        now = utc_now()
        if action.action == "repair-metadata":
            defaults: dict[str, Any] = {
                "schema_version": 2,
                "id": f"kb:{metadata.get('scope', 'repository')}:{metadata.get('type', 'fact')}:{slugify(path.stem)}",
                "title": path.stem.replace("-", " ").title(),
                "type": "fact",
                "scope": "repository",
                "status": "inbox",
                "summary": parsed.layers.get(1, path.stem),
                "confidence": 0.5,
                "authority": "agent",
                "updated": now,
            }
            fields = action.details.get("fields", []) if action.details else []
            for field in fields:
                if field and metadata.get(field) in (None, "") and field in defaults:
                    metadata[field] = defaults[field]
            atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
            action.applied = True
        elif action.action == "mark-stale":
            # Only reviewed knowledge changes status. An inbox or conflicted candidate keeps its
            # status and just records that its sources look stale for the reviewer.
            metadata["freshness"] = "stale"
            metadata["updated"] = now
            if status not in UNREVIEWED_STATUSES:
                metadata["status"] = "stale"
                try:
                    metadata["confidence"] = round(max(0.2, float(metadata.get("confidence", 0.5)) * 0.8), 3)
                except (TypeError, ValueError):
                    metadata["confidence"] = 0.4
            action.details["status"] = metadata["status"]
            atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
            action.applied = True
        elif action.action == "repair-link":
            target = str(action.details["target"])
            replacement = str(action.details["replacement"])
            atomic_write(path, before.replace(f"[[{target}]]", f"[[{replacement}]]"))
            action.applied = True
        elif action.action == "quarantine":
            # Secret values are redacted in the vault copy; the unredacted original survives only
            # in the local .kb/backups copy made above. Injection text is kept for review.
            metadata, redacted_metadata = redact_value(metadata)
            body, redacted_body = redact_secrets(parsed.body)
            action.details["redacted"] = redacted_metadata + redacted_body
            metadata["status"] = "quarantined"
            metadata["freshness"] = "untrusted"
            metadata["updated"] = now
            atomic_write(path, dump_frontmatter(metadata) + body.lstrip())
            moved = move_into(path, self.config.vault / self.config.quarantine_dir, memory_id or action.path)
            if moved.destination != path:
                action.details["destination"] = moved.destination.relative_to(self.config.vault).as_posix()
            if moved.conflicts:
                action.details["conflicts"] = moved.report(self.config.vault)
            action.applied = True
        if action.applied:
            current_path = (
                self.config.vault / str(action.details.get("destination", action.path)) if action.details else path
            )
            after = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
            quarantined = action.action == "quarantine"
            self.operations.append(
                "QUARANTINE" if quarantined else "AMEND",
                memory_id or action.path,
                actor="healer",
                previous_digest=sha256_text(before),
                new_digest=sha256_text(after),
                # Finding excerpts may contain secret fragments; the ledger keeps only counts.
                reason="unsafe content quarantined" if quarantined else action.reason,
                metadata={"redacted": action.details.get("redacted", 0)} if quarantined else {},
            )
            return current_path, backup
        return None

    def _unique_link_target(self, target: str) -> str:
        stem = Path(target).stem
        matches = [
            path.relative_to(self.config.vault).with_suffix("").as_posix()
            for path in vault_markdown_paths(self.config.vault)
            if path.stem == stem
        ]
        return matches[0] if len(matches) == 1 else ""


class Compactor:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.operations = OperationLedger(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def compact(self, apply: bool = False) -> dict[str, Any]:
        applied = 0
        if not apply:
            candidates = self._candidates()
        else:
            with semantic_transaction(self.config.vault):
                # Plan under the writer lock, so a note promoted or revalidated meanwhile is not archived.
                candidates = self._candidates()
                for candidate in candidates:
                    source = self.config.vault / candidate["path"]
                    if not source.exists():
                        continue
                    destination, before, after, conflicts = _archive(
                        self.config, source, candidate["id"], superseded_by=candidate.get("winner", "")
                    )
                    self.operations.append(
                        "ARCHIVE",
                        candidate["id"],
                        actor="compactor",
                        previous_digest=sha256_text(before),
                        new_digest=sha256_text(after),
                        reason=candidate["reason"],
                    )
                    candidate["applied"] = True
                    candidate["destination"] = destination.relative_to(self.config.vault).as_posix()
                    if conflicts:
                        candidate["conflicts"] = conflicts
                    applied += 1
            self.index.index_vault()
        result = {"mode": "apply" if apply else "dry-run", "candidates": candidates, "applied": applied}
        self.index.events.emit("compact.completed", mode=result["mode"], candidates=len(candidates), applied=applied)
        return result

    def _candidates(self) -> list[dict[str, Any]]:
        self.index.index_vault()
        notes = self.index.all_notes({"active", "inbox", "stale", "superseded"})
        candidates: list[dict[str, Any]] = []
        for i, note in enumerate(notes):
            text = str(note.get("l2") or note.get("summary", ""))
            for prior in notes[:i]:
                if note.get("scope") != prior.get("scope") or note.get("type") != prior.get("type"):
                    continue
                if any(
                    note.get(key) != prior.get(key) for key in ("repository_id", "branch", "valid_from", "valid_to")
                ):
                    continue
                if any(
                    note["metadata"].get(key) != prior["metadata"].get(key)
                    for key in ("preconditions", "verification", "dependencies", "evidence", "version_package")
                ):
                    continue
                sim = jaccard(text, str(prior.get("l2") or prior.get("summary", "")))
                if sim >= 0.97:
                    candidates.append(
                        {
                            "id": note["id"],
                            "path": note["path"],
                            "reason": f"near-duplicate similarity={sim:.3f}",
                            "winner": prior["id"],
                            "action": "archive-duplicate",
                        }
                    )
                    break
            else:
                days = age_days(str(note.get("updated") or note.get("created") or ""))
                uses = int(note.get("uses", 0) or 0)
                utility = float(note.get("utility", 0.5))
                if note.get("status") in {"stale", "superseded"} and uses == 0:
                    candidates.append(
                        {
                            "id": note["id"],
                            "path": note["path"],
                            "reason": f"unused {note['status']} memory",
                            "action": "archive",
                        }
                    )
                elif days >= self.config.archive_after_days and uses == 0 and utility < 0.32:
                    candidates.append(
                        {
                            "id": note["id"],
                            "path": note["path"],
                            "reason": "expected future value below maintenance cost",
                            "action": "archive",
                        }
                    )
        return candidates


def _archive(
    config: KBConfig, source: Path, memory_id: str, superseded_by: str = ""
) -> tuple[Path, str, str, list[dict[str, str]]]:
    """Mark a note archived and move it into the archive directory; return any move conflicts."""
    before = source.read_text(encoding="utf-8")
    parsed = parse_markdown(before)
    metadata = dict(parsed.metadata)
    metadata["status"] = "archived"
    metadata["updated"] = utc_now()
    if superseded_by:
        metadata["superseded_by"] = superseded_by
    after = dump_frontmatter(metadata) + parsed.body.lstrip()
    atomic_write(source, after)
    moved = move_into(source, config.vault / config.archive_dir, memory_id)
    return moved.destination, before, after, moved.report(config.vault)


def forget(config: KBConfig, memory_id: str, hard: bool = False) -> dict[str, Any]:
    with KnowledgeIndex(config) as index:
        ledger = OperationLedger(config)
        conflicts: list[dict[str, str]] = []
        with semantic_transaction(config.vault):
            # Resolve the note under the writer lock; a reviewer may have moved or retired it meanwhile.
            index.index_vault()
            note = index.get(memory_id)
            if not note:
                return {"action": "not-found", "memory_id": memory_id}
            declarers = [row for row in index.all_notes() if row["declared_id"] == note["declared_id"]]
            if len(declarers) > 1:
                raise ValueError(
                    f"{memory_id} is declared by {len(declarers)} notes; resolve the duplicate identity first"
                )
            path = config.vault / note["path"]
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            if hard:
                remove_file(path)
                action = "deleted"
                destination = ""
                ledger.append(
                    "RETRACT",
                    memory_id,
                    actor="forget",
                    previous_digest=sha256_text(before),
                    reason="explicit hard delete",
                )
            else:
                target, before, after, conflicts = _archive(config, path, memory_id)
                destination = target.relative_to(config.vault).as_posix()
                action = "archived"
                ledger.append(
                    "ARCHIVE",
                    memory_id,
                    actor="forget",
                    previous_digest=sha256_text(before),
                    new_digest=sha256_text(after),
                    reason="soft forget",
                )
        index.index_vault()
        index.events.emit("memory.forgotten", memory_id=memory_id, action=action, destination=destination)
        return {"action": action, "memory_id": memory_id, "destination": destination, "conflicts": conflicts}
