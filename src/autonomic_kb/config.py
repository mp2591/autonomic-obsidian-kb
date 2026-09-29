from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """# autonomic-obsidian-kb configuration

[retrieval]
default_budget = 1000
minimum_score = 0.28
max_candidates = 200
routes = ["exact", "lexical", "graph", "temporal"]
rrf_k = 60

[lifecycle]
promotion_threshold = 0.62
stale_after_days = 120
archive_after_days = 365
recurrence_threshold = 2

[security]
allow_untrusted = false
allow_cross_repo = false
require_instruction_authorization = true
# true: CLI `remember --force`, elevated --authority/--taint and --authorize-instruction
# are allowed with --reason (recorded in the ledger), and candidates whose score clears
# promotion_threshold activate automatically. false: no self-vouching of any kind; every
# candidate (remember, learn, consolidate, MCP) waits in the inbox for `kb promote`.
allow_privileged_remember = true

[paths]
inbox = "00-inbox"
archive = "99-archive"
quarantine = "98-quarantine"

[telemetry]
enabled = true
"""


@dataclass(slots=True)
class KBConfig:
    vault: Path
    repo: Path | None = None
    default_budget: int = 1000
    minimum_score: float = 0.28
    max_candidates: int = 200
    retrieval_routes: tuple[str, ...] = ("exact", "lexical", "graph", "temporal")
    rrf_k: int = 60
    promotion_threshold: float = 0.62
    stale_after_days: int = 120
    archive_after_days: int = 365
    recurrence_threshold: int = 2
    allow_untrusted: bool = False
    allow_cross_repo: bool = False
    require_instruction_authorization: bool = True
    allow_privileged_remember: bool = True
    inbox_dir: str = "00-inbox"
    archive_dir: str = "99-archive"
    quarantine_dir: str = "98-quarantine"
    telemetry_enabled: bool = True
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
    def trace_path(self) -> Path:
        return self.runtime_dir / "traces.jsonl"

    @property
    def feedback_path(self) -> Path:
        return self.vault / ".kb-feedback.jsonl"

    @property
    def evidence_dir(self) -> Path:
        return self.vault / ".kb-evidence"

    @property
    def operation_dir(self) -> Path:
        return self.vault / ".kb-memory-events"

    @property
    def episode_dir(self) -> Path:
        return self.vault / ".kb-episodes"

    @property
    def lease_dir(self) -> Path:
        return self.runtime_dir / "leases"

    @property
    def config_path(self) -> Path:
        return self.vault / "kb.toml"

    def ensure_runtime(self) -> None:
        for relative in (self.inbox_dir, self.archive_dir, self.quarantine_dir):
            candidate = (self.vault / relative).resolve()
            if not candidate.is_relative_to(self.vault.resolve()):
                raise ValueError("configured directory escaped vault")
        for path in (self.feedback_path, self.vault / ".kb-outcomes.jsonl", self.vault / ".kb-transactions"):
            if path.is_symlink() or not path.resolve().is_relative_to(self.vault.resolve()):
                raise ValueError("KB state path escaped vault or is a symlink")
        for path in (
            self.runtime_dir,
            self.vault / self.inbox_dir,
            self.vault / self.archive_dir,
            self.vault / self.quarantine_dir,
            self.evidence_dir,
            self.operation_dir,
            self.episode_dir,
            self.lease_dir,
        ):
            if path.is_symlink() or not path.resolve().is_relative_to(self.vault.resolve()):
                raise ValueError("KB state path escaped vault or is a symlink")
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(
        cls, vault: str | Path | None = None, repo: str | Path | None = None, *, discover_repo: bool = True
    ) -> KBConfig:
        """Load a vault and the repository that scopes its memories.

        The repository is ``repo``, else ``KB_REPO``, else the Git worktree containing the
        current directory. It is never inferred from the vault's own location: a vault kept
        in Git would otherwise give every project the vault's identity, and a vault outside
        Git would give memories no identity at all. ``discover_repo=False`` means "no
        repository context".
        """
        vault_path = find_vault(vault)
        repo_path = resolve_repo(repo, discover=discover_repo)
        raw: dict[str, Any] = {}
        config_path = vault_path / "kb.toml"
        if config_path.exists():
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)
        retrieval = raw.get("retrieval", {})
        lifecycle = raw.get("lifecycle", {})
        security = raw.get("security", {})
        paths = raw.get("paths", {})
        telemetry = raw.get("telemetry", {})
        routes = retrieval.get("routes", ["exact", "lexical", "graph", "temporal"])
        if isinstance(routes, str):
            routes = [routes]
        config = cls(
            vault=vault_path,
            repo=repo_path,
            default_budget=int(retrieval.get("default_budget", 1000)),
            minimum_score=float(retrieval.get("minimum_score", 0.28)),
            max_candidates=int(retrieval.get("max_candidates", 200)),
            retrieval_routes=tuple(str(route) for route in routes),
            rrf_k=int(retrieval.get("rrf_k", 60)),
            promotion_threshold=float(lifecycle.get("promotion_threshold", 0.62)),
            stale_after_days=int(lifecycle.get("stale_after_days", 120)),
            archive_after_days=int(lifecycle.get("archive_after_days", 365)),
            recurrence_threshold=int(lifecycle.get("recurrence_threshold", 2)),
            allow_untrusted=bool(security.get("allow_untrusted", False)),
            allow_cross_repo=bool(security.get("allow_cross_repo", False)),
            require_instruction_authorization=bool(security.get("require_instruction_authorization", True)),
            allow_privileged_remember=bool(security.get("allow_privileged_remember", True)),
            inbox_dir=str(paths.get("inbox", "00-inbox")),
            archive_dir=str(paths.get("archive", "99-archive")),
            quarantine_dir=str(paths.get("quarantine", "98-quarantine")),
            telemetry_enabled=bool(telemetry.get("enabled", True)),
            extra={
                k: v for k, v in raw.items() if k not in {"retrieval", "lifecycle", "security", "paths", "telemetry"}
            },
        )
        config.ensure_runtime()
        return config


def find_vault(value: str | Path | None = None) -> Path:
    """Locate an existing vault; never fall back to an arbitrary working directory.

    Falling back to the current directory made read-only commands create KB state
    directories wherever they happened to run.
    """
    explicit = value or os.environ.get("KB_VAULT")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"vault does not exist: {path}; create it with `kb init {explicit}`")
        return path
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "kb.toml").exists() or (candidate / ".obsidian").exists():
            return candidate
    raise FileNotFoundError("no vault found: pass --vault, set KB_VAULT, or run `kb init <path>`")


def find_repo(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_repo(value: str | Path | None = None, *, discover: bool = True) -> Path | None:
    explicit = value or os.environ.get("KB_REPO")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return find_repo(Path.cwd()) if discover else None


LOCAL_STATE_IGNORES = (".kb/", ".kb-transactions/", ".kb-writer.lock")


def missing_local_ignores(vault: Path) -> list[str]:
    """Local-only state entries absent from the vault's .gitignore."""
    path = vault / ".gitignore"
    present = set(path.read_text(encoding="utf-8").split()) if path.is_file() else set()
    return [entry for entry in LOCAL_STATE_IGNORES if entry not in present]


def ensure_vault_gitignore(vault: Path) -> list[str]:
    """Keep indexes, backups (which hold unredacted originals), and journals out of Git."""
    missing = missing_local_ignores(vault)
    if missing:
        path = vault / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
        block = "# autonomic-obsidian-kb local state (derived, may hold unredacted backups)\n" + "\n".join(missing)
        path.write_text(prefix + block + "\n", encoding="utf-8")
    return missing


def initialize_vault(path: str | Path, force: bool = False) -> KBConfig:
    vault = Path(path).expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)
    config_path = vault / "kb.toml"
    if config_path.exists() and not force:
        raise FileExistsError(f"{config_path} already exists; pass --force to replace it")
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    (vault / ".obsidian").mkdir(exist_ok=True)
    ensure_vault_gitignore(vault)
    return KBConfig.load(vault)
