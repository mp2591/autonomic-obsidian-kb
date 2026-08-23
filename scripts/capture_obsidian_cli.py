#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def run(binary: str, *args: str) -> dict:
    try:
        result = subprocess.run([binary, *args], text=True, capture_output=True, timeout=10, check=False)
        return {"args": list(args), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"args": list(args), "returncode": 124, "stdout": "", "stderr": "timeout; Obsidian may not be running"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture the installed first-party Obsidian CLI surface")
    parser.add_argument("--binary", default="obsidian")
    parser.add_argument("--output", default="docs/research/obsidian-cli-live-snapshot.json")
    args = parser.parse_args()
    binary = shutil.which(args.binary)
    value = {
        "captured_at": datetime.now(UTC).isoformat(),
        "binary": binary,
        "note": "The app-backed CLI may require Obsidian desktop to be running.",
        "probes": [],
    }
    if binary:
        value["probes"] = [run(binary, "version"), run(binary, "help")]
    else:
        value["error"] = "binary not found"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(value, indent=2))
    return 0 if binary else 1


if __name__ == "__main__":
    raise SystemExit(main())
