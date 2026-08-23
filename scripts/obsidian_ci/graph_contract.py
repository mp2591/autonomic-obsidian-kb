from __future__ import annotations

import json
from pathlib import Path

from autonomic_kb.markdown import parse_markdown

from .cli import OfficialCLI, clean, eval_snapshot
from .report import IntegrationReport

EXPECTED_LINKS = {"Target", "Nested/Second Target", "Missing Contract Target"}


def verify_graph_and_parser_parity(
    cli: OfficialCLI,
    vault: Path,
    artifacts: Path,
    report: IntegrationReport,
) -> None:
    backlinks = clean(cli.run("backlinks", "path=Target.md", "format=json").stdout)
    if "Source.md" not in backlinks:
        raise AssertionError(f"Obsidian backlinks are missing Source.md: {backlinks!r}")
    report.add("obsidian-backlinks", "Target.md <- Source.md")

    source_backlinks = clean(cli.run("backlinks", "path=Source.md", "format=json").stdout)
    if "Backlink Holder.md" not in source_backlinks:
        raise AssertionError(f"Obsidian backlinks are missing Backlink Holder.md: {source_backlinks!r}")
    report.add("obsidian-backlink-reverse-index", "Source.md <- Backlink Holder.md")

    deadends = clean(cli.run("deadends").stdout)
    if "Target.md" not in deadends or "Source.md" in deadends:
        raise AssertionError(f"Obsidian dead-end graph view is inconsistent: {deadends!r}")
    report.add("obsidian-deadends", "Target.md has no outgoing links; Source.md has outgoing links")

    orphans = clean(cli.run("orphans").stdout)
    if "Backlink Holder.md" not in orphans or "Source.md" in orphans:
        raise AssertionError(f"Obsidian orphan graph view is inconsistent: {orphans!r}")
    report.add("obsidian-orphans", "Backlink Holder.md has no incoming links; Source.md has an incoming link")

    unresolved = clean(cli.run("unresolved", "verbose", "format=json").stdout)
    if "Missing Contract Target" not in unresolved or "Source.md" not in unresolved:
        raise AssertionError(f"Obsidian unresolved-link output is incomplete: {unresolved!r}")
    report.add("obsidian-unresolved-links", "missing target attributed to Source.md")

    snapshot = eval_snapshot(cli, "Source.md")
    artifacts.joinpath("obsidian-metadata-snapshot.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frontmatter = snapshot.get("frontmatter", {})
    if frontmatter.get("id") != "kb:repository:architecture:obsidian-contract-source":
        raise AssertionError(f"Obsidian metadata cache returned wrong frontmatter: {frontmatter!r}")
    headings = set(snapshot.get("headings", []))
    for heading in ("Source Heading", "L0 — Pointer", "L1 — Fact", "L2 — Summary"):
        if heading not in headings:
            raise AssertionError(f"Obsidian metadata cache is missing heading {heading!r}: {sorted(headings)!r}")
    report.add(
        "obsidian-metadata-cache",
        "frontmatter, links, tags, and headings queried through app.eval",
    )

    parsed = parse_markdown(vault.joinpath("Source.md").read_text(encoding="utf-8"))
    if set(parsed.links) != EXPECTED_LINKS:
        raise AssertionError(f"KB parser link mismatch: {parsed.links!r}")
    if not {"integration", "frontmatter", "inline-tag"}.issubset(set(parsed.tags)):
        raise AssertionError(f"KB parser tag mismatch: {parsed.tags!r}")
    if set(snapshot.get("links", [])) != EXPECTED_LINKS:
        raise AssertionError(f"Obsidian metadata-cache link mismatch: {snapshot.get('links')!r}")
    report.add("obsidian-outgoing-links", "resolved, nested, and unresolved links visible in metadata cache")
    obsidian_tags = set(snapshot.get("tags", []))
    raw_tags = frontmatter.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    obsidian_tags.update(str(tag).lstrip("#") for tag in raw_tags)
    if not set(parsed.tags).issubset(obsidian_tags):
        raise AssertionError(f"KB/Obsidian tag parity mismatch: kb={parsed.tags!r}, obsidian={sorted(obsidian_tags)!r}")
    report.add("kb-obsidian-parser-parity", "properties, tags, links, and headings agree")
