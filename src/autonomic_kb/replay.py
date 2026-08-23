from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import KBConfig
from .telemetry import TaskOutcome, TelemetryStore
from .util import sha256_text, utc_now


class MatchedReplayHarness:
    """Run paired external-agent commands under matched repository state.

    Commands are argv arrays (never shell strings). Agents may print a final JSON object
    containing token/search/read/retry counters; missing counters default to zero.
    """
    def __init__(self, config: KBConfig):
        self.config = config; self.telemetry = TelemetryStore(config)

    @staticmethod
    def _last_json(text: str) -> dict[str, Any]:
        for line in reversed(text.splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict): return value
            except json.JSONDecodeError:
                continue
        return {}

    def _run_one(self, task: str, mode: str, argv: list[str], cwd: Path, timeout: float) -> TaskOutcome:
        env = dict(os.environ); env.update({"KB_MODE": mode, "KB_TASK": task, "KB_VAULT": str(self.config.vault),
                                            "KB_REPO": str(self.config.repo or cwd)})
        started = time.perf_counter()
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False)
            success = result.returncode == 0; metrics = self._last_json(result.stdout)
        except subprocess.TimeoutExpired:
            success = False; metrics = {}; result = None
        elapsed = (time.perf_counter() - started) * 1000
        task_hash = sha256_text(task)[:16]
        outcome = TaskOutcome(
            task_id=f"replay:{task_hash}:{mode}:{utc_now()}", task_hash=task_hash, kb_mode=mode, success=success,
            input_tokens=int(metrics.get("input_tokens", 0)), output_tokens=int(metrics.get("output_tokens", 0)),
            cached_tokens=int(metrics.get("cached_tokens", 0)), searches=int(metrics.get("searches", 0)),
            file_reads=int(metrics.get("file_reads", 0)), commands=int(metrics.get("commands", 0)),
            retries=int(metrics.get("retries", 0)), corrections=int(metrics.get("corrections", 0)),
            latency_ms=round(elapsed, 3), maintenance_tokens=int(metrics.get("maintenance_tokens", 0)),
            unsafe=bool(metrics.get("unsafe", False)), retrieved_ids=list(metrics.get("retrieved_ids", [])),
            metadata={"argv": argv, "returncode": result.returncode if result else 124},
        )
        return self.telemetry.record_outcome(outcome)

    def run(self, spec_path: str | Path) -> dict[str, Any]:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8")); results = []
        for case in spec.get("tasks", spec if isinstance(spec, list) else []):
            task = str(case["task"]); cwd = Path(case.get("cwd") or self.config.repo or Path.cwd()).resolve()
            timeout = float(case.get("timeout", 900))
            baseline = [str(x) for x in case["baseline_command"]]; assisted = [str(x) for x in case["kb_command"]]
            a = self._run_one(task, "no-kb", baseline, cwd, timeout); b = self._run_one(task, "kb", assisted, cwd, timeout)
            results.append({"task": task, "baseline": a.to_dict(), "kb": b.to_dict()})
        return {"pairs": len(results), "results": results, "summary": self.telemetry.paired_summary()}
