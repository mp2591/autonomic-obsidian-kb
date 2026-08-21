from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """# autonomic-obsidian-kb configuration\n\n[retrieval]\ndefault_budget = 1000\nminimum_score = 0.28\nmax_candidates = 200\n\n[lifecycle]\npromotion_threshold = 0.62\nstale_after_days = 120\narchive_after_days = 365\n\n[security]\nallow_untrusted = false\nallow_cross_repo = false\n\n[paths]\ninbox = \"00-inbox\"\narchive = \"99-archive\"\nquarantine = \"98-quarantine\"\n"""


@dataclass(slots=True)
class KBConfig:
    vault: Path
    repo: Path | None = None
    default_budget: int = 1000
    minimum_score: float = 0.28
    max_candidates: int = 200
    promotion_threshold: float = 0.62
    stale_after_days: int = 120
    archive_after_days: int = 365
    allow_untrusted: bool = False
    allow_cross_repo: bool = False
    inbox_dir: str = "00-inbox"
    archive_dir: str = "99-archive"
    quarantine_dir: str = "98-quarantine"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def runtime_dir(self) -> Path:
        return self.vault / ".kb"

    @property
    def index_path(self) -> Path:
        return self.runtime_dir / "index.sqlite3"

    @property
    def log_path(self) -> Path:
        return self.runtime_dir / "actions.jsonl"

    @property
    def config_path(self) -> Path:
        return self.vault / "kb.toml"

    def ensure_runtime(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        (self.vault / self.inbox_dir).mkdir(parents=True, exist_ok=True)
        (self.vault / self.archive_dir).mkdir(parents=True, exist_ok=True)
        (self.vault / self.quarantine_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, vault: str | Path | None = None, repo: str | Path | None = None) -> "KBConfig":
        vault_path = find_vault(vault)
        repo_path = Path(repo).expanduser().resolve() if repo else find_repo(vault_path)
        raw: dict[str, Any] = {}
        config_path = vault_path / "kb.toml"
        if config_path.exists():
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)
        retrieval = raw.get("retrieval", {})
        lifecycle = raw.get("lifecycle", {})
        security = raw.get("security", {})
        paths = raw.get("paths", {})
        config = cls(
            vault=vault_path,
            repo=repo_path,
            default_budget=int(retrieval.get("default_budget", 1000)),
            minimum_score=float(retrieval.get("minimum_score", 0.28)),
            max_candidates=int(retrieval.get("max_candidates", 200)),
            promotion_threshold=float(lifecycle.get("promotion_threshold", 0.62)),
            stale_after_days=int(lifecycle.get("stale_after_days", 120)),
            archive_after_days=int(lifecycle.get("archive_after_days", 365)),
            allow_untrusted=bool(security.get("allow_untrusted", False)),
            allow_cross_repo=bool(security.get("allow_cross_repo", False)),
            inbox_dir=str(paths.get("inbox", "00-inbox")),
            archive_dir=str(paths.get("archive", "99-archive")),
            quarantine_dir=str(paths.get("quarantine", "98-quarantine")),
            extra={k: v for k, v in raw.items() if k not in {"retrieval", "lifecycle", "security", "paths"}},
        )
        config.ensure_runtime()
        return config


def find_vault(value: str | Path | None = None) -> Path:
    explicit = value or os.environ.get("KB_VAULT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "kb.toml").exists() or (candidate / ".obsidian").exists():
            return candidate
    return current


def find_repo(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def initialize_vault(path: str | Path, force: bool = False) -> KBConfig:
    vault = Path(path).expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)
    config_path = vault / "kb.toml"
    if config_path.exists() and not force:
        raise FileExistsError(f"{config_path} already exists; pass --force to replace it")
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    (vault / ".obsidian").mkdir(exist_ok=True)
    config = KBConfig.load(vault)
    return config
