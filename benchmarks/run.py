#!/usr/bin/env python3
"""Deterministic retrieval regression gate over the committed sample vault.

The gate fails when aggregate savings vanish, expected coverage collapses, or results
regress against ``results/reference.json``. Results go to an untracked file; the committed
reference changes only with ``--update-reference``, in the same commit as the retrieval
change that justifies it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autonomic_kb.benchmark import BenchmarkRunner, compare_to_reference  # noqa: E402
from autonomic_kb.config import KBConfig  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(ROOT / "examples" / "sample-vault"))
    parser.add_argument("--tasks", default=str(ROOT / "benchmarks" / "tasks.json"))
    parser.add_argument("--output", default=str(RESULTS / "latest.json"))
    parser.add_argument("--reference", default=str(RESULTS / "reference.json"))
    parser.add_argument("--update-reference", action="store_true", help="accept these results as the new reference")
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    config = KBConfig.load(args.vault, ROOT)
    result = BenchmarkRunner(config).run(args.tasks)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    reference = Path(args.reference)
    if args.update_reference:
        reference.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    problems: list[str] = []
    summary = result["summary"]
    if summary["net_savings"] <= 0:
        problems.append("aggregate net savings are not positive")
    if summary["mean_expected_coverage"] < 0.70:
        problems.append("mean expected coverage is below 0.70")
    if not args.update_reference:
        if reference.exists():
            problems.extend(compare_to_reference(result, json.loads(reference.read_text(encoding="utf-8"))))
        else:
            problems.append(f"reference result not found: {reference}")
    print(json.dumps(result, indent=2))
    for problem in problems:
        print(f"benchmark regression: {problem}", file=sys.stderr)
    return 1 if problems and not args.no_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
