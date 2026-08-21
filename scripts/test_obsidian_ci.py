#!/usr/bin/env python3
"""End-to-end contract tests against a running first-party Obsidian desktop app."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from obsidian_ci.cli import OfficialCLI
from obsidian_ci.contract import run_contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--vault-name", required=True)
    parser.add_argument("--obsidian", default="obsidian")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    vault = arguments.vault.resolve()
    artifacts = arguments.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    transcript = artifacts / "obsidian-cli-transcript.jsonl"
    cli = OfficialCLI(arguments.obsidian, arguments.vault_name, transcript)
    report_path = artifacts / "obsidian-integration-report.json"
    try:
        report = run_contract(vault, cli, arguments.expected_version, artifacts)
    except Exception as error:
        failure = {
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "vault": str(vault),
        }
        report_path.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        raise
    report_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
