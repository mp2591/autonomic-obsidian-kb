"""Single-writer transactions for KB-owned semantic edits.

This lock coordinates KB processes. External editors must still be reconciled; it is
not a security sandbox or a lock respected by Obsidian.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .security import reject_secrets
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


def semantic_files(vault: Path) -> dict[str, str]:
    result = {}
    for path in vault.rglob("*"):
        relative = path.relative_to(vault)
        if any(p.startswith(".") for p in relative.parts[:-1]) and relative.parts[0] != ".kb-memory-events":
            continue
        if path.suffix not in {".md", ".json"} or not path.is_file():
            continue
        if path.suffix == ".json" and relative.parts[0] != ".kb-memory-events":
            continue
        if not path.resolve().is_relative_to(vault.resolve()) or path.is_symlink():
            raise ValueError("semantic file symlink is not supported")
        result[relative.as_posix()] = path.read_text(encoding="utf-8")
    return result


def _restore(vault: Path, before: dict[str, str]) -> None:
    for relative in semantic_files(vault).keys() - before.keys():
        (vault / relative).unlink()
    for relative, content in before.items():
        atomic_write(vault / relative, content)


def recover_transactions(vault: Path) -> None:
    """Fail closed on an interrupted transaction; never overwrite later human edits."""
    if str(vault.resolve()) in getattr(_LOCAL, "transactions", set()):
        return
    directory = vault / ".kb-transactions"
    for path in directory.glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("state") == "prepared":
            raise ValueError(f"interrupted semantic transaction requires reconciliation: {path.name}")


@contextmanager
def semantic_transaction(vault: Path):
    with vault_lock(vault):
        recover_transactions(vault)
        before = semantic_files(vault)
        reject_secrets(before)
        journal = vault / ".kb-transactions" / f"{uuid.uuid4().hex}.json"
        atomic_write(journal, stable_json({"state": "prepared", "before": before}))
        active = getattr(_LOCAL, "transactions", set())
        _LOCAL.transactions = active | {str(vault.resolve())}
        try:
            yield
        except BaseException:
            _restore(vault, before)
            atomic_write(journal, stable_json({"state": "rolled_back"}))
            raise
        else:
            atomic_write(journal, stable_json({"state": "committed"}))
        finally:
            _LOCAL.transactions = active
