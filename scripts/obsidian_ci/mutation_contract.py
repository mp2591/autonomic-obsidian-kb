from __future__ import annotations

import json
from pathlib import Path

from autonomic_kb.config import KBConfig
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.markdown import render_note

from .cli import OfficialCLI, assert_output_contains, clean, cli_content, wait_for
from .report import IntegrationReport


def verify_obsidian_and_kb_mutations(
    cli: OfficialCLI,
    vault: Path,
    config: KBConfig,
    report: IntegrationReport,
) -> None:
    cli_created = """---
id: kb:repository:fact:cli-created
 title: CLI Created Note
 type: fact
 scope: repository
 status: active
 summary: Created through the official Obsidian CLI.
 confidence: 0.99
 authority: verified
 tags: [\"integration\", \"cli-created\"]
---
# CLI Created

## L1 — Fact

created-by-official-obsidian-cli-73915
""".replace("\n ", "\n")
    cli.run(
        "create", "path=generated/cli-created.md", f"content={cli_content(cli_created)}", "overwrite"
    )
    wait_for("CLI-created file", lambda: vault.joinpath("generated/cli-created.md").exists())
    assert_output_contains(
        cli.run("read", "path=generated/cli-created.md"),
        "created-by-official-obsidian-cli-73915",
        "CLI-created note",
    )
    cli.run(
        "property:set", "name=integration_status", "value=passed", "type=text",
        "path=generated/cli-created.md",
    )
    assert_output_contains(
        cli.run("property:read", "name=integration_status", "path=generated/cli-created.md"),
        "passed",
        "CLI property mutation",
    )
    cli.run("append", "path=generated/cli-created.md", "content=appended-by-official-cli")
    cli.run("prepend", "path=generated/cli-created.md", "content=prepended-by-official-cli")
    mutated_text = clean(cli.run("read", "path=generated/cli-created.md").stdout)
    for marker in ("prepended-by-official-cli", "appended-by-official-cli", "integration_status: passed"):
        if marker not in mutated_text:
            raise AssertionError(f"official CLI mutation missing {marker!r}: {mutated_text!r}")
    report.add("obsidian-cli-mutations", "create/read/property/append/prepend persisted")

    cli.run("create", "path=Mutable.md", "content=mutable-target", "overwrite")
    cli.run(
        "create", "path=Linker.md", f"content={cli_content('Links to [[Mutable]].\n')}", "overwrite"
    )
    wait_for(
        "linker metadata",
        lambda: "Mutable" in clean(cli.run("links", "path=Linker.md").stdout),
    )
    cli.run("rename", "path=Mutable.md", "name=Renamed")
    wait_for(
        "automatic internal-link update",
        lambda: "[[Renamed]]" in clean(cli.run("read", "path=Linker.md").stdout),
    )
    if vault.joinpath("Mutable.md").exists() or not vault.joinpath("Renamed.md").exists():
        raise AssertionError("Obsidian rename did not update filesystem paths")
    report.add("obsidian-link-aware-rename", "link target and referring note updated")

    kb_created_path = vault / "generated" / "kb-created.md"
    kb_created_path.parent.mkdir(parents=True, exist_ok=True)
    kb_created_path.write_text(
        render_note(
            {
                "id": "kb:repository:fact:kb-created",
                "title": "KB Created Note",
                "type": "fact",
                "scope": "repository",
                "status": "active",
                "summary": "Created directly by the KB and observed by Obsidian.",
                "confidence": 0.99,
                "authority": "verified",
                "tags": ["integration", "kb-created"],
            },
            {1: "created-by-kb-observed-by-obsidian-19642"},
        ),
        encoding="utf-8",
    )

    def obsidian_sees_kb_note() -> bool:
        result = cli.run(
            "search", "query=created-by-kb-observed-by-obsidian-19642", "format=json",
            check=False,
        )
        return result.returncode == 0 and "generated/kb-created.md" in clean(result.stdout)

    wait_for("Obsidian to index a KB-created filesystem note", obsidian_sees_kb_note, timeout=45.0)
    assert_output_contains(
        cli.run("property:read", "name=id", "path=generated/kb-created.md"),
        "kb:repository:fact:kb-created",
        "Obsidian read of KB-created frontmatter",
    )
    report.add("bidirectional-vault-interoperability", "Obsidian observed a KB-created note")

    with KnowledgeIndex(config) as index:
        final_stats = index.rebuild()
        for memory_id in ("kb:repository:fact:cli-created", "kb:repository:fact:kb-created"):
            if index.get(memory_id) is None:
                raise AssertionError(f"KB index missed post-startup note {memory_id}")
        cli_record = index.get("kb:repository:fact:cli-created")
        if cli_record and cli_record["metadata"].get("integration_status") != "passed":
            raise AssertionError(
                f"KB parser missed Obsidian property mutation: {cli_record['metadata']!r}"
            )
    report.add("post-mutation-kb-reindex", json.dumps(final_stats.to_dict(), sort_keys=True))
