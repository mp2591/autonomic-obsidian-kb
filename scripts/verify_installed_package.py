"""Smoke-test the installed wheel, not an editable checkout.

Run with the fresh environment's ``python -I`` from outside the source tree.
"""

from __future__ import annotations

import json
import sys
import tempfile
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import autonomic_kb
from autonomic_kb.config import initialize_vault
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.markdown import render_note
from autonomic_kb.models import metadata_validator
from autonomic_kb.retrieval import Retriever
from autonomic_kb.util import utc_now


def main() -> None:
    package_path = Path(autonomic_kb.__file__).resolve()
    if not package_path.is_relative_to(Path(sys.prefix).resolve()):
        raise AssertionError(f"not testing the installed environment: {package_path}")
    if version("autonomic-obsidian-kb") != autonomic_kb.__version__:
        raise AssertionError("distribution and runtime versions disagree")
    schema = json.loads(files("autonomic_kb").joinpath("data/memory.schema.json").read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise AssertionError("packaged memory schema is missing or unexpected")
    metadata = {
        "schema_version": 2,
        "id": "kb:global:fact:installed-wheel",
        "title": "Installed wheel",
        "type": "fact",
        "scope": "global",
        "status": "active",
        "summary": "Installed package verification",
        "confidence": 0.9,
        "authority": "verified",
        "updated": utc_now(),
    }
    metadata_validator(2).validate(metadata)
    with tempfile.TemporaryDirectory(prefix="kb-wheel-smoke-") as temporary:
        config = initialize_vault(Path(temporary) / "vault")
        path = config.vault / "verified.md"
        path.write_text(render_note(metadata, {1: metadata["summary"]}), encoding="utf-8")
        with KnowledgeIndex(config) as index:
            index.rebuild()
            manifest = Retriever(config, index).retrieve("Installed package verification", budget=350)
            if [item.id for item in manifest.items] != [metadata["id"]]:
                raise AssertionError("installed package failed to index/retrieve its own schema-valid fixture")
    print(json.dumps({"version": autonomic_kb.__version__, "schema": "packaged", "retrieval": "passed"}))


if __name__ == "__main__":
    main()
