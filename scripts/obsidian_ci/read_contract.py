from __future__ import annotations

from .cli import OfficialCLI, assert_output_contains, clean, decode_json_output, flatten_strings
from .report import IntegrationReport


def verify_files_properties_and_tags(cli: OfficialCLI, report: IntegrationReport) -> None:
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
    report.add("obsidian-file-metadata", "official file-info command returned path/type/size metadata")

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

    properties = decode_json_output(cli.run("properties", "path=Source.md", "format=json").stdout)
    property_text = " ".join(flatten_strings(properties))
    for fragment in ("id", "verified_flag", "rank", "Obsidian Contract Source"):
        if fragment not in property_text:
            raise AssertionError(f"Obsidian properties output is missing {fragment!r}: {property_text!r}")
    aliases = clean(cli.run("aliases", "path=Source.md").stdout)
    if "CLI Contract Source" not in aliases:
        raise AssertionError(f"Obsidian aliases output is incomplete: {aliases!r}")
    report.add("obsidian-property-and-alias-index", "typed properties and aliases visible")

    tags = decode_json_output(cli.run("tags", "path=Source.md", "format=json").stdout)
    tag_text = " ".join(flatten_strings(tags))
    for tag in ("integration", "frontmatter", "inline-tag"):
        if tag not in tag_text:
            raise AssertionError(f"Obsidian tags output is missing {tag!r}: {tag_text!r}")
    report.add("obsidian-tags", tag_text)

    tag_info = clean(cli.run("tag", "name=integration", "verbose").stdout)
    if "Source.md" not in tag_info or "Target.md" not in tag_info:
        raise AssertionError(f"Obsidian tag reverse index is incomplete: {tag_info!r}")
    report.add("obsidian-tag-reverse-index", "integration tag resolves fixture files")

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
