from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autonomic_kb.config import KBConfig
from autonomic_kb.index import KnowledgeIndex
from autonomic_kb.markdown import parse_markdown, render_note
from autonomic_kb.obsidian import ObsidianBridge
from autonomic_kb.retrieval import Retriever

from .cli import (
    OfficialCLI,
    assert_output_contains,
    clean,
    cli_content,
    decode_json_output,
    eval_snapshot,
    flatten_strings,
    wait_for,
)

@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class IntegrationReport:
    obsidian_version: str = ""
    vault: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, name: str, detail: str = "") -> None:
        self.checks.append(Check(name=name, ok=True, detail=detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "obsidian_version": self.obsidian_version,
            "vault": self.vault,
            "checks": [asdict(check) for check in self.checks],
        }

def run_contract(vault: Path, cli: OfficialCLI, expected_version: str, artifact_dir: Path) -> IntegrationReport:
    report = IntegrationReport(vault=str(vault))

    version = assert_output_contains(
        cli.run("version", target_vault=False), expected_version, "Obsidian version"
    )
    report.obsidian_version = version
    report.add("official-cli-version", version)

    help_output = clean(cli.run("help", target_vault=False).stdout)
    required_commands = {
        "file", "files", "folders", "read", "create", "append", "prepend", "rename", "search",
        "aliases", "properties", "property:set", "property:read", "links", "backlinks",
        "unresolved", "tags", "tag", "tasks", "outline", "wordcount", "eval",
        "dev:errors", "dev:screenshot",
    }
    missing_commands = sorted(command for command in required_commands if command not in help_output)
    if missing_commands:
        raise AssertionError(f"official CLI help is missing required commands: {missing_commands}")
    report.add("official-cli-capabilities", f"{len(required_commands)} required commands present")

    configured_path = clean(cli.run("vault", "info=path").stdout)
    if str(vault) not in configured_path:
        raise AssertionError(f"Obsidian opened the wrong vault: {configured_path!r}")
    report.add("configured-vault-open", configured_path)

    files_output = clean(cli.run("files", "ext=md").stdout)
    for path in ("Source.md", "Target.md", "Nested/Second Target.md", "Backlink Holder.md"):
        if path not in files_output:
            raise AssertionError(f"Obsidian file index is missing {path!r}: {files_output!r}")
    total_files = clean(cli.run("files", "ext=md", "total").stdout)
    if not total_files.isdigit() or int(total_files) < 4:
        raise AssertionError(f"Obsidian file total was invalid: {total_files!r}")
    folders_output = clean(cli.run("folders").stdout)
    if "Nested" not in folders_output:
        raise AssertionError(f"Obsidian folder index is missing Nested: {folders_output!r}")
    report.add("obsidian-file-index", f"{total_files} Markdown files and nested folders indexed")

    source_text = assert_output_contains(
        cli.run("read", "path=Source.md"), "obsidian-ci-unique-phrase-48291", "read"
    )
    report.add("obsidian-read", f"{len(source_text)} characters")

    file_info = clean(cli.run("file", "path=Source.md").stdout)
    for fragment in ("Source", "md", "size"):
        if fragment not in file_info:
            raise AssertionError(f"Obsidian file info is missing {fragment!r}: {file_info!r}")
    word_count = clean(cli.run("wordcount", "path=Source.md", "words").stdout)
    if not any(character.isdigit() for character in word_count):
        raise AssertionError(f"Obsidian word count was not numeric: {word_count!r}")
    report.add("obsidian-file-metadata", f"word count {word_count}")

    for name, expected in {
        "id": "kb:repository:architecture:obsidian-contract-source",
        "title": "Obsidian Contract Source",
        "type": "architecture",
        "scope": "repository",
        "rank": "7",
        "verified_flag": "true",
    }.items():
        assert_output_contains(
            cli.run("property:read", f"name={name}", "path=Source.md"), expected, f"property {name}"
        )
    report.add("obsidian-properties", "scalar frontmatter values agree")

    properties = decode_json_output(
        cli.run("properties", "path=Source.md", "format=json").stdout
    )
    property_text = " ".join(flatten_strings(properties))
    for fragment in ("id", "verified_flag", "rank", "Obsidian Contract Source"):
        if fragment not in property_text:
            raise AssertionError(f"Obsidian properties output is missing {fragment!r}: {property_text!r}")
    aliases = clean(cli.run("aliases", "path=Source.md").stdout)
    if "CLI Contract Source" not in aliases:
        raise AssertionError(f"Obsidian aliases output is incomplete: {aliases!r}")
    report.add("obsidian-property-and-alias-index", "typed properties and aliases visible")

    tags_result = cli.run("tags", "path=Source.md", "format=json")
    tag_text = " ".join(flatten_strings(decode_json_output(tags_result.stdout)))
    for tag in ("integration", "frontmatter", "inline-tag"):
        if tag not in tag_text:
            raise AssertionError(f"Obsidian tags output is missing {tag!r}: {tag_text!r}")
    report.add("obsidian-tags", tag_text)

    tag_info = clean(cli.run("tag", "name=integration", "verbose").stdout)
    if "Source.md" not in tag_info or "Target.md" not in tag_info:
        raise AssertionError(f"Obsidian tag reverse index is incomplete: {tag_info!r}")
    report.add("obsidian-tag-reverse-index", "integration tag resolves fixture files")

    links_output = clean(cli.run("links", "path=Source.md").stdout)
    for target in ("Target", "Nested/Second Target", "Missing Contract Target"):
        if target not in links_output:
            raise AssertionError(f"Obsidian outgoing links are missing {target!r}: {links_output!r}")
    report.add("obsidian-links", "resolved, nested, and unresolved links visible")

    backlinks_output = clean(cli.run("backlinks", "path=Target.md", "format=json").stdout)
    if "Source.md" not in backlinks_output:
        raise AssertionError(f"Obsidian backlinks are missing Source.md: {backlinks_output!r}")
    report.add("obsidian-backlinks", "Target.md <- Source.md")

    source_backlinks = clean(cli.run("backlinks", "path=Source.md", "format=json").stdout)
    if "Backlink Holder.md" not in source_backlinks:
        raise AssertionError(f"Obsidian backlinks are missing Backlink Holder.md: {source_backlinks!r}")
    report.add("obsidian-backlink-reverse-index", "Source.md <- Backlink Holder.md")

    unresolved_output = clean(cli.run("unresolved", "verbose", "format=json").stdout)
    if "Missing Contract Target" not in unresolved_output or "Source.md" not in unresolved_output:
        raise AssertionError(f"Obsidian unresolved-link output is incomplete: {unresolved_output!r}")
    report.add("obsidian-unresolved-links", "missing target attributed to Source.md")

    search_output = clean(
        cli.run("search", "query=obsidian-ci-unique-phrase-48291", "format=json").stdout
    )
    if "Source.md" not in search_output:
        raise AssertionError(f"Obsidian search did not find Source.md: {search_output!r}")
    report.add("obsidian-search", "unique phrase resolved to Source.md")

    tasks_output = clean(cli.run("tasks", "path=Source.md", "format=json").stdout)
    if "obsidian-ci-task-sentinel" not in tasks_output:
        raise AssertionError(f"Obsidian task index is missing the fixture task: {tasks_output!r}")
    report.add("obsidian-tasks", "fixture task parsed")

    outline_output = clean(cli.run("outline", "path=Source.md", "format=json").stdout)
    for heading in ("Source Heading", "L0", "L1", "L2"):
        if heading not in outline_output:
            raise AssertionError(f"Obsidian outline is missing heading fragment {heading!r}: {outline_output!r}")
    report.add("obsidian-outline", "fixture headings parsed")

    snapshot = eval_snapshot(cli, "Source.md")
    artifact_dir.joinpath("obsidian-metadata-snapshot.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frontmatter = snapshot.get("frontmatter", {})
    if frontmatter.get("id") != "kb:repository:architecture:obsidian-contract-source":
        raise AssertionError(f"Obsidian metadata cache returned wrong frontmatter: {frontmatter!r}")
    report.add("obsidian-metadata-cache", "queried through app.eval")

    parsed = parse_markdown(vault.joinpath("Source.md").read_text(encoding="utf-8"))
    expected_links = {"Target", "Nested/Second Target", "Missing Contract Target"}
    if set(parsed.links) != expected_links:
        raise AssertionError(f"KB parser link mismatch: {parsed.links!r}")
    if not {"integration", "frontmatter", "inline-tag"}.issubset(set(parsed.tags)):
        raise AssertionError(f"KB parser tag mismatch: {parsed.tags!r}")
    if set(snapshot.get("links", [])) != expected_links:
        raise AssertionError(f"Obsidian metadata-cache link mismatch: {snapshot.get('links')!r}")
    obsidian_tags = set(snapshot.get("tags", []))
    raw_frontmatter_tags = frontmatter.get("tags", [])
    if isinstance(raw_frontmatter_tags, str):
        raw_frontmatter_tags = [raw_frontmatter_tags]
    obsidian_tags.update(str(tag).lstrip("#") for tag in raw_frontmatter_tags)
    if not set(parsed.tags).issubset(obsidian_tags):
        raise AssertionError(f"KB/Obsidian tag parity mismatch: kb={parsed.tags!r}, obsidian={sorted(obsidian_tags)!r}")
    report.add("kb-obsidian-parser-parity", "properties, tags, and links agree")

    config = KBConfig.load(vault)
    with KnowledgeIndex(config) as index:
        stats = index.rebuild()
        if stats.malformed:
            raise AssertionError(f"fixture produced malformed KB records: {stats.to_dict()}")
        source = index.get("kb:repository:architecture:obsidian-contract-source")
        if not source:
            raise AssertionError("KB index did not contain the Obsidian source fixture")
        indexed_links = {
            row[0] for row in index.connection.execute(
                "SELECT target FROM links WHERE source_id=?",
                ("kb:repository:architecture:obsidian-contract-source",),
            )
        }
        if indexed_links != expected_links:
            raise AssertionError(f"KB index link mismatch: {indexed_links!r}")
        manifest = Retriever(config, index).retrieve(
            "obsidian-ci-unique-phrase-48291", budget=240, paths=["Source.md"]
        )
        if "kb:repository:architecture:obsidian-contract-source" not in {item.id for item in manifest.items}:
            raise AssertionError(f"KB retrieval missed the shared-vault source: {manifest.to_dict()}")
    report.add("kb-index-and-retrieval", "same Obsidian vault indexed and retrieved")

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
    linker_content = cli_content("Links to [[Mutable]].\n")
    cli.run("create", "path=Linker.md", f"content={linker_content}", "overwrite")
    wait_for("linker metadata", lambda: "Mutable" in clean(cli.run("links", "path=Linker.md").stdout))
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
            raise AssertionError(f"KB parser missed Obsidian property mutation: {cli_record['metadata']!r}")
    report.add("post-mutation-kb-reindex", json.dumps(final_stats.to_dict(), sort_keys=True))

    bridge = ObsidianBridge(vault, binary=cli.binary, vault_name=cli.vault_name)
    status = bridge.status(timeout=10.0)
    if not status.responsive or not status.vault_responsive:
        raise AssertionError(f"ObsidianBridge did not recognize the live app/vault: {status.to_dict()}")
    bridge_read = bridge.run("read", "path=Source.md", timeout=10.0)
    if bridge_read.returncode != 0 or "obsidian-ci-unique-phrase-48291" not in bridge_read.stdout:
        raise AssertionError(f"ObsidianBridge targeted command failed: {bridge_read}")
    report.add("production-obsidian-bridge", status.version)

    screenshot_path = artifact_dir / "obsidian-workspace.png"
    cli.run(
        "dev:screenshot", f"path={screenshot_path}",
        check=True, timeout=30.0,
    )
    if not screenshot_path.exists() or screenshot_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("Obsidian developer screenshot was not written as a valid PNG")
    report.add("obsidian-headless-renderer", f"PNG screenshot {screenshot_path.stat().st_size} bytes")

    return report
