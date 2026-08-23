from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import KBConfig
from .evidence import OperationLedger
from .index import KnowledgeIndex
from .markdown import dump_frontmatter, parse_markdown
from .util import age_days, atomic_write, jaccard, sha256_text, slugify, utc_now
from .validation import ValidationReport, Validator


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
        before = validator.validate()
        actions = self.plan(before)
        changed: list[tuple[Path, Path]] = []
        if apply:
            for action in actions:
                if action.safe:
                    backup = self._apply(action)
                    if backup:
                        changed.append(backup)
            self.index.index_vault(force=True)
            after = validator.validate()
            if after.errors > before.errors:
                for source, backup in reversed(changed):
                    if backup.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, source)
                self.index.index_vault(force=True)
                for action in actions:
                    action.applied = False
                rollback = True
                final = validator.validate()
            else:
                rollback = False
                final = after
        else:
            rollback = False
            final = before
        contradictions = [issue.to_dict() for issue in before.issues if issue.code == "contradiction"]
        if contradictions:
            (self.config.runtime_dir / "contradictions.json").write_text(json.dumps(contradictions, indent=2), encoding="utf-8")
        result = {"mode": "apply" if apply else "dry-run", "validation": final.to_dict(),
                  "actions": [action.to_dict() for action in actions], "applied": sum(action.applied for action in actions),
                  "rolled_back": rollback}
        self.index.events.emit("heal.completed", mode=result["mode"], planned=len(actions), applied=result["applied"], rolled_back=rollback)
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
            if issue.code in {"source-changed", "source-missing", "expired", "validation-aged", "validator-command-failed", "validator-hash-mismatch"}:
                actions.append(HealingAction("mark-stale", issue.path, issue.message, True, details=issue.details))
            elif issue.code in {"secret", "prompt-injection"}:
                actions.append(HealingAction("quarantine", issue.path, issue.message, True, details=issue.details))
            elif issue.code == "broken-link":
                replacement = self._unique_link_target(str(issue.details.get("target", "")))
                actions.append(HealingAction("repair-link" if replacement else "report-broken-link", issue.path, issue.message,
                                             bool(replacement), details={"target": issue.details.get("target"), "replacement": replacement}))
            elif issue.code == "obsolete-path":
                actions.append(HealingAction("mark-stale", issue.path, issue.message, True, details=issue.details))
            elif issue.code in {"duplicate-id", "contradiction", "evidence-missing-or-tampered"}:
                actions.append(HealingAction("require-human-resolution", issue.path, issue.message, False, details=issue.details))
        for path, fields in missing_by_path.items():
            actions.append(HealingAction("repair-metadata", path, f"Add missing metadata: {', '.join(filter(None, fields))}", True,
                                         details={"fields": [field for field in fields if field]}))
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
        backup = self._backup(action.path)
        before = path.read_text(encoding="utf-8")
        parsed = parse_markdown(before)
        metadata = dict(parsed.metadata)
        memory_id = str(metadata.get("id", ""))
        now = utc_now()
        if action.action == "repair-metadata":
            defaults: dict[str, Any] = {
                "schema_version": 2, "id": f"kb:{metadata.get('scope','repository')}:{metadata.get('type','fact')}:{slugify(path.stem)}",
                "title": path.stem.replace("-", " ").title(), "type": "fact", "scope": "repository", "status": "inbox",
                "summary": parsed.layers.get(1, path.stem), "confidence": 0.5, "authority": "agent", "updated": now,
            }
            fields = action.details.get("fields", []) if action.details else []
            for field in fields:
                if field and metadata.get(field) in {None, ""} and field in defaults:
                    metadata[field] = defaults[field]
            atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
            action.applied = True
        elif action.action == "mark-stale":
            metadata["status"] = "stale"; metadata["freshness"] = "stale"; metadata["updated"] = now
            try:
                metadata["confidence"] = round(max(0.2, float(metadata.get("confidence", 0.5)) * 0.8), 3)
            except (TypeError, ValueError):
                metadata["confidence"] = 0.4
            atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
            action.applied = True
        elif action.action == "repair-link":
            target = str(action.details["target"]); replacement = str(action.details["replacement"])
            atomic_write(path, before.replace(f"[[{target}]]", f"[[{replacement}]]"))
            action.applied = True
        elif action.action == "quarantine":
            metadata["status"] = "quarantined"; metadata["freshness"] = "untrusted"; metadata["updated"] = now
            atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
            destination = self.config.vault / self.config.quarantine_dir / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination != path:
                if destination.exists():
                    destination = destination.with_name(f"{destination.stem}-{slugify(action.path,12)}.md")
                path.replace(destination)
                action.details["destination"] = destination.relative_to(self.config.vault).as_posix()
            action.applied = True
        if action.applied:
            current_path = self.config.vault / str(action.details.get("destination", action.path)) if action.details else path
            after = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
            self.operations.append("QUARANTINE" if action.action == "quarantine" else "AMEND", memory_id or action.path,
                                   actor="healer", previous_digest=sha256_text(before), new_digest=sha256_text(after), reason=action.reason)
            return current_path, backup
        return None

    def _unique_link_target(self, target: str) -> str:
        stem = Path(target).stem
        matches = [path.relative_to(self.config.vault).with_suffix("").as_posix()
                   for path in self.config.vault.rglob("*.md") if path.stem == stem and ".kb" not in path.parts]
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
        self.index.index_vault()
        notes = self.index.all_notes({"active", "inbox", "stale", "superseded"})
        candidates: list[dict[str, Any]] = []
        for i, note in enumerate(notes):
            text = str(note.get("l2") or note.get("summary", ""))
            for prior in notes[:i]:
                if note.get("scope") != prior.get("scope") or note.get("type") != prior.get("type"):
                    continue
                sim = jaccard(text, str(prior.get("l2") or prior.get("summary", "")))
                if sim >= 0.97:
                    candidates.append({"id": note["id"], "path": note["path"], "reason": f"near-duplicate similarity={sim:.3f}",
                                       "winner": prior["id"], "action": "archive-duplicate"})
                    break
            else:
                days = age_days(str(note.get("updated") or note.get("created") or ""))
                uses = int(note.get("uses", 0) or 0); utility = float(note.get("utility", 0.5))
                if note.get("status") in {"stale", "superseded"} and uses == 0:
                    candidates.append({"id": note["id"], "path": note["path"], "reason": f"unused {note['status']} memory", "action": "archive"})
                elif days >= self.config.archive_after_days and uses == 0 and utility < 0.32:
                    candidates.append({"id": note["id"], "path": note["path"], "reason": "expected future value below maintenance cost", "action": "archive"})
        applied = 0
        if apply:
            for candidate in candidates:
                source = self.config.vault / candidate["path"]
                if not source.exists(): continue
                parsed = parse_markdown(source.read_text(encoding="utf-8")); metadata = dict(parsed.metadata)
                metadata["status"] = "archived"; metadata["updated"] = utc_now()
                if candidate.get("winner"): metadata["superseded_by"] = candidate["winner"]
                before = source.read_text(encoding="utf-8")
                source.write_text(dump_frontmatter(metadata) + parsed.body.lstrip(), encoding="utf-8")
                destination = self.config.vault / self.config.archive_dir / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and destination != source:
                    destination = destination.with_name(f"{destination.stem}-{slugify(candidate['id'],10)}.md")
                if destination != source: source.replace(destination)
                self.operations.append("ARCHIVE", candidate["id"], actor="compactor", previous_digest=sha256_text(before),
                                       new_digest=sha256_text(destination.read_text(encoding="utf-8")), reason=candidate["reason"])
                candidate["applied"] = True; candidate["destination"] = destination.relative_to(self.config.vault).as_posix(); applied += 1
            self.index.index_vault(force=True)
        result = {"mode": "apply" if apply else "dry-run", "candidates": candidates, "applied": applied}
        self.index.events.emit("compact.completed", mode=result["mode"], candidates=len(candidates), applied=applied)
        return result


def forget(config: KBConfig, memory_id: str, hard: bool = False) -> dict[str, Any]:
    with KnowledgeIndex(config) as index:
        index.index_vault(); note = index.get(memory_id)
        if not note: return {"action": "not-found", "memory_id": memory_id}
        path = config.vault / note["path"]; ledger = OperationLedger(config)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        if hard:
            path.unlink(missing_ok=True); action = "deleted"; destination = ""
            ledger.append("RETRACT", memory_id, actor="forget", previous_digest=sha256_text(before), reason="explicit hard delete")
        else:
            parsed = parse_markdown(before); metadata = dict(parsed.metadata); metadata["status"] = "archived"; metadata["updated"] = utc_now()
            path.write_text(dump_frontmatter(metadata) + parsed.body.lstrip(), encoding="utf-8")
            target = config.vault / config.archive_dir / path.name; target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target != path: target = target.with_name(f"{target.stem}-{slugify(memory_id,10)}.md")
            if target != path: path.replace(target)
            destination = target.relative_to(config.vault).as_posix(); action = "archived"
            ledger.append("ARCHIVE", memory_id, actor="forget", previous_digest=sha256_text(before),
                          new_digest=sha256_text(target.read_text(encoding="utf-8")), reason="soft forget")
        index.index_vault(force=True); index.events.emit("memory.forgotten", memory_id=memory_id, action=action, destination=destination)
        return {"action": action, "memory_id": memory_id, "destination": destination}
