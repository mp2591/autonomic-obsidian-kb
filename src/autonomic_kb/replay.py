from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .config import KBConfig
from .security import reject_secrets
from .telemetry import TaskOutcome, TelemetryStore
from .util import sha256_file, sha256_text, stable_json, utc_now

_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "maintenance_tokens",
    "cached_tokens",
    "searches",
    "file_reads",
    "commands",
    "retries",
    "corrections",
)
_SKIP = {".kb", ".kb-transactions", ".kb-writer.lock", "__pycache__", ".venv", ".pytest_cache"}


class MatchedReplayHarness:
    """Compare trusted host commands from frozen, isolated working copies.

    This is not an OS sandbox. A completed independent evaluator is required to
    qualify a comparison. Failed/timed-out agent runs are evaluated too; missing
    or malformed usage never becomes an invented zero-cost successful task.
    """

    def __init__(self, config: KBConfig):
        self.config = config
        self.telemetry = TelemetryStore(config)

    @staticmethod
    def _last_json(text: str) -> dict[str, Any]:
        for line in reversed(text.splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _metric(metrics: dict[str, Any], name: str) -> int | None:
        value = metrics.get(name)
        return value if type(value) is int and value >= 0 else None

    def _run_one(
        self,
        task: str,
        pair_id: str,
        mode: str,
        argv: list[str],
        cwd: Path,
        timeout: float,
        evaluator: list[str] | None = None,
        vault: Path | None = None,
        experiment: dict[str, Any] | None = None,
    ) -> TaskOutcome:
        env = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT", "TMPDIR", "TEMP") if key in os.environ}
        env.update(
            {
                "KB_MODE": mode,
                "KB_TASK": task,
                "KB_VAULT": str(vault or self.config.vault),
                "KB_REPO": str(cwd),
                "HOME": str(cwd.parent / (mode + "-home")),
            }
        )
        Path(env["HOME"]).mkdir(exist_ok=True)
        started = time.perf_counter()
        result = eval_result = None
        metrics: dict[str, Any] = {}
        agent_status, evaluator_status = "not_started", "not_configured"
        try:
            result = subprocess.run(
                argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
            )
            metrics = self._last_json(result.stdout)
            agent_status = "completed" if result.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            agent_status = "timeout"
        except OSError:
            agent_status = "launch_failed"
        # Do not drop timed-out or failed tasks from the correctness denominator.
        # Evaluation failure/timeout is explicit and does not qualify as a matched pair.
        if evaluator:
            try:
                eval_result = subprocess.run(
                    evaluator, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
                )
                evaluator_status = "completed" if eval_result.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                evaluator_status = "timeout"
            except OSError:
                evaluator_status = "launch_failed"
        success = agent_status == "completed" and evaluator_status == "completed"
        counters = {key: self._metric(metrics, key) for key in _COUNTERS}
        errors = [key for key in _COUNTERS if metrics.get(key) is not None and counters[key] is None]
        if (
            counters["cached_tokens"] is not None
            and counters["input_tokens"] is not None
            and counters["cached_tokens"] > counters["input_tokens"]
        ):
            errors.append("cached_tokens exceeds input_tokens")
            counters["cached_tokens"] = None
        retrieved = metrics.get("retrieved_ids", [])
        if not isinstance(retrieved, list) or not all(isinstance(value, str) for value in retrieved):
            errors.append("retrieved_ids")
            retrieved = []
        if "unsafe" in metrics and not isinstance(metrics["unsafe"], bool):
            errors.append("unsafe")
        complete = not errors and all(counters[key] is not None for key in _COUNTERS[:3])
        outcome = TaskOutcome(
            task_id=f"replay:{pair_id}:{mode}:{utc_now()}",
            task_hash=sha256_text(task)[:16],
            kb_mode=mode,
            success=success,
            **counters,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            unsafe=metrics.get("unsafe") is True,
            retrieved_ids=retrieved,
            pair_id=pair_id,
            usage_complete=complete,
            metadata={
                **(experiment or {}),
                "evaluated": eval_result is not None,
                "agent_status": agent_status,
                "evaluator_status": evaluator_status,
                "metric_errors": errors,
                "safety_reported": isinstance(metrics.get("unsafe"), bool),
                "argv": argv,
                "returncode": result.returncode if result is not None else None,
                "evaluator": evaluator or [],
                "evaluator_returncode": eval_result.returncode if eval_result is not None else None,
            },
        )
        return self.telemetry.record_outcome(outcome)

    @staticmethod
    def _copy_snapshot(source: Path, destination: Path, *, exclude: Path | None = None) -> None:
        source = source.resolve()
        if not source.is_dir():
            raise ValueError("snapshot source must be a directory")
        if (source / ".git").is_file():
            raise ValueError("replay requires a standalone checkout, not a shared Git worktree pointer")

        def ignored(directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in _SKIP or (exclude and Path(directory) / name == exclude)}

        # Prune ignored directories before validating links; never traverse their
        # virtual environments/caches. Included directory links can form cycles.
        for directory, subdirs, files in os.walk(source, followlinks=False):
            skip = ignored(directory, subdirs + files)
            subdirs[:] = [name for name in subdirs if name not in skip]
            for name in subdirs + [name for name in files if name not in skip]:
                path = Path(directory) / name
                if path.is_symlink():
                    target = path.resolve()
                    if not target.is_relative_to(source) or not target.is_file():
                        raise ValueError("replay snapshot contains an external or directory symlink")
        shutil.copytree(source, destination, symlinks=False, ignore=ignored)

    @staticmethod
    def _fingerprint(root: Path) -> str:
        # Executability affects the task environment even when file bytes match.
        return sha256_text(
            stable_json(
                {
                    path.relative_to(root).as_posix(): [sha256_file(path), path.stat().st_mode & 0o111]
                    for path in sorted(root.rglob("*"))
                    if path.is_file()
                }
            )
        )

    def _cases(self, spec: Any) -> list[dict[str, Any]]:
        cases = spec if isinstance(spec, list) else spec.get("tasks") if isinstance(spec, dict) else None
        if not isinstance(cases, list):
            raise ValueError("replay spec requires a tasks array")
        planned = []
        pair_ids = {row["pair_id"] for row in self.telemetry.outcomes() if row.get("pair_id")}
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("task"), str) or not case["task"].strip():
                raise ValueError("every replay case requires a nonempty task")
            reject_secrets(case)
            for key in ("baseline_command", "kb_command", "evaluator_command"):
                argv = case.get(key)
                if (
                    not isinstance(argv, list)
                    or not argv
                    or not argv[0]
                    or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
                ):
                    raise ValueError("replay requires argv arrays and an independent evaluator")
            timeout = case.get("timeout", 900)
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int | float)
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError("timeout must be positive and finite")
            pair = case.get("pair_id") or uuid.uuid4().hex[:16]
            if not isinstance(pair, str) or pair in pair_ids:
                raise ValueError("pair_id must be a new unique string; omit it to generate one per run")
            pair_ids.add(pair)
            for key in ("model_id", "agent_version", "tool_config", "cwd"):
                if key in case and not isinstance(case[key], str):
                    raise ValueError(f"{key} must be a string")
            source = Path(case.get("cwd") or self.config.repo or Path.cwd()).resolve()
            vault = self.config.vault.resolve()
            if not source.is_dir() or source.is_relative_to(vault):
                raise ValueError("replay repository must exist outside the memory vault")
            planned.append({**case, "pair_id": pair, "source": source, "timeout": float(timeout)})
        return planned

    def run(self, spec_path: str | Path) -> dict[str, Any]:
        # Validate every case before the first potentially side-effecting command.
        cases = self._cases(json.loads(Path(spec_path).read_text(encoding="utf-8")))
        results = []
        for case in cases:
            task, pair_id, source = case["task"], case["pair_id"], case["source"]
            vault = self.config.vault.resolve()
            with tempfile.TemporaryDirectory(prefix="kb-replay-") as temporary:
                root = Path(temporary)
                repo_seed, vault_seed = root / "repository-seed", root / "vault-seed"
                self._copy_snapshot(source, repo_seed, exclude=vault if vault.is_relative_to(source) else None)
                self._copy_snapshot(vault, vault_seed)
                repo_digest, vault_digest = self._fingerprint(repo_seed), self._fingerprint(vault_seed)
                a_dir, b_dir = root / "baseline", root / "kb"
                for destination in (a_dir, b_dir):
                    self._copy_snapshot(repo_seed, destination)
                    if self._fingerprint(destination) != repo_digest:
                        raise ValueError("repository snapshot changed during replay preparation")
                a_vault, b_vault = root / "baseline-vault", root / "assisted-vault"
                a_vault.mkdir()
                self._copy_snapshot(vault_seed, b_vault)
                if self._fingerprint(b_vault) != vault_digest:
                    raise ValueError("vault snapshot changed during replay preparation")
                experiment = {
                    "repo_snapshot": repo_digest,
                    "vault_snapshot": vault_digest,
                    "evaluator_id": sha256_text(stable_json(case["evaluator_command"])),
                    "model_id": case.get("model_id", ""),
                    "agent_version": case.get("agent_version", ""),
                    "tool_config": case.get("tool_config", ""),
                }
                a = self._run_one(
                    task,
                    pair_id,
                    "no-kb",
                    case["baseline_command"],
                    a_dir,
                    case["timeout"],
                    case["evaluator_command"],
                    a_vault,
                    experiment,
                )
                if self._fingerprint(b_dir) != repo_digest or self._fingerprint(b_vault) != vault_digest:
                    raise ValueError("assisted starting state changed during the baseline run")
                b = self._run_one(
                    task,
                    pair_id,
                    "kb",
                    case["kb_command"],
                    b_dir,
                    case["timeout"],
                    case["evaluator_command"],
                    b_vault,
                    experiment,
                )
            results.append({"task": task, "pair_id": pair_id, "baseline": a.to_dict(), "kb": b.to_dict()})
        return {"pairs": len(results), "results": results, "summary": self.telemetry.paired_summary()}
