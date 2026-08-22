from __future__ import annotations

from pathlib import Path

from autonomic_kb.obsidian import ObsidianBridge

from .cli import OfficialCLI
from .report import IntegrationReport


def verify_runtime_and_renderer(
    cli: OfficialCLI,
    vault: Path,
    artifacts: Path,
    report: IntegrationReport,
) -> None:
    bridge = ObsidianBridge(vault, binary=cli.binary, vault_name=cli.vault_name)
    status = bridge.status(timeout=10.0)
    if not status.responsive or not status.vault_responsive:
        raise AssertionError(
            f"ObsidianBridge did not recognize the live app/vault: {status.to_dict()}"
        )
    bridge_read = bridge.run("read", "path=Source.md", timeout=10.0)
    if bridge_read.returncode != 0 or "obsidian-ci-unique-phrase-48291" not in bridge_read.stdout:
        raise AssertionError(f"ObsidianBridge targeted command failed: {bridge_read}")
    capabilities = bridge.capabilities()
    if not {"read", "property:read", "dev:screenshot"}.issubset(
        set(capabilities["discovered_commands"])
    ):
        raise AssertionError(f"ObsidianBridge capability parsing was incomplete: {capabilities}")
    report.add("production-obsidian-bridge", status.version)

    screenshot_path = artifacts / "obsidian-workspace.png"
    cli.run("dev:screenshot", f"path={screenshot_path}", check=True, timeout=30.0)
    if not screenshot_path.exists() or screenshot_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("Obsidian developer screenshot was not written as a valid PNG")
    report.add(
        "obsidian-headless-renderer",
        f"PNG screenshot {screenshot_path.stat().st_size} bytes",
    )
