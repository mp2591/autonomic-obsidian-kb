from __future__ import annotations

import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(slots=True)
class GitContext:
    root: str = ""
    branch: str = ""
    remote: str = ""
    head: str = ""
    changed_paths: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.changed_paths is None:
            self.changed_paths = []

    def to_dict(self) -> dict:
        return asdict(self)


def _git(cwd: Path, *args: str, timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def inspect_git(start: str | Path | None) -> GitContext:
    if not start:
        return GitContext()
    cwd = Path(start).expanduser().resolve()
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return GitContext()
    root_path = Path(root)
    changed: set[str] = set()
    for args in (
        ("diff", "--name-only", "HEAD"),
        ("diff", "--name-only", "--cached"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        output = _git(root_path, *args)
        changed.update(line.strip() for line in output.splitlines() if line.strip())
    return GitContext(
        root=root,
        branch=_git(root_path, "branch", "--show-current"),
        remote=_git(root_path, "config", "--get", "remote.origin.url"),
        head=_git(root_path, "rev-parse", "HEAD"),
        changed_paths=sorted(changed),
    )


def recent_commit_summary(start: str | Path | None, count: int = 5) -> str:
    if not start:
        return ""
    cwd = Path(start).resolve()
    return _git(cwd, "log", f"-{count}", "--pretty=format:%h %s")
