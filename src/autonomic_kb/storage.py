"""Single-writer transactions for KB-owned semantic edits.

This lock coordinates KB processes. External editors must still be reconciled; it is
not a security sandbox or a lock respected by Obsidian.

A transaction snapshots semantic files as hard links (a copy only where the filesystem
cannot link), so starting one costs one link per file rather than reading and journaling
the whole vault. KB writes replace files atomically or rename them, which leaves the
linked inode holding the before-state. Rollback restores what the KB replaced and keeps
external in-place edits (a file that still has its recorded inode). Only a crash leaves a
prepared journal behind; ``kb reconcile`` resolves it under owner control.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from .util import atomic_write, sha256_text, stable_json

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
# Heal backups and rolled-back files keep unredacted originals; they are local recovery
# copies, not an archive, so they expire.
LOCAL_COPY_DIRECTORIES = (".kb/backups", ".kb/rolled-back")
LOCAL_COPY_RETENTION_DAYS = 30
LEDGER_DIRECTORY = ".kb-memory-events"
JOURNAL_DIRECTORY = ".kb-transactions"


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


def move_into(source: Path, directory: Path, identity: str) -> Path:
    """Move ``source`` into ``directory`` without ever replacing an existing file.

    A taken name gets a suffix from a hash of the note's full identity (not a prefix of it,
    which most memory IDs share). Hard link then unlink makes the move no-clobber on
    filesystems that support links.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if source.parent.resolve() == directory.resolve():
        return source
    digest = sha256_text(identity)
    names = [source.name]
    names += [f"{source.stem}-{digest[:length]}{source.suffix}" for length in (8, 16, 64)]
    names += [f"{source.stem}-{digest[:16]}-{number}{source.suffix}" for number in range(2, 1000)]
    for name in names:
        destination = directory / name
        try:
            os.link(source, destination)
        except FileExistsError:
            continue
        except OSError:
            if destination.exists() or destination.is_symlink():
                continue
            os.rename(source, destination)
            return destination
        source.unlink()
        return destination
    raise FileExistsError(f"no free file name for {source.name} in {directory}")


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


def _set_aside(vault: Path, identity: str, relative: str) -> None:
    """Move a file created during a failed transaction to local runtime state instead of deleting it."""
    destination = vault / ".kb" / "rolled-back" / identity / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(vault / relative, destination)


def _restore(vault: Path, identity: str, snapshot: Path, files: dict[str, list[int]]) -> list[str]:
    """Undo KB changes; return files whose before-state is missing from the snapshot.

    KB writes replace files (new inode) or rename them. A file that still has its
    recorded inode was never replaced by the transaction, so any change to it is an
    external in-place edit (for example Obsidian saving) and is kept.
    """
    unavailable: list[str] = []
    for path in semantic_paths(vault):
        relative = path.relative_to(vault).as_posix()
        if relative not in files:
            _set_aside(vault, identity, relative)
    for relative, recorded in sorted(files.items()):
        saved = snapshot / relative
        target = vault / relative
        inode = recorded[2] if len(recorded) > 2 else None
        try:
            current = target.stat()
        except FileNotFoundError:
            current = None
        if current is not None and inode is not None and current.st_ino == inode:
            continue
        if current is not None and saved.exists() and os.path.samefile(saved, target):
            continue
        if not saved.exists():
            unavailable.append(relative)
            continue
        _restore_file(saved, target)
    return unavailable


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
        result.append(
            {
                "journal": path.stem,
                "format": "inline" if "before" in row else "snapshot",
                "files": {key: value for key, value in sorted(files.items()) if value != "unchanged"},
                "unchanged": sum(value == "unchanged" for value in files.values()),
            }
        )
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
        new = sorted(relative for relative, state in status.items() if state == "new")
        if mode == "restore-snapshot":
            before = row.get("before")
            for relative, state in status.items():
                if state not in {"changed", "missing"}:
                    if state in {"unavailable", "missing-unavailable"}:
                        unavailable.append(relative)
                    continue
                target = vault / relative
                if before is not None:
                    atomic_write(target, before[relative])
                else:
                    _restore_file(path.with_suffix("") / relative, target)
                restored.append(relative)
            if delete_new:
                for relative in new:
                    (vault / relative).unlink(missing_ok=True)
        _discard(path)
        return {
            "journal": journal,
            "mode": mode,
            "restored": sorted(restored),
            "unavailable": sorted(unavailable),
            "new_files": new,
            "new_files_deleted": bool(delete_new and mode == "restore-snapshot"),
        }


def prune_local_copies(vault: Path, retention_days: int = LOCAL_COPY_RETENTION_DAYS) -> list[str]:
    """Remove backup and rolled-back sets older than ``retention_days``; return what was removed."""
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
        try:
            yield
        except BaseException:
            unavailable = _restore(vault, identity, snapshot, files)
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
