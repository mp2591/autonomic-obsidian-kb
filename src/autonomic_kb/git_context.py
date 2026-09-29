from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .util import sha256_text


@dataclass(slots=True)
class GitContext:
    root: str = ""
    branch: str = ""
    upstream: str = ""
    remote: str = ""
    repository_id: str = ""
    head: str = ""
    merge_base: str = ""
    changed_paths: list[str] = field(default_factory=list)
    staged_paths: list[str] = field(default_factory=list)
    unstaged_paths: list[str] = field(default_factory=list)
    untracked_paths: list[str] = field(default_factory=list)
    renamed_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _git(cwd: Path, *args: str, timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def canonical_remote(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    value = re.sub(r"\.git$", "", value)
    if value.startswith("git@") and ":" in value:
        host, path = value.split(":", 1)
        value = f"https://{host.split('@', 1)[1]}/{path}"
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        # Credentials and transient query arguments are never repository identity.
        netloc = parsed.netloc.rsplit("@", 1)[-1].lower()
        value = urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
    return value.rstrip("/").lower()


def repository_identity(root: Path, remote: str) -> str:
    canonical = canonical_remote(remote)
    if canonical:
        return f"git:{canonical}"
    return f"local:{sha256_text(str(root.resolve()))[:20]}"


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def inspect_git(start: str | Path | None) -> GitContext:
    if not start:
        return GitContext()
    cwd = Path(start).expanduser().resolve()
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return GitContext()
    root_path = Path(root)
    remote = _git(root_path, "config", "--get", "remote.origin.url")
    branch = _git(root_path, "branch", "--show-current")
    upstream = _git(root_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    head = _git(root_path, "rev-parse", "HEAD")
    merge_base = _git(root_path, "merge-base", "HEAD", upstream) if upstream else ""
    staged = _lines(_git(root_path, "diff", "--name-only", "--cached"))
    unstaged = _lines(_git(root_path, "diff", "--name-only"))
    untracked = _lines(_git(root_path, "ls-files", "--others", "--exclude-standard"))
    renamed: dict[str, str] = {}
    for line in _lines(_git(root_path, "diff", "--name-status", "--find-renames", "HEAD")):
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0].startswith("R"):
            renamed[fields[1]] = fields[2]
    changed = sorted(set(staged) | set(unstaged) | set(untracked) | set(renamed.values()))
    return GitContext(
        root=root,
        branch=branch,
        upstream=upstream,
        remote=remote,
        repository_id=repository_identity(root_path, remote),
        head=head,
        merge_base=merge_base,
        changed_paths=changed,
        staged_paths=staged,
        unstaged_paths=unstaged,
        untracked_paths=untracked,
        renamed_paths=renamed,
    )


def recent_commit_summary(start: str | Path | None, count: int = 5) -> str:
    if not start:
        return ""
    return _git(Path(start).resolve(), "log", f"-{count}", "--pretty=format:%h %s")


def is_ancestor(start: str | Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    cwd = Path(start).resolve()
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=cwd, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
