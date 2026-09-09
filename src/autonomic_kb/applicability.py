"""Source- and version-bound applicability, independent of retrieval scores."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import TaskContext
from .util import is_time_active, parse_time, sha256_file


def source_dependencies(metadata: dict[str, Any]) -> list[dict[str, str]]:
    dependencies = []
    for key in ("dependencies", "provenance", "validators"):
        values = metadata.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            path, digest = value.get("path"), value.get("sha256") or value.get("source_hash")
            if path and digest:
                dependencies.append({"path": str(path), "sha256": str(digest)})
    return dependencies


def dependencies_match(metadata: dict[str, Any], root: Path | None) -> tuple[bool, str]:
    dependencies = source_dependencies(metadata)
    if dependencies and root is None:
        return False, "source repository unavailable"
    for dependency in dependencies:
        path = (root / dependency["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            return False, "source dependency escaped repository"
        try:
            if sha256_file(path) != dependency["sha256"]:
                return False, "source dependency changed"
        except OSError:
            return False, "source dependency unavailable"
    return True, "source dependencies applicable"


def applicability_gate(note: dict[str, Any], context: TaskContext, root: Path | None) -> tuple[bool, str]:
    metadata = note.get("metadata", {})
    start, end = str(note.get("valid_from", "")), str(note.get("valid_to", ""))
    at = parse_time(context.at) if context.at else None
    if (context.at and at is None) or any(value and parse_time(value) is None for value in (start, end)):
        return False, "invalid temporal boundary"
    if not is_time_active(start, end, at=at):
        return False, "outside valid-time interval"
    version_range = str(note.get("version_range", ""))
    if version_range:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        package = str(metadata.get("version_package", ""))
        version = context.versions.get(package)
        if not package or version is None:
            return False, "version applicability unknown"
        try:
            if Version(version) not in SpecifierSet(version_range):
                return False, "outside version range"
        except (InvalidSpecifier, InvalidVersion):
            return False, "invalid version applicability"
    commit = str(note.get("as_of_commit", ""))
    if commit:
        from .git_context import is_ancestor

        if not context.head:
            return False, "commit context unavailable"
        if commit != context.head:
            if not root or not is_ancestor(root, commit, context.head):
                return False, "memory commit is not in active lineage"
            if not source_dependencies(metadata):
                return False, "ancestor memory requires unchanged source dependencies"
    return dependencies_match(metadata, root)


def requested_time(task: str) -> str:
    match = re.search(r"\bas of\s+(\d{4}-\d{2}-\d{2}(?:T[0-9:]+Z)?)", task, re.I)
    return match.group(1) if match else ""
