from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class OfficialCLI:
    def __init__(self, binary: str, vault_name: str, transcript: Path):
        self.binary = binary
        self.vault_name = vault_name
        self.transcript = transcript
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("", encoding="utf-8")

    def run(
        self,
        command: str,
        *arguments: str,
        target_vault: bool = True,
        timeout: float = 20.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.binary]
        if target_vault:
            cmd.append(f"vault={self.vault_name}")
        cmd.extend([command, *arguments])
        started = time.monotonic()
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        record = {
            "command": cmd,
            "returncode": result.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
        }
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if check and result.returncode != 0:
            raise AssertionError(
                f"official Obsidian CLI command failed ({result.returncode}): {cmd!r}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result


def clean(text: str) -> str:
    return _ANSI.sub("", text).strip()


def flatten_strings(value: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            strings.add(str(key))
            strings.update(flatten_strings(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            strings.update(flatten_strings(item))
    elif value is not None:
        strings.add(str(value))
    return strings


def decode_json_output(text: str) -> Any:
    """Decode CLI JSON despite occasional labels or a JSON-string wrapper."""

    value = clean(text)
    candidates = [value, *reversed([line.strip() for line in value.splitlines() if line.strip()])]
    for candidate in candidates:
        for start in (0, candidate.find("{"), candidate.find("["), candidate.find('"{'), candidate.find('"[')):
            if start < 0:
                continue
            try:
                decoded = json.loads(candidate[start:])
                if isinstance(decoded, str) and decoded[:1] in "[{":
                    decoded = json.loads(decoded)
                return decoded
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"could not decode JSON from Obsidian CLI output: {value!r}")


def assert_output_contains(result: subprocess.CompletedProcess[str], expected: str, label: str) -> str:
    output = clean(result.stdout or result.stderr)
    if expected not in output:
        raise AssertionError(f"{label}: expected {expected!r} in {output!r}")
    return output


def wait_for(description: str, function: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if function():
                return
        except Exception as error:  # expected while Obsidian's metadata cache catches up
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {description}; last error: {last_error}")


def cli_content(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def eval_snapshot(cli: OfficialCLI, path: str) -> dict[str, Any]:
    encoded_path = json.dumps(path)
    code = (
        "(() => {"
        f"const file=app.vault.getAbstractFileByPath({encoded_path});"
        "if(!file){throw new Error('fixture file not found');}"
        "const cache=app.metadataCache.getFileCache(file)||{};"
        "return JSON.stringify({"
        "path:file.path,"
        "frontmatter:cache.frontmatter||{},"
        "links:(cache.links||[]).map(x=>x.link.split('#',1)[0]).sort(),"
        "tags:(cache.tags||[]).map(x=>x.tag.replace(/^#/, '')).sort(),"
        "headings:(cache.headings||[]).map(x=>x.heading)"
        "});"
        "})()"
    )
    result = cli.run("eval", f"code={code}")
    decoded = decode_json_output(result.stdout)
    if not isinstance(decoded, dict):
        raise AssertionError(f"eval snapshot was not an object: {decoded!r}")
    return decoded
