from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from .config import KBConfig
from .markdown import dump_frontmatter, parse_markdown
from .models import TYPE_KIND
from .storage import semantic_transaction
from .util import atomic_write, utc_now


def migrate_metadata(metadata: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    version = int(metadata.get("schema_version", 1) or 1)
    changed: list[str] = []
    value = dict(metadata)
    if version < 2:
        value["schema_version"] = 2
        changed.append("schema_version")
        memory_type = str(value.get("type", "fact"))
        if "kind" not in value:
            value["kind"] = TYPE_KIND.get(memory_type, "semantic")
            changed.append("kind")
        if "validity" not in value:
            value["validity"] = {"valid_from": "", "valid_to": "", "as_of_commit": "", "version_range": ""}
            changed.append("validity")
        if "taint" not in value:
            authority = str(value.get("authority", "agent"))
            value["taint"] = (
                "trusted"
                if authority in {"source-of-truth", "authoritative", "verified", "user-corrected"}
                else authority
                if authority in {"agent", "external", "untrusted"}
                else "derived"
            )
            changed.append("taint")
        if memory_type == "agent-instruction" and "authorized_instruction" not in value:
            value["authorized_instruction"] = False
            changed.append("authorized_instruction")
        value["updated"] = str(value.get("updated") or utc_now())
    return value, changed


def migrate_vault(config: KBConfig, apply: bool = False) -> dict[str, Any]:
    results = []
    ignored = {".git", ".obsidian", ".kb", ".kb-evidence", ".kb-memory-events", ".kb-episodes", ".venv"}
    with semantic_transaction(config.vault) if apply else nullcontext():
        for path in config.vault.rglob("*.md"):
            relative = path.relative_to(config.vault)
            if any(part in ignored for part in relative.parts):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(config.vault.resolve()):
                continue
            text = path.read_text(encoding="utf-8")
            parsed = parse_markdown(text)
            metadata, changed = migrate_metadata(parsed.metadata)
            if changed:
                if apply:
                    atomic_write(path, dump_frontmatter(metadata) + parsed.body.lstrip())
                results.append({"path": relative.as_posix(), "changes": changed, "applied": apply})
    return {"mode": "apply" if apply else "dry-run", "memories": len(results), "results": results}
