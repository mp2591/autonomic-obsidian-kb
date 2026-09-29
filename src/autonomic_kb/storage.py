"""Single-writer transactions for KB-owned semantic edits.

This lock coordinates KB processes. External editors must still be reconciled; it is
not a security sandbox or a lock respected by Obsidian.

A transaction snapshots semantic files as hard links (a copy only where the filesystem
cannot link), so starting one costs one link per file rather than reading and journaling
the whole vault. KB writes replace files atomically, which leaves the linked inode holding
the before-state. An in-place edit during a failed transaction cannot be undone from a
link, so rollback then fails closed and leaves the journal for owner reconciliation.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .util import atomic_write, stable_json

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
LEDGER_DIRECTORY = ".kb-memory-events"
JOURNAL_DIRECTORY = ".kb-transactions"


def vault_markdown_paths(vault: Path) -> Iterator[Path]:
    """Every note Obsidian would show, in sorted order.

    Hidden files and directories (``.obsidian``, ``.kb*``, ``.trash``, transaction snapshots)
    and symlinks are skipped, so every vault walk agrees on what a note is.
    """
    root = vault.resolve()
    for directory, subdirectories, filenames in os.walk(vault):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith(".")
            and name not in EXCLUDED_DIRECTORIES
            and not os.path.islink(os.path.join(directory, name))
        )
        for name in sorted(filenames):
            if name.startswith(".") or not name.endswith(".md"):
                continue
            path = Path(directory) / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            yield path


def semantic_paths(vault: Path) -> list[Path]:
    """Notes plus append-only operation records; the files a transaction protects."""
    paths = list(vault_markdown_paths(vault))
    ledger = vault / LEDGER_DIRECTORY
    if ledger.is_dir() and not ledger.is_symlink():
        paths.extend(
            path for path in sorted(ledger.rglob("*.json")) if path.is_file() and not path.is_symlink()
        )
    return paths


def semantic_files(vault: Path) -> dict[str, str]:
    return {
        path.relative_to(vault).as_posix(): path.read_text(encoding="utf-8") for path in semantic_paths(vault)
    }


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _snapshot(vault: Path, snapshot: Path) -> dict[str, list[int]]:
    files: dict[str, list[int]] = {}
    for path in semantic_paths(vault):
        relative = path.relative_to(vault).as_posix()
        try:
            _link_or_copy(path, snapshot / relative)
        except FileNotFoundError:
            continue  # removed concurrently; nothing to restore
        stat = (snapshot / relative).stat()
        files[relative] = [stat.st_size, stat.st_mtime_ns]
    return files


def _restore(vault: Path, snapshot: Path, files: dict[str, list[int]]) -> list[str]:
    """Restore the before-state; return paths that could not be restored faithfully."""
    unrecoverable: list[str] = []
    for path in semantic_paths(vault):
        if path.relative_to(vault).as_posix() not in files:
            path.unlink()
    for relative, (size, mtime_ns) in sorted(files.items()):
        saved = snapshot / relative
        target = vault / relative
        try:
            stat = saved.stat()
        except OSError:
            unrecoverable.append(relative)
            continue
        if (stat.st_size, stat.st_mtime_ns) != (size, mtime_ns):
            unrecoverable.append(relative)  # edited in place through the shared inode
            continue
        if target.exists() and os.path.samefile(saved, target):
            continue
        temporary = target.parent / f".{target.name}.kb-restore-{uuid.uuid4().hex}"
        _link_or_copy(saved, temporary)
        os.replace(temporary, target)
    return unrecoverable


def recover_transactions(vault: Path) -> None:
    """Fail closed on an interrupted transaction; never overwrite later human edits."""
    if str(vault.resolve()) in getattr(_LOCAL, "transactions", set()):
        return
    directory = vault / JOURNAL_DIRECTORY
    for path in directory.glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("state") == "prepared":
            raise ValueError(f"interrupted semantic transaction requires reconciliation: {path.name}")


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
        identity = uuid.uuid4().hex
        journal = directory / f"{identity}.json"
        snapshot = directory / identity
        # Journal first: a crash while snapshotting still fails closed.
        atomic_write(journal, stable_json({"state": "prepared", "snapshot": identity, "files": None}))
        try:
            files = _snapshot(vault, snapshot)
            atomic_write(journal, stable_json({"state": "prepared", "snapshot": identity, "files": files}))
        except BaseException:
            # Nothing has been modified yet, so an incomplete snapshot is simply discarded.
            journal.unlink(missing_ok=True)
            shutil.rmtree(snapshot, ignore_errors=True)
            raise
        active = getattr(_LOCAL, "transactions", set())
        _LOCAL.transactions = active | {str(vault.resolve())}
        try:
            yield
        except BaseException:
            unrecoverable = _restore(vault, snapshot, files)
            if unrecoverable:
                atomic_write(
                    journal,
                    stable_json(
                        {"state": "prepared", "snapshot": identity, "files": files, "unrecoverable": unrecoverable}
                    ),
                )
            else:
                journal.unlink(missing_ok=True)
                shutil.rmtree(snapshot, ignore_errors=True)
            raise
        else:
            journal.unlink(missing_ok=True)
            shutil.rmtree(snapshot, ignore_errors=True)
        finally:
            _LOCAL.transactions = active
