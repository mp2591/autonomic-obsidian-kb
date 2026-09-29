from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import stable_json

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_LAYER = re.compile(r"^##\s+L([0-4])(?:\s*[-—:]\s*|\s+)(.*)$", re.MULTILINE | re.IGNORECASE)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_TAG = re.compile(r"(?<![\w/])#([A-Za-z0-9_/-]+)")


@dataclass(slots=True)
class ParsedMarkdown:
    metadata: dict[str, Any]
    body: str
    layers: dict[int, str]
    links: list[str]
    tags: list[str]


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if value[:1] in {"[", "{", '"'} or value[-1:] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value.strip("'\"")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    if yaml is not None:
        try:
            loaded = yaml.safe_load(raw)
            if isinstance(loaded, dict):
                # YAML dates are a native Obsidian property type. Canonicalize them
                # before schema validation; reject cycles/non-finite scalar data.
                normalized = json.loads(json.dumps(loaded, default=str, allow_nan=False))
                return normalized, text[match.end() :]
            return {}, text[match.end() :]
        except Exception:
            return {"_parse_error": "invalid YAML metadata"}, text[match.end() :]
    metadata: dict[str, Any] = {}
    key: str | None = None
    list_values: list[Any] | None = None
    for raw_line in raw.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith(("  - ", "- ")) and key is not None:
            if list_values is None:
                list_values = []
                metadata[key] = list_values
            list_values.append(_scalar(raw_line.split("-", 1)[1]))
            continue
        if ":" not in raw_line:
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        metadata[key] = _scalar(raw_value)
        list_values = None
    return metadata, text[match.end() :]


def dump_frontmatter(metadata: dict[str, Any]) -> str:
    preferred = [
        "schema_version",
        "id",
        "title",
        "kind",
        "type",
        "scope",
        "repo",
        "repository_id",
        "project",
        "module",
        "branch",
        "status",
        "summary",
        "confidence",
        "authority",
        "taint",
        "authorized_instruction",
        "created",
        "updated",
        "validated",
        "freshness",
        "validity",
        "token_cost",
        "utility",
        "claim_key",
        "claim_value",
        "applies_to",
        "agents",
        "provenance",
        "evidence",
        "validators",
        "relations",
        "invalidation",
        "supersedes",
        "superseded_by",
        "tags",
    ]
    keys = [key for key in preferred if key in metadata]
    keys.extend(sorted(key for key in metadata if key not in set(keys)))
    lines = ["---"]
    for key in keys:
        value = metadata[key]
        if isinstance(value, str):
            # Always quote strings: "off", "false", and dates must not change type.
            rendered = json.dumps(value, ensure_ascii=False)
        elif value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, dict | list):
            rendered = stable_json(value)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def extract_layers(body: str, summary: str = "") -> dict[int, str]:
    matches = list(_LAYER.finditer(body))
    layers: dict[int, str] = {}
    if matches:
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            layers[int(match.group(1))] = body[start:end].strip()
    else:
        stripped = body.strip()
        if summary:
            layers[1] = summary.strip()
        if stripped:
            first_paragraph = stripped.split("\n\n", 1)[0].strip()
            layers.setdefault(1, first_paragraph[:320])
            layers[2] = stripped[:1600]
            layers[3] = stripped
    if 1 not in layers and summary:
        layers[1] = summary.strip()
    if 0 not in layers:
        layers[0] = layers.get(1, summary).split("\n", 1)[0][:140]
    return layers


def parse_markdown(text: str) -> ParsedMarkdown:
    metadata, body = parse_frontmatter(text)
    layers = extract_layers(body, str(metadata.get("summary", "")))
    links = sorted(set(link.strip() for link in _WIKILINK.findall(body)))
    tags = sorted(set(_TAG.findall(body)))
    raw_tags = metadata.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if isinstance(raw_tags, list):
        tags = sorted(set(tags) | {str(tag).lstrip("#") for tag in raw_tags})
    return ParsedMarkdown(metadata=metadata, body=body, layers=layers, links=links, tags=tags)


def render_note(metadata: dict[str, Any], layers: dict[int, str], extra_body: str = "") -> str:
    labels = {0: "Pointer", 1: "Fact", 2: "Summary", 3: "Detail", 4: "Provenance"}
    sections: list[str] = []
    for level in sorted(layers):
        content = layers[level].strip()
        if content:
            sections.append(f"## L{level} — {labels.get(level, 'Layer')}\n\n{content}")
    if extra_body.strip():
        sections.append(extra_body.strip())
    return dump_frontmatter(metadata) + "\n\n".join(sections).rstrip() + "\n"


def read_note(path: Path) -> ParsedMarkdown:
    return parse_markdown(path.read_text(encoding="utf-8"))
