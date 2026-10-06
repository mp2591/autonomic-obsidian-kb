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

import contextlib
import ctypes
import errno
import functools
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

from .util import (
    atomic_write,
    record_write,
    sha256_file,
    sha256_text,
    stable_json,
    tracked_key,
    tracked_writes,
    utc_now,
)

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
# Recorded digest for a path the KB meant to write but did not (another program created it first).
UNTOUCHED = ""


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


def _name_limit(directory: Path) -> int:
    """Longest file name, in bytes, the filesystem holding ``directory`` accepts."""
    try:
        return int(os.pathconf(directory, "PC_NAME_MAX"))
    except (OSError, ValueError, AttributeError):  # missing directory, or no pathconf (Windows)
        return 255


def _bounded(directory: Path, stem: str, suffix: str) -> Path:
    """``directory / (stem + suffix)``, cutting the stem on a character boundary to fit the name limit."""
    room = _name_limit(directory) - len(os.fsencode(suffix))
    encoded = os.fsencode(stem)
    if len(encoded) > room:
        stem = encoded[: max(room, 1)].decode("utf-8", "ignore") or "note"
    return directory / f"{stem}{suffix}"


def _private_name(directory: Path, purpose: str) -> Path:
    """A hidden, bounded, unique name in ``directory`` (``.kb-<purpose>-<uuid>``); nothing else uses it."""
    return directory / f".kb-{purpose}-{uuid.uuid4().hex}"


def _free_names(path: Path, identity: str) -> Iterator[Path]:
    """``path`` and then names suffixed with a hash of the full identity (not a shared prefix).

    Suffixed names are cut to the filesystem's name limit, so a note whose name is already
    near the limit still gets a usable alternative.
    """
    digest = sha256_text(identity)
    yield path
    for length in (8, 16, 64):
        yield _bounded(path.parent, path.stem, f"-{digest[:length]}{path.suffix}")
    for number in range(2, 1000):
        yield _bounded(path.parent, path.stem, f"-{digest[:16]}-{number}{path.suffix}")


def _place(staged: Path, path: Path, identity: str, digest: str) -> tuple[Path, bool]:
    """Put ``staged`` (whose bytes hash to ``digest``) at the first free name for ``path``.

    Never replaces an existing file. Each candidate is recorded as a KB write before it is
    created, so a crash right after it lands is still attributed to the transaction, and is
    marked untouched again when nothing was published there.
    Returns the name used and whether it holds exactly the staged bytes. A hard link is the
    same file, so it always does; a copy (where links are unsupported) can miss a write made
    through a descriptor opened before the move.
    """
    for candidate in _free_names(path, identity):
        if os.path.lexists(candidate):
            continue
        record_write(candidate, digest)
        try:
            os.link(staged, candidate)
            return candidate, True
        except FileExistsError:
            record_write(candidate, UNTOUCHED)  # another program created it meanwhile
            continue
        except OSError:
            pass  # no hard link here (unsupported, or another filesystem): copy instead
        try:
            _copy_exclusive(staged, candidate)
        except FileExistsError:
            record_write(candidate, UNTOUCHED)
            continue
        except BaseException:
            record_write(candidate, UNTOUCHED)  # nothing was published at this name
            raise
        return candidate, sha256_file(staged) == sha256_file(candidate)
    raise FileExistsError(f"no free file name for {path.name} in {path.parent}")


def move_into(source: Path, directory: Path, identity: str) -> Moved:
    """Move ``source`` into ``directory`` without replacing a file or discarding a concurrent save.

    The source is first renamed to a private name in its own directory, which captures
    exactly the version at ``source`` at that instant. An editor that saves afterwards
    re-creates ``source``; that file is never touched and is reported as a conflict.
    The captured file is then placed under a free name (hard link, or a copy published
    without replacing anything where links are unsupported). If a copy no longer matches
    the captured file, the captured file is kept as a conflict copy instead of being deleted.

    Inside a transaction, the private name is logged before the rename, and every captured
    version the move cannot place is recorded in the transaction's recovery inventory, so
    ``kb reconcile`` and ``rollback_conflicts`` report where it is.

    Limits: if the rename fails nothing has changed (on Windows it fails while another
    program holds the note open without delete sharing); if placement fails the captured
    file is put back, or, when ``source`` was re-created or cannot be written, it stays at
    its private name, which is reported.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if source.parent.resolve() == directory.resolve():
        return Moved(source)
    for place in {source.parent, directory}:
        if not _publishable(place):
            raise OSError(
                errno.EOPNOTSUPP,
                "this filesystem can neither hard-link nor rename without replacing, so the KB will not move "
                "notes here; nothing was changed",
                str(place),
            )
    context = _transaction_for(source)
    staged = _private_name(source.parent, "move")
    if context is not None:
        context.log_stage(source, staged)
    record_write(source, None)
    os.rename(source, staged)
    digest = sha256_file(staged)
    try:
        destination, exact = _place(staged, directory / source.name, identity, digest)
    except BaseException as error:
        record_write(source, digest)
        try:
            _link_exclusive(staged, source)
        except OSError as failure:
            record_write(source, None)  # the source still lacks the KB's version
            _keep(context, source, staged, f"a move failed and the note could not be put back ({failure})")
            error.add_note(f"could not put {source.name} back ({failure}); the captured version is at {staged}")
            raise error from None
        staged.unlink()
        raise
    moved = Moved(destination)
    if exact:
        staged.unlink()
    else:
        reason = "changed while it was being copied; the changed version was kept beside the moved copy"
        conflict = _bounded(destination.parent, source.stem, f".conflict{source.suffix}")
        try:
            kept, exact = _place(staged, conflict, identity, sha256_file(staged))
        except OSError as failure:
            # Keep the only copy of the changed version where it is, and say where that is.
            if not _keep(context, source, staged, f"{reason}; the conflict copy could not be placed ({failure})"):
                raise OSError(
                    errno.EIO, f"could not record the changed version of {source.name} kept at {staged}"
                ) from failure
            moved.conflicts.append({"path": str(source), "kept": str(staged), "reason": reason})
        else:
            moved.conflicts.append({"path": str(source), "kept": str(kept), "reason": reason})
            if exact:
                staged.unlink()
            else:  # still changing: keep the private file too rather than lose a write
                _keep(context, source, staged, f"{reason}; it changed again while the conflict copy was made")
                moved.conflicts.append({"path": str(source), "kept": str(staged), "reason": reason})
    if os.path.lexists(source):
        moved.conflicts.append(
            {"path": str(source), "reason": "re-created by another program during the move; left in place"}
        )
    return moved


def _rename_noreplace(source: Path, destination: Path) -> bool:
    """Rename ``source`` to ``destination`` only if ``destination`` does not exist.

    Returns False where the platform or filesystem offers no such rename. Raises
    FileExistsError when ``destination`` exists. Windows renames never replace a file;
    Linux uses ``renameat2(RENAME_NOREPLACE)`` and macOS ``renamex_np(RENAME_EXCL)``.
    """
    if os.name == "nt":
        os.rename(source, destination)
        return True
    call = _noreplace_call()
    if call is None:
        return False
    if call(os.fsencode(source), os.fsencode(destination)) == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise FileExistsError(code, os.strerror(code), str(destination))
    if code in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, getattr(errno, "ENOTSUP", errno.EOPNOTSUPP)}:
        return False
    raise OSError(code, os.strerror(code), str(destination))


@functools.cache
def _noreplace_call() -> Any:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    if hasattr(libc, "renameat2"):  # Linux, glibc 2.28+
        renameat2 = libc.renameat2
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        return lambda source, destination: renameat2(-100, source, -100, destination, 1)  # AT_FDCWD, NOREPLACE
    if hasattr(libc, "renamex_np"):  # macOS
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        return lambda source, destination: renamex_np(source, destination, 0x4)  # RENAME_EXCL
    return None


def _publishable(directory: Path) -> bool:
    """True when ``directory`` can receive files without replacing any (a hard link or a no-replace rename)."""
    probe, target = _private_name(directory, "probe"), _private_name(directory, "probe")
    try:
        probe.touch(exist_ok=False)
        try:
            os.link(probe, target)
            return True
        except OSError:
            pass
        try:
            return _rename_noreplace(probe, target)
        except OSError:
            return False
    finally:
        probe.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def _publish(temporary: Path, destination: Path) -> None:
    """Make the complete file at ``temporary`` appear at ``destination`` without replacing anything.

    Raises FileExistsError when ``destination`` is taken. Where the filesystem can neither
    hard-link nor rename without replacing, it refuses rather than risk replacing a file.
    """
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise
    except OSError:
        if not _rename_noreplace(temporary, destination):
            raise OSError(
                errno.EOPNOTSUPP,
                "this filesystem can neither hard-link nor rename without replacing, so the KB will not "
                "place files here",
                str(destination),
            ) from None
        return
    temporary.unlink()


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination`` without ever replacing a file (FileExistsError if taken).

    The copy is written, fsynced and given its metadata under a private name beside
    ``destination`` and published only when complete (see ``_publish``). A failure therefore
    never leaves a partial file at a visible name, and cleanup removes only the private file,
    which no editor can have opened or replaced.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _private_name(destination.parent, "copy")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, temporary)
        _publish(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


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
    temporary = _private_name(target.parent, "restore")
    _link_or_copy(saved, temporary)
    os.replace(temporary, target)


class _OwnedLog(dict):
    """Tracked writes, appended and fsynced to ``<journal>.owned`` before each write lands.

    The log is created with the journal, so an empty log means the KB wrote nothing. The
    first time the transaction writes a path, the version actually there becomes that
    path's before-state: ``snapshot`` while it still matches the transaction snapshot,
    ``replaced`` when another program saved it after the snapshot (a link to that exact
    file is kept beside the journal), or ``absent``. Rollback and crash reconciliation
    restore that version rather than the snapshot taken when the transaction started.
    """

    def __init__(self, vault: Path, journal: Path, files: dict[str, list[int]]):
        super().__init__()
        self.vault = vault
        self.root = os.path.realpath(vault)
        self.snapshot = journal.with_suffix("")
        self.replaced = journal.with_suffix(".replaced")
        self.files = files
        self.path = journal.with_suffix(".owned")
        self.digests: dict[str, str | None] = {}
        self.before: dict[str, str] = {}
        self.stages: list[dict[str, Any]] = []
        _append_durably(self.path, None)

    def __setitem__(self, key: str, digest: str | None) -> None:
        relative = _protected_relative(self.root, key)
        entry: list[Any] = [relative, digest]
        if relative is not None and relative not in self.before:
            saved = self.snapshot / relative if relative in self.files else None
            kind = _before_state(self.vault / relative, saved, self.replaced / relative)
            self.before[relative] = kind
            entry.append(kind)
        super().__setitem__(key, digest)
        if relative is not None:
            self.digests[relative] = digest
            _append_durably(self.path, entry)

    def log_stage(self, source: Path, staged: Path) -> None:
        """Log, before the rename, the private name a move is about to capture ``source`` under."""
        relative = _protected_relative(self.root, tracked_key(source))
        stage = _vault_relative(self.root, staged)
        if relative is not None and stage is not None:
            entry = {"stage": stage, "path": relative, "digest": self.digests.get(relative)}
            _append_durably(self.path, entry)
            self.stages.append(entry)


def _vault_relative(root: str, path: Path) -> str | None:
    try:
        return Path(tracked_key(path)).relative_to(root).as_posix()
    except ValueError:
        return None


class _Transaction:
    """What helpers deep inside a semantic transaction need: its write log and recovery inventory."""

    def __init__(self, vault: Path, identity: str, owned: _OwnedLog):
        self.root = os.path.realpath(vault)
        self.owned = owned
        self.inventory = _Inventory(vault, identity)

    def log_stage(self, source: Path, staged: Path) -> None:
        self.owned.log_stage(source, staged)

    def relative(self, path: Path) -> str:
        return _vault_relative(self.root, path) or str(path)


def _transaction_for(path: Path) -> _Transaction | None:
    """The semantic transaction this thread is running on the vault that contains ``path``, if any."""
    key = tracked_key(path)
    for context in getattr(_LOCAL, "active", {}).values():
        if key.startswith(context.root + os.sep):
            return context
    return None


def _keep(context: _Transaction | None, source: Path, kept: Path, reason: str) -> bool:
    """Record that the only copy of a captured version stays at ``kept``; False if that failed."""
    if context is None:
        return True
    entry = {"op": "kept", "kind": "conflict", "path": context.relative(source), "reason": reason}
    return context.inventory.append({**entry, "preserved": context.relative(kept)}, best_effort=True)


class _Inventory:
    """Append-only, fsynced record of what recovery preserved: ``.kb/rolled-back/<id>/recovery.jsonl``.

    A capture is recorded, with the name reserved for it, before the file moves, and its
    outcome after. Replaying the log resolves a capture that has no outcome against the
    filesystem, so a rollback or reconcile that failed or was killed part-way still reports
    every version it moved. It lives outside the journal, so discarding a journal never
    discards it, and retries append to it instead of replacing it.
    """

    def __init__(self, vault: Path, holding_id: str):
        self.vault = vault
        self.directory = vault / ROLLED_BACK_DIRECTORY / holding_id
        self.path = self.directory / "recovery.jsonl"
        self.last_capture = ""

    def append(self, entry: dict[str, Any], *, best_effort: bool = False) -> bool:
        """Append ``entry``; captures and kept versions get a unique ``id`` that acknowledgement names."""
        if entry.get("op") in {"capture", "kept"}:
            entry = {"id": uuid.uuid4().hex, **entry}
            self.last_capture = entry["id"]
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            _append_durably(self.path, {**entry, "at": utc_now()})
        except OSError:
            if not best_effort:
                raise
            return False
        return True

    def relative(self, path: Path) -> str:
        return path.relative_to(self.vault).as_posix()


def _append_durably(path: Path, entry: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        if entry is not None:
            handle.write(json.dumps(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _before_state(target: Path, saved: Path | None, keep: Path) -> str:
    """Classify the version at ``target`` just before the KB first writes it; keep a newer save."""
    if not os.path.lexists(target):
        return "absent"
    if saved is not None and _holds_before_state(target, saved):
        return "snapshot"
    _link_or_copy(target, keep)  # saved by another program after the snapshot: keep that exact file
    return "replaced"


def _owned_log(
    journal: Path, row: dict[str, Any]
) -> tuple[dict[str, str | None], dict[str, str], list[dict[str, Any]]] | None:
    """(digests, before-states, move stages) a transaction logged; None for journals without a log."""
    try:
        lines = journal.with_suffix(".owned").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ({}, {}, []) if row.get("owned_log") else None
    digests: dict[str, str | None] = {}
    before: dict[str, str] = {}
    stages: list[dict[str, Any]] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line cut short by the crash
        if isinstance(entry, dict) and isinstance(entry.get("stage"), str) and isinstance(entry.get("path"), str):
            stages.append(entry)
            continue
        if not isinstance(entry, list) or len(entry) not in (2, 3):
            continue
        relative, digest = entry[0], entry[1]
        if not isinstance(relative, str) or not (digest is None or isinstance(digest, str)):
            continue
        if len(entry) == 3 and entry[2] in {"snapshot", "replaced", "absent"}:
            before.setdefault(relative, entry[2])
        digests[relative] = digest
    return digests, before, stages


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


def _capture(target: Path, kept: Path, inventory: _Inventory, relative: str, expected: str | None) -> Path | None:
    """Move whatever is at ``target`` to a free name for ``kept``; None when nothing was there.

    The capture is recorded in ``inventory``, with the reserved name, before anything moves;
    if that record cannot be written this raises and ``target`` is left untouched.
    """
    if not os.path.lexists(target):
        return None  # nothing to capture, so nothing to record
    kept.parent.mkdir(parents=True, exist_ok=True)
    destination = next(name for name in _free_names(kept, relative) if not os.path.lexists(name))
    via = _private_name(target.parent, "rollback")
    capture = uuid.uuid4().hex
    intent = {"op": "capture", "id": capture, "path": relative, "to": inventory.relative(destination)}
    inventory.append({**intent, "expected": expected, "via": inventory.relative(via)})
    try:
        os.rename(target, destination)
    except FileNotFoundError:
        inventory.append({"op": "outcome", "id": capture, "to": intent["to"], "kind": "absent"}, best_effort=True)
        return None
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        os.rename(target, via)  # same directory, so same filesystem
        _copy_exclusive(via, destination)
        via.unlink()
    return destination


def _holds_before_state(target: Path, saved: Path) -> bool:
    """True when ``target`` is still (or again) the before-state in ``saved``: a KB write that never landed."""
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


@dataclass(slots=True)
class _Rollback:
    restored: list[str] = field(default_factory=list)
    set_aside: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def _rollback(
    vault: Path,
    holding_id: str,
    journal: Path,
    digests: dict[str, str | None],
    before: dict[str, str],
    files: dict[str, Any],
) -> _Rollback:
    """Undo the transaction's own writes; never delete or replace a file.

    Only paths the transaction wrote or removed are touched, each independently: a path
    that fails is reported and the rest are still undone. The current file is moved under
    ``.kb/rolled-back/<holding_id>/`` (recorded in that directory's recovery inventory first)
    and the path's before-state is linked back without replacing anything. A moved file whose
    bytes differ from the KB's last write for that path was changed by another program: a
    conflict. A path whose before-state is unavailable is left exactly as it is.
    """
    sources = {"snapshot": journal.with_suffix(""), "replaced": journal.with_suffix(".replaced")}
    inventory = _Inventory(vault, holding_id)
    result = _Rollback()
    for relative, expected in sorted(digests.items()):
        if expected == UNTOUCHED:
            continue
        kind = before.get(relative) or ("snapshot" if relative in files else "absent")
        source = sources[kind] / relative if kind in sources else None
        try:
            _undo(vault, relative, expected, source, inventory, result)
        except OSError as error:
            failure = {"path": relative, "error": f"{type(error).__name__}: {error}"}
            result.errors.append(failure)
            inventory.append({"op": "error", **failure}, best_effort=True)
    return result


def _undo(
    vault: Path, relative: str, expected: str | None, source: Path | None, inventory: _Inventory, result: _Rollback
) -> None:
    target = vault / relative
    if source is not None and _holds_before_state(target, source):
        return  # the write never landed, or the path already holds its before-state
    if source is not None and not source.exists():
        result.unavailable.append(relative)  # nothing to restore from: leave the live file alone
        return
    if source is None and not os.path.lexists(target):
        return
    kept = _capture(target, inventory.directory / relative, inventory, relative, expected)
    if kept is not None:
        preserved = inventory.relative(kept)
        capture = inventory.last_capture
        if expected is not None and sha256_file(kept) == expected:
            outcome = {"op": "outcome", "id": capture, "to": preserved, "kind": "set_aside"}
            inventory.append(outcome, best_effort=True)
            result.set_aside.append(preserved)
        else:
            reason = (
                "changed by another program after the KB wrote it"
                if expected
                else "created by another program after the KB removed it"
            )
            outcome = {"op": "outcome", "id": capture, "to": preserved, "kind": "conflict", "reason": reason}
            inventory.append(outcome, best_effort=True)
            result.conflicts.append({"path": relative, "preserved": preserved, "reason": reason})
    if source is None:
        record_write(target, None)  # for an enclosing transaction
        return
    record_write(target, sha256_file(source))
    try:
        _link_exclusive(source, target)
    except FileExistsError:
        # Saved again while rolling back: keep that save, and the before-state beside the conflicts.
        name = _bounded(inventory.directory / Path(relative).parent, Path(relative).name, ".before")
        copy, _ = _place(source, name, relative, sha256_file(source))
        reason = "saved by another program during rollback; kept, with the before-state preserved"
        conflict = {"path": relative, "preserved": inventory.relative(copy), "reason": reason}
        inventory.append({"op": "kept", "kind": "conflict", **conflict}, best_effort=True)
        result.conflicts.append(conflict)
        return
    result.restored.append(relative)


def _report_rollback(holding_id: str, result: _Rollback, error: BaseException) -> None:
    """Tell the caller what a rollback preserved or could not restore (the inventory already records it)."""
    with contextlib.suppress(AttributeError):  # read by callers that report rollbacks (heal)
        error.kb_rollback = {  # type: ignore[attr-defined]
            "transaction": holding_id,
            "set_aside": result.set_aside,
            "conflicts": result.conflicts,
            "errors": result.errors,
            "unavailable": result.unavailable,
        }
    if result.conflicts:
        files = ", ".join(f"{item['path']} -> {item['preserved']}" for item in result.conflicts)
        error.add_note(
            f"rollback preserved {len(result.conflicts)} file(s) another program changed: {files}; "
            f"see `kb reconcile`, then `kb reconcile {holding_id} --acknowledge`"
        )
    unresolved = [item["path"] for item in result.errors] + result.unavailable
    if unresolved:
        error.add_note(
            f"rollback could not restore {', '.join(unresolved)}; the journal is kept, run `kb reconcile`"
        )


def _replay(vault: Path, path: Path) -> dict[str, Any]:
    """Summarize one recovery inventory, resolving interrupted captures against the filesystem.

    Only preserved files that still exist are listed. Each captured or kept version has its
    own id, and acknowledgement names ids, so a later capture that reuses a freed name is
    listed again rather than inheriting an earlier acknowledgement.
    """
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line cut short by a crash
        if isinstance(entry, dict):
            entries.append(entry)

    def key(entry: dict[str, Any]) -> str:
        return str(entry.get("id") or entry.get("to", ""))

    captures = {key(entry): entry for entry in entries if entry.get("op") == "capture" and "to" in entry}
    outcomes = {key(entry): entry for entry in entries if entry.get("op") == "outcome" and "to" in entry}
    acknowledged = {
        str(value) for entry in entries if entry.get("op") == "acknowledged" for value in entry.get("ids", [])
    }
    set_aside: list[str] = []
    found: dict[str, dict[str, str]] = {}
    for identity, intent in captures.items():
        outcome = outcomes.get(identity)
        if outcome is not None:
            kind, preserved, reason = outcome.get("kind"), str(intent["to"]), str(outcome.get("reason", ""))
            if not _held(vault, preserved):
                continue
        else:
            # Recorded but never concluded (bookkeeping failed or the process died): look.
            names = [name for name in (intent.get("to"), intent.get("via")) if isinstance(name, str)]
            preserved = next((name for name in names if _held(vault, name)), None)
            if preserved is None:
                continue
            expected = intent.get("expected")
            own = isinstance(expected, str) and sha256_file(vault / preserved) == expected
            kind = "set_aside" if own else "conflict"
            reason = "preserved by a rollback or reconcile that did not finish its bookkeeping"
        if kind == "set_aside":
            set_aside.append(preserved)
        elif kind == "conflict":
            item = {"id": identity, "path": str(intent.get("path", "")), "preserved": preserved, "reason": reason}
            found[identity] = item
    seen: set[str] = set()
    for entry in entries:
        preserved = entry.get("preserved")
        if entry.get("op") != "kept" or preserved in seen or not _held(vault, preserved):
            continue
        seen.add(str(preserved))  # the same private file may be recorded by a move and by recovery
        item = {"id": key(entry), **{name: str(entry.get(name, "")) for name in ("path", "preserved", "reason")}}
        if entry.get("kind") == "set_aside":
            set_aside.append(item["preserved"])
        else:
            found[item["id"]] = item
    pending = [item for identity, item in found.items() if identity not in acknowledged]
    errors = [
        {"path": str(entry.get("path", "")), "error": str(entry.get("error", ""))}
        for entry in entries
        if entry.get("op") == "error"
    ]
    return {
        "transaction": path.parent.name,
        "created": str(entries[0].get("at", "")) if entries else "",
        "set_aside": set_aside,
        "conflicts": pending,
        "errors": errors,
        "acknowledged": not pending,
    }


def _held(vault: Path, relative: Any) -> bool:
    """True when ``relative`` (untrusted, from an inventory) names a file inside the vault."""
    candidate = _contained(vault, relative)
    return candidate is not None and candidate.is_file()


def rollback_conflicts(vault: Path) -> list[dict[str, Any]]:
    """Recovery inventories that preserved versions another program wrote, not yet acknowledged."""
    rows = [_replay(vault, path) for path in sorted((vault / ROLLED_BACK_DIRECTORY).glob("*/recovery.jsonl"))]
    return sorted((row for row in rows if row["conflicts"]), key=lambda row: row["created"])


def acknowledge_rollback(vault: Path, transaction: str) -> dict[str, Any]:
    """Mark the preserved versions listed now as reviewed; the files are left where they are."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", transaction):
        raise ValueError("invalid transaction identity")
    with vault_lock(vault):
        inventory = _Inventory(vault, transaction)
        if not inventory.path.is_file():
            raise ValueError(f"no recovery record for transaction {transaction}")
        conflicts = _replay(vault, inventory.path)["conflicts"]
        inventory.append({"op": "acknowledged", "ids": [item["id"] for item in conflicts]})
    return {"transaction": transaction, "acknowledged": True, "preserved": [item["preserved"] for item in conflicts]}


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
    shutil.rmtree(journal.with_suffix(".replaced"), ignore_errors=True)


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
        log = _owned_log(path, row) if "before" not in row else None
        if log is not None:
            # Only these paths were written by the interrupted transaction; restore-snapshot
            # leaves every other change in place.
            owned = {relative for relative, digest in log[0].items() if digest != UNTOUCHED}
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

    ``accept-current`` keeps the vault as it is. ``restore-snapshot`` undoes the
    interrupted transaction. With a write log (every journal this release creates) it
    runs the same rollback as a handled failure: only paths the KB wrote are restored,
    to the version the KB replaced; what is there is moved under
    ``.kb/rolled-back/<journal>-reconcile/`` (a conflict when another program changed it);
    nothing is deleted, so ``delete_new`` has no effect. Journals from earlier releases
    have no log: every changed or missing file is restored from the snapshot, and files
    created since are kept unless ``delete_new`` is set.
    """
    if mode not in {"accept-current", "restore-snapshot"}:
        raise ValueError("mode must be accept-current or restore-snapshot")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", journal):
        raise ValueError("invalid journal identity")
    with vault_lock(vault):
        path = vault / JOURNAL_DIRECTORY / f"{journal}.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("state") != "prepared":
            raise ValueError(f"journal {journal} is not an interrupted transaction")
        status = next(item for item in pending_transactions(vault) if item["journal"] == journal)["files"]
        log = _owned_log(path, row) if "before" not in row else None
        root = os.path.realpath(vault)
        rejected = sorted(relative for relative, state in status.items() if state == "rejected-path")
        if log is not None:
            rejected += sorted(
                str(relative)
                for relative in log[0]
                if _protected_relative(root, os.path.join(root, relative)) != relative
                or _contained(vault, relative) is None
            )
        if rejected:
            raise ValueError(
                f"journal {journal} records paths outside the vault or its snapshot ({', '.join(rejected)}); "
                "nothing was changed; inspect the journal manually"
            )
        new = sorted(relative for relative, state in status.items() if state == "new")
        if mode == "accept-current":
            _discard(path)
            return {"journal": journal, "mode": mode, "restored": [], "new_files": new, "new_files_deleted": False}
        if log is not None:
            digests, before, stages = log
            holding = f"{journal}-reconcile"
            result = _rollback(vault, holding, path, digests, before, row.get("files") or {})
            _report_stranded_moves(vault, holding, stages, result)
            unresolved = [f"{item['path']} ({item['error']})" for item in result.errors]
            unresolved += [f"{relative} (its before-state is unavailable)" for relative in result.unavailable]
            if unresolved:
                raise ValueError(
                    f"could not restore {', '.join(unresolved)}; the journal is kept and the vault stays "
                    "blocked; fix the cause and run this again"
                )
            _discard(path)
            return {
                "journal": journal,
                "mode": mode,
                "restored": sorted(result.restored),
                "set_aside": result.set_aside,
                "conflicts": result.conflicts,
                "unavailable": sorted(result.unavailable),
                "kept_external": sorted(relative for relative in status if relative not in digests),
                "new_files": new,
                "new_files_deleted": False,
            }
        restored: list[str] = []
        unavailable: list[str] = []
        inline = row.get("before")
        for relative, state in status.items():
            if state not in {"changed", "missing"}:
                if state in {"unavailable", "missing-unavailable"}:
                    unavailable.append(relative)
                continue
            target = vault / relative
            if inline is not None:
                atomic_write(target, inline[relative])
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
            "new_files_deleted": bool(delete_new),
        }


def _report_stranded_moves(vault: Path, holding: str, stages: list[dict[str, Any]], result: _Rollback) -> None:
    """Record notes an interrupted move left at its private name, so recovery reports them.

    A note that cannot be recorded becomes an error, which keeps the journal (and the
    write log naming the private file) for a retry.
    """
    inventory = _Inventory(vault, holding)
    recorded = {
        str(entry.get("preserved"))
        for log in (vault / ROLLED_BACK_DIRECTORY).glob("*/recovery.jsonl")
        for entry in _entries(log)
        if entry.get("op") == "kept"
    }
    for stage in stages:
        if not _held(vault, stage["stage"]) or stage["stage"] in recorded:
            continue
        digest = stage.get("digest")
        own = isinstance(digest, str) and sha256_file(vault / stage["stage"]) == digest
        reason = "captured by a move that did not finish" + ("" if own else "; it holds another program's version")
        entry = {"path": stage["path"], "preserved": stage["stage"], "reason": reason}
        if not inventory.append({"op": "kept", "kind": "set_aside" if own else "conflict", **entry}, best_effort=True):
            failure = f"could not record the note left at {stage['stage']}"
            result.errors.append({"path": stage["path"], "error": failure})
        elif own:
            result.set_aside.append(stage["stage"])
        else:
            result.conflicts.append(entry)


def _entries(log: Path) -> list[dict[str, Any]]:
    entries = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            entry = json.loads(line)
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


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
        if not _publishable(vault):
            # Rollback could not restore a file here without risking replacing someone's save.
            raise OSError(
                errno.EOPNOTSUPP,
                "the vault's filesystem can neither hard-link nor rename without replacing, so the KB will not "
                "write to it; nothing was changed",
                str(vault),
            )
        _discard_finished_journals(directory)
        prune_local_copies(vault)
        identity = uuid.uuid4().hex
        journal = directory / f"{identity}.json"
        snapshot = directory / identity
        # Journal first; a crash while snapshotting leaves files=None, which is discarded later.
        atomic_write(journal, stable_json({"state": "prepared", "snapshot": identity, "files": None}))
        try:
            files = _snapshot(vault, snapshot)
            owned = _OwnedLog(vault, journal, files)  # exists before the journal can block anything
            row = {"state": "prepared", "snapshot": identity, "files": files, "owned_log": True}
            atomic_write(journal, stable_json(row))
        except BaseException:
            # Nothing has been modified yet, so an incomplete snapshot is simply discarded.
            _discard(journal)
            raise
        active = getattr(_LOCAL, "transactions", set())
        _LOCAL.transactions = active | {str(vault.resolve())}
        contexts = getattr(_LOCAL, "active", {})
        _LOCAL.active = {**contexts, str(vault.resolve()): _Transaction(vault, identity, owned)}
        try:
            with tracked_writes(owned):
                yield
        except BaseException as error:
            result = _rollback(vault, identity, journal, owned.digests, owned.before, files)
            _report_stranded_moves(vault, identity, owned.stages, result)
            _report_rollback(identity, result, error)
            keep = bool(result.unavailable or result.errors)
            if keep:
                # Leave the journal (and its write log) for `kb reconcile` to finish the rollback.
                row.update(unavailable=result.unavailable, rollback_errors=result.errors)
                with contextlib.suppress(OSError):  # the prepared journal on disk blocks the vault either way
                    atomic_write(journal, stable_json(row))
            else:
                _discard(journal)
            raise
        else:
            _discard(journal)
        finally:
            _LOCAL.transactions = active
            _LOCAL.active = contexts
