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
        reason = _require_reason(reason)
        old, new = self._note(old_id), self._note(new_id)
        if old["id"] == new["id"]:
            raise ValueError("a memory cannot supersede itself")
        if old["status"] in RETIRED:
            raise ValueError(f"{old_id} is already {old['status']}")
        if new["status"] in RETIRED or new["status"] == "stale":
            raise ValueError(f"replacement {new_id} is {new['status']}; revalidate or choose an active memory")
        if new["status"] in REVIEWABLE:
            self._raise_blockers(new)
        old_identity = old["declared_id"] or old["id"]
        new_identity = new["declared_id"] or new["id"]

        def retire(data: dict[str, Any]) -> None:
            data["status"] = "superseded"
            data["superseded_by"] = new_identity

        with semantic_transaction(self.config.vault):
            _, before, after = self._rewrite(old, retire)
            self.operations.append(
                "SUPERSEDE",
                old_identity,
                actor=actor,
                basis=[new_identity],
                previous_digest=sha256_text(before),
                new_digest=sha256_text(after),
                reason=reason,
            )
            self._activate(new, reason=f"supersedes {old_identity}: {reason}", actor=actor, supersedes=old_identity)
        self.index.index_vault()
        return {"action": "superseded", "memory_id": old_identity, "superseded_by": new_identity}

    def _activate(self, note: dict[str, Any], *, reason: str, actor: str, supersedes: str = "") -> Path:
        previous = note["status"]

        def mutate(data: dict[str, Any]) -> None:
            data["status"] = "active"
            if supersedes:
                data["supersedes"] = sorted({*data.get("supersedes", []), supersedes})

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
