from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_COMMAND = re.compile(r"^ {2}([a-z][a-z0-9:-]*)[ \t]{2,}\S", re.IGNORECASE | re.MULTILINE)


def parse_help_commands(help_text: str) -> list[str]:
    """Return exact top-level commands from first-party ``obsidian help`` output.

    Obsidian indents commands by two spaces and their parameters by four.
    Matching the indentation prevents options such as ``file=<name>`` from
    being misreported as commands, and parsing the complete help output avoids
    losing commands that appear after a human-readable excerpt is truncated.
    """

    return sorted({match.group(1).lower() for match in _COMMAND.finditer(help_text)})


@dataclass(slots=True)
class ObsidianStatus:
    binary: str = ""
    available: bool = False
    responsive: bool = False
    version: str = ""
    help_excerpt: str = ""
    requires_desktop: bool = True
    vault_responsive: bool = False
    vault_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObsidianBridge:
    """Capability-probed bridge to the first-party Obsidian CLI.

    The official CLI is an app-backed IPC client. The desktop process must be
    installed, running, and configured with CLI support. Core KB operations do
    not depend on this bridge; they continue to operate directly on Markdown.

    ``vault_name`` is the name or ID registered in Obsidian's global
    ``obsidian.json``. When omitted, the vault directory name is used.
    """

    def __init__(self, vault: Path, binary: str = "obsidian", vault_name: str | None = None):
        self.vault = vault.resolve()
        self.binary = shutil.which(binary) or ""
        self.vault_name = vault_name or self.vault.name

    def status(self, timeout: float = 5.0) -> ObsidianStatus:
        if not self.binary:
            return ObsidianStatus(error="official Obsidian CLI binary not found")

        version = self._run(["version"], timeout=timeout)
        help_result = self._run(["help"], timeout=timeout)
        responsive = version.returncode == 0 or help_result.returncode == 0
        vault_result = self._run(
            [f"vault={self.vault_name}", "vault", "info=path"],
            timeout=timeout,
        ) if responsive else subprocess.CompletedProcess([], 1, "", "CLI is not responsive")
        vault_path = (vault_result.stdout or "").strip()
        vault_responsive = vault_result.returncode == 0 and self._same_path(vault_path, self.vault)

        errors = []
        if not responsive:
            errors.append((version.stderr or help_result.stderr or "CLI did not respond").strip())
        elif not vault_responsive:
            detail = (vault_result.stderr or vault_result.stdout or "target vault did not respond").strip()
            errors.append(detail)
        return ObsidianStatus(
            binary=self.binary,
            available=True,
            responsive=responsive,
            version=(version.stdout or version.stderr).strip()[:240],
            help_excerpt=(help_result.stdout or help_result.stderr).strip()[:4000],
            vault_responsive=vault_responsive,
            vault_path=vault_path[:1000],
            error="; ".join(error for error in errors if error)[:1000],
        )

    def wait_until_ready(self, timeout: float = 60.0, interval: float = 1.0) -> ObsidianStatus:
        """Wait until both the app-backed CLI and target vault are responsive."""

        deadline = time.monotonic() + timeout
        last = self.status(timeout=min(5.0, max(interval, 1.0)))
        while time.monotonic() < deadline:
            if last.responsive and last.vault_responsive:
                return last
            time.sleep(interval)
            last = self.status(timeout=min(5.0, max(interval, 1.0)))
        return last

    def capabilities(self) -> dict[str, Any]:
        status = self.status()
        help_result = self._run(["help"], timeout=5.0) if self.binary else None
        help_text = ""
        if help_result is not None:
            help_text = help_result.stdout or help_result.stderr or ""
        return {
            "status": status.to_dict(),
            "discovered_commands": parse_help_commands(help_text),
        }

    def run(
        self,
        command: str,
        *arguments: str,
        timeout: float = 10.0,
        target_vault: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one official CLI command, targeting this bridge's vault by default."""

        if not self.binary:
            raise FileNotFoundError("official Obsidian CLI binary not found")
        command_line = [command, *arguments]
        if target_vault:
            command_line.insert(0, f"vault={self.vault_name}")
        return self._run(command_line, timeout=timeout)

    def _run(self, arguments: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        if not self.binary:
            return subprocess.CompletedProcess(["obsidian", *arguments], 127, "", "binary not found")
        command = [self.binary, *arguments]
        try:
            return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
            detail = stderr or "timed out; is Obsidian running and is CLI mode enabled?"
            return subprocess.CompletedProcess(command, 124, stdout, detail)

    @staticmethod
    def _same_path(value: str, expected: Path) -> bool:
        if not value:
            return False
        try:
            return Path(value).expanduser().resolve() == expected
        except (OSError, RuntimeError):
            return False
