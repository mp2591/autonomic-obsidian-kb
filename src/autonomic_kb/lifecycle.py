"""Reviewer-directed lifecycle: inspect the inbox, promote, revalidate, and supersede.

Candidates submitted by agents land in the inbox unless they carry enough evidence to
clear the promotion threshold. These operations are the review gate they pass through,
so they are CLI-only and are not exposed as MCP tools. Each one runs in a semantic
transaction, requires a stated reason, and appends a ledger operation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .applicability import source_dependencies
from .config import KBConfig
from .evidence import EvidenceStore, OperationLedger
from .index import KnowledgeIndex
from .learning import Learner
from .markdown import dump_frontmatter, parse_markdown
from .security import instruction_authorized, scan_content
from .storage import semantic_transaction
from .util import atomic_write, sha256_file, sha256_text, slugify, utc_now

REVIEWABLE = {"inbox", "conflicted"}
RETIRED = {"archived", "superseded", "retracted", "quarantined"}


def _merge_list(existing: Any, additions: list[str]) -> list[str]:
    """Combine identity lists; tolerate a legacy scalar value instead of splitting it into characters."""
    if isinstance(existing, str):
        existing = [existing] if existing else []
    elif not isinstance(existing, list):
        existing = []
    return sorted({*(str(value) for value in existing), *additions})


def _require_reason(reason: str) -> str:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("a non-empty --reason is required for lifecycle changes")
    return reason


class Lifecycle:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.operations = OperationLedger(config)
        self.evidence = EvidenceStore(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def inbox(self) -> list[dict[str, Any]]:
        self.index.index_vault()
        return [
            {
                "id": note["declared_id"] or note["id"],
                "path": note["path"],
                "title": note["title"],
                "type": note["type"],
                "status": note["status"],
                "score": round(float(note.get("utility", 0.0)), 3),
                "threshold": self.config.promotion_threshold,
                "created": note.get("created", ""),
                "reason": self._inbox_reason(note),
                "blockers": self._promotion_blockers(note),
            }
            for note in self.index.all_notes(REVIEWABLE)
        ]

    def promote(self, memory_id: str, *, reason: str, actor: str = "reviewer") -> dict[str, Any]:
        reason = _require_reason(reason)
        note = self._note(memory_id)
        if note["status"] == "conflicted":
            raise ValueError(f"{memory_id} contradicts another memory; resolve it with `kb supersede`")
        if note["status"] != "inbox":
            raise ValueError(f"only inbox memories can be promoted; {memory_id} is {note['status']}")
        self._raise_blockers(note)
        with semantic_transaction(self.config.vault):
            destination = self._activate(note, reason=f"promote: {reason}", actor=actor)
        self.index.index_vault()
        return {
            "action": "promoted",
            "memory_id": note["declared_id"] or note["id"],
            "status": "active",
            "path": destination.relative_to(self.config.vault).as_posix(),
        }

    def revalidate(self, memory_id: str, *, reason: str, actor: str = "reviewer") -> dict[str, Any]:
        """Confirm a memory still holds for the current sources and rebind their digests."""
        reason = _require_reason(reason)
        note = self._note(memory_id)
        if note["status"] in RETIRED:
            raise ValueError(f"{memory_id} is {note['status']} and cannot be revalidated")
        metadata = note.get("metadata", {})
        bound = source_dependencies(metadata) + [
            {"path": str(path), "sha256": str(digest)}
            for path, digest in (metadata.get("invalidation", {}) or {}).get("source_hashes", {}).items()
        ]
        if bound and not self.config.repo:
            raise ValueError("revalidating source-bound memory requires a repository; pass --repo or set KB_REPO")
        current: dict[str, str] = {}
        missing: list[str] = []
        for dependency in bound:
            source = self._repository_file(dependency["path"])
            if source is None:
                missing.append(dependency["path"])
            else:
                current[dependency["path"]] = sha256_file(source)
        if missing:
            raise ValueError(
                "source dependencies are missing: " + ", ".join(sorted(set(missing))) + "; supersede or forget it"
            )
        changed = sorted({item["path"] for item in bound if current[item["path"]] != item["sha256"]})
        now = utc_now()

        def rebind(values: Any) -> Any:
            if not isinstance(values, list):
                return values
            result = []
            for value in values:
                if isinstance(value, dict) and value.get("path") in current:
                    value = dict(value)
                    for key in ("sha256", "source_hash"):
                        if key in value:
                            value[key] = current[str(value["path"])]
                result.append(value)
            return result

        def mutate(data: dict[str, Any]) -> None:
            for key in ("dependencies", "provenance", "validators"):
                if key in data:
                    data[key] = rebind(data[key])
            invalidation = data.get("invalidation")
            if isinstance(invalidation, dict) and isinstance(invalidation.get("source_hashes"), dict):
                invalidation["source_hashes"] = {
                    path: current.get(path, digest) for path, digest in invalidation["source_hashes"].items()
                }
            data["validated"] = now
            data["freshness"] = "verified"
            if data.get("status") == "stale":
                data["status"] = "active"

        with semantic_transaction(self.config.vault):
            path, before, after = self._rewrite(note, mutate)
            self.operations.append(
                "REVALIDATE",
                note["declared_id"] or note["id"],
                actor=actor,
                basis=[f"{source}@sha256:{digest}" for source, digest in sorted(current.items())],
                previous_digest=sha256_text(before),
                new_digest=sha256_text(after),
                reason=reason,
                metadata={"changed_sources": changed},
            )
        self.index.index_vault()
        return {
            "action": "revalidated",
            "memory_id": note["declared_id"] or note["id"],
            "path": path.relative_to(self.config.vault).as_posix(),
            "changed_sources": changed,
            "dependencies": parse_markdown(after).metadata.get("dependencies", []),
        }

    def supersede(self, old_id: str, new_id: str, *, reason: str, actor: str = "reviewer") -> dict[str, Any]:
        """Retire ``old_id`` in favor of ``new_id``; this is how a reviewer resolves a contradiction."""
        old, new = self._replace([old_id], [new_id], "SUPERSEDE", reason=reason, actor=actor)
        return {"action": "superseded", "memory_id": old[0], "superseded_by": new[0]}

    def merge(self, target_id: str, source_ids: list[str], *, reason: str, actor: str = "reviewer") -> dict[str, Any]:
        """Retire several overlapping memories into one reviewed ``target_id``."""
        sources, target = self._replace(source_ids, [target_id], "MERGE", reason=reason, actor=actor)
        return {"action": "merged", "memory_id": target[0], "merged_from": sources}

    def split(self, source_id: str, part_ids: list[str], *, reason: str, actor: str = "reviewer") -> dict[str, Any]:
        """Retire an overloaded memory in favor of narrower reviewed ``part_ids``."""
        if len(part_ids) < 2:
            raise ValueError("a split needs at least two parts")
        source, parts = self._replace([source_id], part_ids, "SPLIT", reason=reason, actor=actor)
        return {"action": "split", "memory_id": source[0], "split_into": parts}

    def _replace(
        self, retired_ids: list[str], successor_ids: list[str], operation: str, *, reason: str, actor: str
    ) -> tuple[list[str], list[str]]:
        """Retire memories in favor of successors in one transaction, recording provenance both ways."""
        reason = _require_reason(reason)
        if not retired_ids or not successor_ids:
            raise ValueError("both retired and replacement memories are required")
        retired = [self._note(identity) for identity in retired_ids]
        successors = [self._note(identity) for identity in successor_ids]
        rows = [note["id"] for note in retired + successors]
        if len(set(rows)) != len(rows):
            raise ValueError("a memory can appear only once and cannot replace itself")
        for note in retired:
            if note["status"] in RETIRED:
                raise ValueError(f"{note['declared_id'] or note['id']} is already {note['status']}")
        for note in successors:
            if note["status"] in RETIRED or note["status"] == "stale":
                raise ValueError(
                    f"replacement {note['declared_id'] or note['id']} is {note['status']}; "
                    "revalidate or choose an active memory"
                )
            if note["status"] in REVIEWABLE:
                self._raise_blockers(note)
        retired_identities = [note["declared_id"] or note["id"] for note in retired]
        successor_identities = [note["declared_id"] or note["id"] for note in successors]
        forward, backward = {
            "SUPERSEDE": ("superseded_by", "supersedes"),
            "MERGE": ("merged_into", "merged_from"),
            "SPLIT": ("split_into", "split_from"),
        }[operation]

        def retire(data: dict[str, Any]) -> None:
            data["status"] = "superseded"
            if len(successor_identities) == 1:
                data["superseded_by"] = successor_identities[0]
            if forward != "superseded_by":
                data[forward] = _merge_list(data.get(forward), successor_identities)

        with semantic_transaction(self.config.vault):
            for note, identity in zip(retired, retired_identities, strict=True):
                _, before, after = self._rewrite(note, retire)
                self.operations.append(
                    operation,
                    identity,
                    actor=actor,
                    basis=successor_identities,
                    previous_digest=sha256_text(before),
                    new_digest=sha256_text(after),
                    reason=reason,
                )
            for note in successors:
                self._activate(
                    note,
                    reason=f"{operation.lower()} of {', '.join(retired_identities)}: {reason}",
                    actor=actor,
                    provenance=(backward, retired_identities),
                )
        self.index.index_vault()
        return retired_identities, successor_identities

    def _activate(
        self, note: dict[str, Any], *, reason: str, actor: str, provenance: tuple[str, list[str]] | None = None
    ) -> Path:
        previous = note["status"]

        def mutate(data: dict[str, Any]) -> None:
            data["status"] = "active"
            if provenance:
                field, identities = provenance
                data[field] = _merge_list(data.get(field), identities)

        move_to = Learner.directory_for_type(str(note.get("type", "fact"))) if previous in REVIEWABLE else None
        path, before, after = self._rewrite(note, mutate, move_to=move_to)
        self.operations.append(
            "AMEND",
            note["declared_id"] or note["id"],
            actor=actor,
            previous_digest=sha256_text(before),
            new_digest=sha256_text(after),
            reason=reason,
            metadata={"from_status": previous, "to_status": "active"},
        )
        return path

    def _rewrite(
        self, note: dict[str, Any], mutate: Callable[[dict[str, Any]], None], move_to: str | None = None
    ) -> tuple[Path, str, str]:
        path = self.config.vault / note["path"]
        before = path.read_text(encoding="utf-8")
        parsed = parse_markdown(before)
        metadata = dict(parsed.metadata)
        mutate(metadata)
        metadata["updated"] = utc_now()
        after = dump_frontmatter(metadata) + parsed.body.lstrip()
        atomic_write(path, after)
        destination = path
        if move_to and Path(note["path"]).is_relative_to(self.config.inbox_dir):
            destination = self.config.vault / move_to / path.name
            if destination.exists():
                destination = destination.with_name(f"{destination.stem}-{slugify(note['id'], 12)}.md")
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.replace(destination)
        return destination, before, after

    def _note(self, memory_id: str) -> dict[str, Any]:
        self.index.index_vault()
        note = self.index.get(memory_id)
        if not note:
            raise ValueError(f"memory not found: {memory_id}")
        declarers = [row for row in self.index.all_notes() if row["declared_id"] == note["declared_id"]]
        if len(declarers) > 1:
            raise ValueError(f"{memory_id} is declared by {len(declarers)} notes; resolve the duplicate identity")
        return note

    def _repository_file(self, relative: str) -> Path | None:
        if not self.config.repo:
            return None
        root = self.config.repo.resolve()
        source = (root / relative).resolve()
        return source if source.is_relative_to(root) and source.is_file() else None

    def _inbox_reason(self, note: dict[str, Any]) -> str:
        if note["status"] == "conflicted":
            return "contradicts an applicable memory; resolve with `kb supersede OLD NEW`"
        authorized, reason = instruction_authorized(
            str(note.get("type")),
            str(note.get("authority")),
            bool(note.get("authorized_instruction")),
            str(note.get("taint")),
        )
        if not authorized:
            return reason
        score = float(note.get("utility", 0.0))
        if score < self.config.promotion_threshold:
            return f"candidate score {score:.3f} is below the promotion threshold {self.config.promotion_threshold:.2f}"
        return "awaiting review"

    def _promotion_blockers(self, note: dict[str, Any]) -> list[str]:
        blockers = []
        if not note.get("schema_valid", True):
            blockers.append("memory fails schema validation")
        if scan_content(str(note.get("body", "")) + str(note.get("metadata", {}))):
            blockers.append("memory contains unsafe content")
        authorized, reason = instruction_authorized(
            str(note.get("type")),
            str(note.get("authority")),
            bool(note.get("authorized_instruction")),
            str(note.get("taint")),
        )
        if not authorized:
            blockers.append(reason)
        if note.get("taint") == "hostile":
            blockers.append("hostile provenance taint")
        evidence = note.get("metadata", {}).get("evidence", [])
        if not isinstance(evidence, list) or not all(self.evidence.verify(str(value)) for value in evidence):
            blockers.append("evidence missing or tampered")
        return blockers

    def _raise_blockers(self, note: dict[str, Any]) -> None:
        blockers = self._promotion_blockers(note)
        if blockers:
            raise ValueError(f"cannot activate {note['declared_id'] or note['id']}: " + "; ".join(blockers))
