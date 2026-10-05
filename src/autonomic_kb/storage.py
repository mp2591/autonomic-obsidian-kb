"""Single-writer transactions for KB-owned semantic edits.

This lock coordinates KB processes. External editors must still be reconciled; it is
not a security sandbox or a lock respected by Obsidian.

A transaction snapshots semantic files as hard links (a copy only where the filesystem
cannot link), so starting one costs one link per file rather than reading and journaling
the whole vault. While it runs, every KB write records the path and the digest of the
bytes it left there (``util.tracked_writes``). Rollback touches only those paths: it moves
the current file aside, restores the before-state without replacing anything, and leaves
every other path alone. A current file that differs from what the KB wrote was changed by
another program; it is preserved under ``.kb/rolled-back/`` and reported. Only a crash
leaves a prepared journal behind; ``kb reconcile`` resolves it under owner control.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .util import atomic_write, record_write, sha256_file, sha256_text, stable_json, tracked_writes, utc_now

_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()
_GUARD = threading.Lock()


@contextmanager
def vault_lock(vault: Path):
    key = str(vault.resolve())
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_LOCAL, "held", set())
        if key in held:
            yield
            return
        path = vault / ".kb-writer.lock"
        if path.is_symlink() or not path.resolve().is_relative_to(vault.resolve()):
            raise ValueError("vault lock path escaped vault")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX)
            _LOCAL.held = held | {key}
            try:
                yield
            finally:
                _LOCAL.held = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


EXCLUDED_DIRECTORIES = {"__pycache__", "node_modules"}
# Heal backups keep unredacted originals; they are local recovery copies, not an archive,
# so they expire. Files under .kb/rolled-back/ are never pruned: one may be the only copy
# of a note someone saved while a transaction failed.
LOCAL_COPY_DIRECTORIES = (".kb/backups",)
LOCAL_COPY_RETENTION_DAYS = 30
LEDGER_DIRECTORY = ".kb-memory-events"
JOURNAL_DIRECTORY = ".kb-transactions"
ROLLED_BACK_DIRECTORY = ".kb/rolled-back"


def _walk(root: Path, suffix: str, *, skip_hidden: bool) -> Iterator[Path]:
    """Deterministic walk that never follows or yields symlinks.

    Without following symlinks every yielded file is inside ``root``, so no per-file
    ``resolve()`` is needed.
    """
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            continue
        subdirectories = []
        for entry in entries:
            if (skip_hidden and entry.name.startswith(".")) or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in EXCLUDED_DIRECTORIES:
                    subdirectories.append(Path(entry.path))
            elif entry.name.endswith(suffix) and entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
        pending.extend(reversed(subdirectories))


def vault_markdown_paths(vault: Path) -> Iterator[Path]:
    """Every note Obsidian would show, in a stable order.

    Hidden files and directories (``.obsidian``, ``.kb*``, ``.trash``, transaction snapshots)
    and symlinks are skipped, so every vault walk agrees on what a note is.
    """
    return _walk(vault, ".md", skip_hidden=True)


def semantic_paths(vault: Path) -> list[Path]:
    """Notes plus append-only operation records; the files a transaction protects."""
    paths = list(vault_markdown_paths(vault))
    ledger = vault / LEDGER_DIRECTORY
    if ledger.is_dir() and not ledger.is_symlink():
        paths.extend(_walk(ledger, ".json", skip_hidden=False))
    return paths


def semantic_files(vault: Path) -> dict[str, str]:
    return {
        path.relative_to(vault).as_posix(): path.read_text(encoding="utf-8") for path in semantic_paths(vault)
    }


@dataclass(slots=True)
class Moved:
    """Where ``move_into`` put a file, and any concurrent saves it kept instead of deleting."""

    destination: Path
    conflicts: list[dict[str, str]] = field(default_factory=list)

    def report(self, vault: Path) -> list[dict[str, str]]:
        """Conflicts with paths relative to ``vault``."""

        def relative(value: str) -> str:
            try:
                return Path(value).relative_to(vault).as_posix()
            except ValueError:
                return value

        return [
            {key: relative(value) if key in {"path", "kept"} else value for key, value in conflict.items()}
            for conflict in self.conflicts
        ]


def _free_names(path: Path, identity: str) -> Iterator[Path]:
    """``path`` and then names suffixed with a hash of the full identity (not a shared prefix)."""
    digest = sha256_text(identity)
    yield path
    for length in (8, 16, 64):
        yield path.with_name(f"{path.stem}-{digest[:length]}{path.suffix}")
    for number in range(2, 1000):
        yield path.with_name(f"{path.stem}-{digest[:16]}-{number}{path.suffix}")


def _place(staged: Path, path: Path, identity: str) -> tuple[Path, bool]:
    """Put ``staged`` at the first free name for ``path``; never replaces an existing file.

    Returns the name used and whether it holds exactly the staged bytes. A hard link is the
    same file, so it always does; a copy (where links are unsupported) can miss a write made
    through a descriptor opened before the move.
    """
    for candidate in _free_names(path, identity):
        try:
            os.link(staged, candidate)
            return candidate, True
        except FileExistsError:
            continue
        except OSError:
            # No hard links here: copy into an exclusively created file instead of renaming,
            # because rename would replace a file an external editor created meanwhile.
            try:
                _copy_exclusive(staged, candidate)
            except FileExistsError:
                continue
            return candidate, sha256_file(staged) == sha256_file(candidate)
    raise FileExistsError(f"no free file name for {path.name} in {path.parent}")


def move_into(source: Path, directory: Path, identity: str) -> Moved:
    """Move ``source`` into ``directory`` without replacing a file or discarding a concurrent save.

    The source is first renamed to a private name in its own directory, which captures
    exactly the version at ``source`` at that instant. An editor that saves afterwards
    re-creates ``source``; that file is never touched and is reported as a conflict.
    The captured file is then placed under a free name (hard link, or exclusive copy where
    links are unsupported). If a copy no longer matches the captured file, the captured
    file is kept beside it as a conflict copy instead of being deleted.

    Limits: if the rename fails nothing has changed; if placement fails the captured file
    is put back unless ``source`` was re-created, in which case its private name is
    reported in the error; a crash between the two steps leaves the hidden private file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if source.parent.resolve() == directory.resolve():
        return Moved(source)
    staged = source.with_name(f".{source.name}.kb-move-{uuid.uuid4().hex}")
    record_write(source, None)
    os.rename(source, staged)
    try:
        destination, exact = _place(staged, directory / source.name, identity)
    except BaseException:
        try:
            _link_exclusive(staged, source)
        except FileExistsError as error:
            raise FileExistsError(
                f"could not move {source.name} and it was re-created meanwhile; the moved version is at {staged}"
            ) from error
        record_write(source, sha256_file(source))
        staged.unlink()
        raise
    record_write(destination, sha256_file(destination))
    moved = Moved(destination)
    if exact:
        staged.unlink()
    else:
        kept, exact = _place(staged, destination.with_name(f"{source.stem}.conflict{source.suffix}"), identity)
        record_write(kept, sha256_file(kept))
        if exact:
            staged.unlink()
        else:  # still changing: keep the private file too rather than lose a write
            kept = staged
        moved.conflicts.append(
            {
                "path": str(source),
                "kept": str(kept),
                "reason": "changed while it was being copied; the changed version was kept beside the moved copy",
            }
        )
    if os.path.lexists(source):
        moved.conflicts.append(
            {"path": str(source), "reason": "re-created by another program during the move; left in place"}
        )
    return moved


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy to ``destination`` only if it does not exist (O_EXCL); never replaces a file."""
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination)


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _snapshot(vault: Path, snapshot: Path) -> dict[str, list[int]]:
    """Link every semantic file into ``snapshot``; record size, mtime and inode of the original."""
    files: dict[str, list[int]] = {}
    for path in semantic_paths(vault):
        relative = path.relative_to(vault).as_posix()
        try:
            stat = path.stat()
            _link_or_copy(path, snapshot / relative)
        except FileNotFoundError:
            continue  # removed concurrently; nothing to restore
        files[relative] = [stat.st_size, stat.st_mtime_ns, stat.st_ino]
    return files


def _restore_file(saved: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.kb-restore-{uuid.uuid4().hex}"
    _link_or_copy(saved, temporary)
    os.replace(temporary, target)


class _OwnedLog(dict):
    """Tracked writes that are also appended to ``<journal>.owned``, so crash reconciliation knows them."""

    def __init__(self, vault: Path, path: Path):
        super().__init__()
        self._root = os.path.realpath(vault)
        self._path = path

    def __setitem__(self, key: str, digest: str | None) -> None:
        super().__setitem__(key, digest)
        relative = _protected_relative(self._root, key)
        if relative is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps([relative, digest]) + "\n")


def _owned_paths(journal: Path) -> dict[str, str | None] | None:
    """What a transaction recorded writing before it stopped, or None for journals without a log."""
    path = journal.with_suffix(".owned")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    owned: dict[str, str | None] = {}
    for line in lines:
        try:
            relative, digest = json.loads(line)
        except (ValueError, TypeError):
            continue  # a line cut short by the crash
        if isinstance(relative, str) and (digest is None or isinstance(digest, str)):
            owned[relative] = digest
    return owned


def _protected_relative(root: str, key: str) -> str | None:
    """Vault-relative path when ``key`` names a file transactions protect (see ``semantic_paths``)."""
    try:
        relative = PurePosixPath(Path(key).relative_to(root).as_posix())
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) > 1 and parts[0] == LEDGER_DIRECTORY:
        return relative.as_posix() if relative.suffix == ".json" else None
    if not parts or relative.suffix != ".md" or any(part.startswith(".") for part in parts):
        return None
    if any(part in EXCLUDED_DIRECTORIES for part in parts[:-1]):
        return None
    return relative.as_posix()


def _capture(target: Path, kept: Path) -> bool:
    """Atomically move whatever is at ``target`` to ``kept``; False when nothing was there."""
    kept.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(target, kept)
    except FileNotFoundError:
        return False
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        sibling = target.with_name(f".{target.name}.kb-rollback-{uuid.uuid4().hex}")
        os.rename(target, sibling)  # same directory, so same filesystem
        shutil.copy2(sibling, kept)
        sibling.unlink()
    return True


def _holds_before_state(target: Path, saved: Path) -> bool:
    """True when ``target`` is still (or again) the snapshot's before-state: a KB write that never landed."""
    try:
        if os.path.samefile(target, saved):
            return True
        return target.stat().st_size == saved.stat().st_size and target.read_bytes() == saved.read_bytes()
    except FileNotFoundError:
        return False


def _link_exclusive(source: Path, destination: Path) -> None:
    """Make ``destination`` a link to (or exclusive copy of) ``source``; raise FileExistsError if taken."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        _copy_exclusive(source, destination)


def _rollback(
    vault: Path, identity: str, snapshot: Path, files: dict[str, list[int]], owned: dict[str, str | None]
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Undo the transaction's own writes; never delete or replace a file.

    Only paths the transaction wrote or removed are touched. For each, the current file
    is moved under ``.kb/rolled-back/<transaction>/`` and the before-state is linked back
    without replacing anything. A moved file whose bytes differ from the KB's last write
    for that path was changed by another program after the KB wrote it: a conflict.
    Returns (before-states missing from the snapshot, files set aside, conflicts).
    """
    root = os.path.realpath(vault)
    holding = vault / ROLLED_BACK_DIRECTORY / identity
    unavailable: list[str] = []
    set_aside: list[str] = []
    conflicts: list[dict[str, str]] = []
    for key, expected in sorted(owned.items()):
        relative = _protected_relative(root, key)
        if relative is None:
            continue
        target = vault / relative
        if relative in files and _holds_before_state(target, snapshot / relative):
            continue  # the write was recorded but never landed, or nothing changed
        kept = holding / relative
        if _capture(target, kept):
            preserved = kept.relative_to(vault).as_posix()
            if expected is not None and sha256_file(kept) == expected:
                set_aside.append(preserved)
            else:
                reason = (
                    "changed by another program after the KB wrote it"
                    if expected
                    else "created by another program after the KB removed it"
                )
                conflicts.append({"path": relative, "preserved": preserved, "reason": reason})
        if relative not in files:
            record_write(target, None)  # for an enclosing transaction
            continue
        saved = snapshot / relative
        if not saved.exists():
            unavailable.append(relative)
            continue
        try:
            _link_exclusive(saved, target)
            record_write(target, sha256_file(target))
        except FileExistsError:
            # Saved again while rolling back: keep that save, and the before-state beside the conflicts.
            before, _ = _place(saved, holding / f"{relative}.before", identity)
            conflicts.append(
                {
                    "path": relative,
                    "preserved": before.relative_to(vault).as_posix(),
                    "reason": "saved by another program during rollback; kept, with the before-state preserved",
                }
            )
    return unavailable, set_aside, conflicts


def _record_rollback(
    vault: Path, identity: str, error: BaseException, set_aside: list[str], conflicts: list[dict[str, str]]
) -> None:
    """Write a manifest for the files a rollback moved aside; conflicts stay listed until acknowledged."""
    if not set_aside and not conflicts:
        return
    record = {
        "transaction": identity,
        "created": utc_now(),
        "error": type(error).__name__,
        "set_aside": set_aside,
        "conflicts": conflicts,
        "acknowledged": not conflicts,
    }
    atomic_write(vault / ROLLED_BACK_DIRECTORY / identity / "rollback.json", json.dumps(record, indent=2) + "\n")
    if conflicts:
        files = ", ".join(f"{item['path']} -> {item['preserved']}" for item in conflicts)
        error.add_note(
            f"rollback preserved {len(conflicts)} file(s) another program changed: {files}; "
            f"see `kb reconcile`, then `kb reconcile {identity} --acknowledge`"
        )


def rollback_conflicts(vault: Path) -> list[dict[str, Any]]:
    """Rollbacks that preserved files changed by another program and have not been acknowledged."""
    rows = []
    for path in sorted((vault / ROLLED_BACK_DIRECTORY).glob("*/rollback.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("conflicts") and not row.get("acknowledged"):
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("created", "")))


def acknowledge_rollback(vault: Path, transaction: str) -> dict[str, Any]:
    """Mark a rollback's conflicts reviewed; the preserved files are left where they are."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", transaction):
        raise ValueError("invalid transaction identity")
    with vault_lock(vault):
        path = vault / ROLLED_BACK_DIRECTORY / transaction / "rollback.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"no rollback record for transaction {transaction}") from error
        row["acknowledged"] = True
        row["acknowledged_at"] = utc_now()
        atomic_write(path, json.dumps(row, indent=2) + "\n")
    return {
        "transaction": transaction,
        "acknowledged": True,
        "preserved": [item["preserved"] for item in row.get("conflicts", [])],
    }


def _contained(root: Path, relative: Any) -> Path | None:
    """``root / relative`` when it cannot leave ``root`` and is not a symlink; journals are untrusted input."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or ".." in parts:
        return None
    candidate = root.joinpath(*parts)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
        return None
    return candidate


def _journals(vault: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for path in sorted((vault / JOURNAL_DIRECTORY).glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"unreadable transaction journal requires reconciliation: {path.name}") from error
        rows.append((path, row if isinstance(row, dict) else {}))
    return rows


def _discard(journal: Path) -> None:
    journal.unlink(missing_ok=True)
    journal.with_suffix(".owned").unlink(missing_ok=True)
    shutil.rmtree(journal.with_suffix(""), ignore_errors=True)


def recover_transactions(vault: Path) -> None:
    """Fail closed on an interrupted transaction; never overwrite later human edits."""
    if str(vault.resolve()) in getattr(_LOCAL, "transactions", set()):
        return
    blocking = []
    for path, row in _journals(vault):
        if row.get("state") != "prepared":
            continue
        if "files" in row and row["files"] is None and "before" not in row:
            # Interrupted while snapshotting: nothing had been modified yet.
            _discard(path)
            continue
        blocking.append(path.name)
    if blocking:
        raise ValueError(
            f"interrupted semantic transaction requires reconciliation: {', '.join(blocking)}; run `kb reconcile`"
        )


def pending_transactions(vault: Path) -> list[dict[str, Any]]:
    """Describe interrupted transactions and how each protected file differs from its before-state."""
    result = []
    current = {path.relative_to(vault).as_posix() for path in semantic_paths(vault)}
    for path, row in _journals(vault):
        if row.get("state") != "prepared":
            continue
        files: dict[str, str] = {}
        if "before" in row:  # 0.3.0 journals stored the before-state inline
            before = row.get("before") or {}
            for relative, content in before.items():
                target = _contained(vault, relative)
                if target is None or not isinstance(content, str):
                    files[str(relative)] = "rejected-path"
                elif not target.exists():
                    files[relative] = "missing"
                else:
                    same = target.read_text(encoding="utf-8") == content
                    files[relative] = "unchanged" if same else "changed"
            known = set(before)
        else:
            snapshot = path.with_suffix("")
            recorded = row.get("files") or {}
            for relative in recorded:
                saved, target = _contained(snapshot, relative), _contained(vault, relative)
                if saved is None or target is None:
                    files[str(relative)] = "rejected-path"
                elif not target.exists():
                    files[relative] = "missing" if saved.exists() else "missing-unavailable"
                elif not saved.exists():
                    files[relative] = "unavailable"
                elif os.path.samefile(saved, target) or saved.read_bytes() == target.read_bytes():
                    files[relative] = "unchanged"
                else:
                    files[relative] = "changed"
            known = set(recorded)
        for relative in sorted(current - known):
            files[relative] = "new"
        entry: dict[str, Any] = {
            "journal": path.stem,
            "format": "inline" if "before" in row else "snapshot",
            "files": {key: value for key, value in sorted(files.items()) if value != "unchanged"},
            "unchanged": sum(value == "unchanged" for value in files.values()),
        }
        owned = _owned_paths(path)
        if owned is not None:
            # Only these paths were written by the interrupted transaction; restore-snapshot
            # leaves every other change in place.
            entry["kb_written"] = sorted(owned)
            entry["external"] = sorted(
                relative
                for relative, state in entry["files"].items()
                if relative not in owned and state != "rejected-path"
            )
        result.append(entry)
    return result


def resolve_transaction(vault: Path, journal: str, mode: str, *, delete_new: bool = False) -> dict[str, Any]:
    """Resolve an interrupted transaction under owner control.

    ``accept-current`` keeps the vault as it is. ``restore-snapshot`` restores every
    changed or missing file from the before-state; files created since are listed and
    kept unless ``delete_new`` is set, because they may be notes written after the crash.
    """
    if mode not in {"accept-current", "restore-snapshot"}:
        raise ValueError("mode must be accept-current or restore-snapshot")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", journal):
        raise ValueError("invalid journal identity")
    with vault_lock(vault):
        path = vault / JOURNAL_DIRECTORY / f"{journal}.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("state") != "prepared":
            raise ValueError(f"journal {journal} is not an interrupted transaction")
        status = next(item for item in pending_transactions(vault) if item["journal"] == journal)["files"]
        rejected = sorted(relative for relative, state in status.items() if state == "rejected-path")
        if rejected:
            raise ValueError(
                f"journal {journal} records paths outside the vault or its snapshot ({', '.join(rejected)}); "
                "nothing was changed; inspect the journal manually"
            )
        restored: list[str] = []
        unavailable: list[str] = []
        kept_external: list[str] = []
        conflicts: list[dict[str, str]] = []
        deleted: list[str] = []
        owned = _owned_paths(path)
        holding = vault / ROLLED_BACK_DIRECTORY / journal
        new = sorted(relative for relative, state in status.items() if state == "new")
        if mode == "restore-snapshot":
            before = row.get("before")
            for relative, state in status.items():
                if state not in {"changed", "missing"}:
                    if state in {"unavailable", "missing-unavailable"}:
                        unavailable.append(relative)
                    continue
                if owned is not None and relative not in owned:
                    kept_external.append(relative)  # the KB never wrote it; another program did
                    continue
                target = vault / relative
                if before is not None:
                    atomic_write(target, before[relative])
                    restored.append(relative)
                    continue
                if owned is not None and state == "changed" and sha256_file(target) != owned[relative]:
                    # Changed again after the KB wrote it: keep that version before restoring.
                    kept = holding / relative
                    if _capture(target, kept):
                        preserved = kept.relative_to(vault).as_posix()
                        reason = "changed by another program after the interrupted KB write"
                        conflicts.append({"path": relative, "preserved": preserved, "reason": reason})
                    try:
                        _link_exclusive(path.with_suffix("") / relative, target)
                    except FileExistsError:
                        kept_external.append(relative)
                        continue
                else:
                    _restore_file(path.with_suffix("") / relative, target)
                restored.append(relative)
            if delete_new:
                for relative in new:
                    target = vault / relative
                    if owned is not None and (relative not in owned or sha256_file(target) != owned[relative]):
                        kept_external.append(relative)  # created or changed by another program: never deleted
                        continue
                    target.unlink(missing_ok=True)
                    deleted.append(relative)
        if conflicts:
            record = {"transaction": journal, "created": utc_now(), "error": "reconcile", "set_aside": []}
            record.update(conflicts=conflicts, acknowledged=False)
            atomic_write(holding / "rollback.json", json.dumps(record, indent=2) + "\n")
        _discard(path)
        return {
            "journal": journal,
            "mode": mode,
            "restored": sorted(restored),
            "unavailable": sorted(unavailable),
            "new_files": new,
            "new_files_deleted": bool(deleted),
            "deleted": sorted(deleted),
            "kept_external": sorted(set(kept_external)),
            "conflicts": conflicts,
        }


def prune_local_copies(vault: Path, retention_days: int = LOCAL_COPY_RETENTION_DAYS) -> list[str]:
    """Remove heal backup sets older than ``retention_days``; return what was removed."""
    cutoff = time.time() - retention_days * 86400
    removed = []
    for relative in LOCAL_COPY_DIRECTORIES:
        root = vault / relative
        if not root.is_dir() or root.is_symlink():
            continue
        for entry in root.iterdir():
            try:
                expired = entry.is_dir() and not entry.is_symlink() and entry.stat().st_mtime < cutoff
            except OSError:
                continue
            if expired:
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(f"{relative}/{entry.name}")
    return removed


def _discard_finished_journals(directory: Path) -> None:
    # Earlier releases kept one committed/rolled-back journal per write indefinitely.
    for path in directory.glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8")).get("state")
        except (OSError, ValueError):
            continue
        if state in {"committed", "rolled_back"}:
            path.unlink(missing_ok=True)


@contextmanager
def semantic_transaction(vault: Path):
    with vault_lock(vault):
        recover_transactions(vault)
        directory = vault / JOURNAL_DIRECTORY
        if directory.is_symlink() or not directory.resolve().is_relative_to(vault.resolve()):
            raise ValueError("transaction journal path escaped vault")
        directory.mkdir(parents=True, exist_ok=True)
        _discard_finished_journals(directory)
        prune_local_copies(vault)
        identity = uuid.uuid4().hex
        journal = directory / f"{identity}.json"
        snapshot = directory / identity
        # Journal first; a crash while snapshotting leaves files=None, which is discarded later.
        atomic_write(journal, stable_json({"state": "prepared", "snapshot": identity, "files": None}))
        try:
            files = _snapshot(vault, snapshot)
            atomic_write(journal, stable_json({"state": "prepared", "snapshot": identity, "files": files}))
        except BaseException:
            # Nothing has been modified yet, so an incomplete snapshot is simply discarded.
            _discard(journal)
            raise
        active = getattr(_LOCAL, "transactions", set())
        _LOCAL.transactions = active | {str(vault.resolve())}
        owned = _OwnedLog(vault, journal.with_suffix(".owned"))
        try:
            with tracked_writes(owned):
                yield
        except BaseException as error:
            unavailable, set_aside, conflicts = _rollback(vault, identity, snapshot, files, owned)
            _record_rollback(vault, identity, error, set_aside, conflicts)
            if unavailable:
                row = {"state": "prepared", "snapshot": identity, "files": files, "unavailable": unavailable}
                atomic_write(journal, stable_json(row))
            else:
                _discard(journal)
            raise
        else:
            _discard(journal)
        finally:
            _LOCAL.transactions = active
