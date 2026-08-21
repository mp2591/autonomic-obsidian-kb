from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ObsidianStatus:
    binary: str = ""
    available: bool = False
    responsive: bool = False
    version: str = ""
    help_excerpt: str = ""
    requires_desktop: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObsidianBridge:
    """Optional bridge to the first-party Obsidian CLI.

    Core KB operations never depend on this bridge. The official CLI is used
    only after capability probing because command availability follows the
    installed Obsidian desktop version and the app must be running.
    """

    def __init__(self, vault: Path, binary: str = "obsidian"):
        self.vault = vault
        self.binary = shutil.which(binary) or ""

    def status(self) -> ObsidianStatus:
        if not self.binary:
            return ObsidianStatus(error="official Obsidian CLI binary not found")
        version = self._run(["version"])
        help_result = self._run(["help"])
        responsive = version.returncode == 0 or help_result.returncode == 0
        return ObsidianStatus(
            binary=self.binary,
            available=True,
            responsive=responsive,
            version=(version.stdout or version.stderr).strip()[:240],
            help_excerpt=(help_result.stdout or help_result.stderr).strip()[:1200],
            error="" if responsive else (version.stderr or help_result.stderr).strip()[:400],
        )

    def capabilities(self) -> dict[str, Any]:
        status = self.status()
        commands: list[str] = []
        if status.help_excerpt:
            for line in status.help_excerpt.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(("Usage", "Options", "Commands", "-")):
                    token = stripped.split()[0].strip(",:")
                    if token.replace(":", "").replace("-", "").isalnum():
                        commands.append(token)
        return {"status": status.to_dict(), "discovered_commands": sorted(set(commands))}

    def run(self, command: str, *arguments: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        if not self.binary:
            raise FileNotFoundError("official Obsidian CLI binary not found")
        return self._run([command, *arguments], timeout=timeout)

    def _run(self, arguments: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        if not self.binary:
            return subprocess.CompletedProcess(["obsidian", *arguments], 127, "", "binary not found")
        command = [self.binary, *arguments]
        # Official CLI versions differ in vault selector spelling; KB probes the
        # live help output before relying on mutation commands.
        try:
            return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            return subprocess.CompletedProcess(command, 124, error.stdout or "", "timed out; is Obsidian running?")
