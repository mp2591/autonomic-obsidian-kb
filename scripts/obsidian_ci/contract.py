from __future__ import annotations

from pathlib import Path

from .capabilities import verify_capabilities
from .cli import OfficialCLI
from .graph_contract import verify_graph_and_parser_parity
from .kb_contract import verify_kb_index_and_retrieval
from .mutation_contract import verify_obsidian_and_kb_mutations
from .read_contract import verify_files_properties_and_tags
from .report import IntegrationReport
from .runtime_contract import verify_runtime_and_renderer


def run_contract(
    vault: Path,
    cli: OfficialCLI,
    expected_version: str,
    artifact_dir: Path,
) -> IntegrationReport:
    report = IntegrationReport(vault=str(vault))
    verify_capabilities(cli, expected_version, vault, artifact_dir, report)
    verify_files_properties_and_tags(cli, report)
    verify_graph_and_parser_parity(cli, vault, artifact_dir, report)
    config = verify_kb_index_and_retrieval(vault, report)
    verify_obsidian_and_kb_mutations(cli, vault, config, report)
    verify_runtime_and_renderer(cli, vault, artifact_dir, report)
    return report
