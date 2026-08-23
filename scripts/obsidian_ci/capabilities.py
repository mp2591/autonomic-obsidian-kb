from __future__ import annotations

import json
from pathlib import Path

from autonomic_kb.obsidian import parse_help_commands

from .cli import OfficialCLI, assert_output_contains, clean
from .report import IntegrationReport

REQUIRED_COMMANDS = {
    "aliases",
    "append",
    "backlinks",
    "commands",
    "create",
    "deadends",
    "eval",
    "file",
    "files",
    "folder",
    "folders",
    "move",
    "orphans",
    "prepend",
    "properties",
    "property:read",
    "property:remove",
    "property:set",
    "read",
    "reload",
    "rename",
    "search",
    "search:context",
    "tag",
    "tags",
    "task",
    "tasks",
    "unresolved",
    "vault",
    "version",
    "dev:errors",
    "dev:screenshot",
}


def verify_capabilities(
    cli: OfficialCLI,
    expected_version: str,
    vault: Path,
    artifacts: Path,
    report: IntegrationReport,
) -> None:
    version = assert_output_contains(cli.run("version", target_vault=False), expected_version, "Obsidian version")
    report.obsidian_version = version
    report.add("official-cli-version", version)

    help_output = clean(cli.run("help", target_vault=False).stdout)
    discovered = set(parse_help_commands(help_output))
    missing = sorted(REQUIRED_COMMANDS - discovered)
    if missing:
        raise AssertionError(
            f"official CLI help is missing required commands: {missing}; discovered={sorted(discovered)}"
        )
    artifacts.joinpath("obsidian-discovered-commands.json").write_text(
        json.dumps(sorted(discovered), indent=2) + "\n", encoding="utf-8"
    )
    report.add(
        "official-cli-capabilities",
        f"{len(REQUIRED_COMMANDS)} required commands present; {len(discovered)} total discovered",
    )

    configured_path = clean(cli.run("vault", "info=path").stdout)
    if str(vault) not in configured_path:
        raise AssertionError(f"Obsidian opened the wrong vault: {configured_path!r}")
    report.add("configured-vault-open", configured_path)
