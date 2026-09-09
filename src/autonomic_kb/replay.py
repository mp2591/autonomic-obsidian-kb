from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .config import KBConfig
from .telemetry import TaskOutcome, TelemetryStore
from .util import sha256_text, utc_now


class MatchedReplayHarness:
    """Run paired external-agent commands in isolated snapshots.

    A zero exit status is process success only. Task success requires an evaluator when
    supplied by the case. Missing token counters remain unknown and never become zero.
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
        if name not in metrics or metrics[name] is None:
            return None
        try:
            return int(metrics[name])
        except (TypeError, ValueError):
            return None

    def _run_one(
        self,
        task: str,
        pair_id: str,
        mode: str,
        argv: list[str],
        cwd: Path,
        timeout: float,
        evaluator: list[str] | None = None,
    ) -> TaskOutcome:
        env = dict(os.environ)
        env.update({"KB_MODE": mode, "KB_TASK": task, "KB_VAULT": str(self.config.vault), "KB_REPO": str(cwd)})
        started = time.perf_counter()
        result = None
        eval_result = None
        metrics: dict[str, Any] = {}
        try:
            result = subprocess.run(
                argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
            )
            metrics = self._last_json(result.stdout)
            process_ok = result.returncode == 0
            if evaluator:
                eval_result = subprocess.run(
                    evaluator,
                    cwd=cwd,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
                success = process_ok and eval_result.returncode == 0
            else:
                success = process_ok
        except subprocess.TimeoutExpired:
            success = False
        elapsed = (time.perf_counter() - started) * 1000
        task_hash = sha256_text(task)[:16]
        usage_complete = all(self._metric(metrics, k) is not None for k in ("input_tokens", "output_tokens"))
        outcome = TaskOutcome(
            task_id=f"replay:{pair_id}:{mode}:{utc_now()}",
            task_hash=task_hash,
            kb_mode=mode,
            success=success,
            input_tokens=self._metric(metrics, "input_tokens"),
            output_tokens=self._metric(metrics, "output_tokens"),
            cached_tokens=self._metric(metrics, "cached_tokens"),
            searches=self._metric(metrics, "searches"),
            file_reads=self._metric(metrics, "file_reads"),
            commands=self._metric(metrics, "commands"),
            retries=self._metric(metrics, "retries"),
            corrections=self._metric(metrics, "corrections"),
            latency_ms=round(elapsed, 3),
            maintenance_tokens=self._metric(metrics, "maintenance_tokens"),
            unsafe=bool(metrics.get("unsafe", False)),
            retrieved_ids=list(metrics.get("retrieved_ids", [])),
            pair_id=pair_id,
            usage_complete=usage_complete,
            metadata={
                "argv": argv,
                "returncode": result.returncode if result else 124,
                "evaluator": evaluator or [],
                "evaluator_returncode": eval_result.returncode if eval_result else None,
            },
        )
        return self.telemetry.record_outcome(outcome)

    @staticmethod
    def _copy_snapshot(source: Path, destination: Path) -> None:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(".kb", "__pycache__", ".venv"),
        )

    def run(self, spec_path: str | Path) -> dict[str, Any]:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        results = []
        cases = spec if isinstance(spec, list) else spec.get("tasks", [])
        for case in cases:
            task = str(case["task"])
            source = Path(case.get("cwd") or self.config.repo or Path.cwd()).resolve()
            timeout = float(case.get("timeout", 900))
            baseline = [str(x) for x in case["baseline_command"]]
            assisted = [str(x) for x in case["kb_command"]]
            evaluator = [str(x) for x in case.get("evaluator_command", [])] or None
            pair_id = str(case.get("pair_id") or uuid.uuid4().hex[:16])
            with tempfile.TemporaryDirectory(prefix="kb-replay-") as temporary:
                root = Path(temporary)
                a_dir, b_dir = root / "baseline", root / "kb"
                self._copy_snapshot(source, a_dir)
                self._copy_snapshot(source, b_dir)
                a = self._run_one(task, pair_id, "no-kb", baseline, a_dir, timeout, evaluator)
                b = self._run_one(task, pair_id, "kb", assisted, b_dir, timeout, evaluator)
            results.append({"task": task, "pair_id": pair_id, "baseline": a.to_dict(), "kb": b.to_dict()})
        return {"pairs": len(results), "results": results, "summary": self.telemetry.paired_summary()}
