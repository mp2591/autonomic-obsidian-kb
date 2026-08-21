from __future__ import annotations

from pathlib import Path

from autonomic_kb.config import KBConfig
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.retrieval import Retriever

from .graph_contract import EXPECTED_LINKS
from .report import IntegrationReport

SOURCE_ID = "kb:repository:architecture:obsidian-contract-source"


def verify_kb_index_and_retrieval(vault: Path, report: IntegrationReport) -> KBConfig:
    config = KBConfig.load(vault)
    with KnowledgeIndex(config) as index:
        stats = index.rebuild()
        if stats.malformed:
            raise AssertionError(f"fixture produced malformed KB records: {stats.to_dict()}")
        if index.get(SOURCE_ID) is None:
            raise AssertionError("KB index did not contain the Obsidian source fixture")
        indexed_links = {
            row[0]
            for row in index.connection.execute(
                "SELECT target FROM links WHERE source_id=?",
                (SOURCE_ID,),
            )
        }
        if indexed_links != EXPECTED_LINKS:
            raise AssertionError(f"KB index link mismatch: {indexed_links!r}")
        manifest = Retriever(config, index).retrieve(
            "obsidian-ci-unique-phrase-48291", budget=240, paths=["Source.md"]
        )
        if SOURCE_ID not in {item.id for item in manifest.items}:
            raise AssertionError(f"KB retrieval missed the shared-vault source: {manifest.to_dict()}")
    report.add("kb-index-and-retrieval", "same Obsidian vault indexed and retrieved")
    return config
