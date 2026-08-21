#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autonomic_kb.benchmark import BenchmarkRunner
from autonomic_kb.config import KBConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", default=str(ROOT / "examples" / "sample-vault"))
    parser.add_argument("--tasks", default=str(ROOT / "benchmarks" / "tasks.json"))
    parser.add_argument("--output", default=str(ROOT / "benchmarks" / "results" / "reference.json"))
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    config = KBConfig.load(args.vault, ROOT)
    result = BenchmarkRunner(config).run(args.tasks)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    summary = result["summary"]
    if not args.no_fail and (summary["net_savings"] <= 0 or summary["mean_expected_coverage"] < 0.70):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
