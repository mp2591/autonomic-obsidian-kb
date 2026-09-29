from __future__ import annotations

import ast
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .util import atomic_write, sha256_file

_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs", ".toml", ".yaml", ".yml", ".json"}
_IGNORED = {"node_modules", "dist", "build", "__pycache__"}
_MAX_BYTES = 2_000_000
_CACHE_VERSION = 2


class RepositoryCodeGraph:
    """Disposable code projection with qualified Python symbols and per-file invalidation.

    Each file is parsed only when its stat identity (size, mtime, ctime, inode) changes.
    Retrieval asks for the symbols of a few active paths and never walks the repository;
    the whole-repository projection prunes ignored directories and re-parses only
    changed files.
    """

    def __init__(self, repo: Path, cache_path: Path):
        self.repo = repo.resolve()
        self.cache_path = cache_path
        self._cache: dict[str, Any] | None = None
        self._dirty = False

    def _entries(self) -> dict[str, Any]:
        if self._cache is None:
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                valid = data.get("version") == _CACHE_VERSION and data.get("root") == str(self.repo)
                self._cache = data if valid and isinstance(data.get("entries"), dict) else None
            except (OSError, ValueError, TypeError, AttributeError):
                self._cache = None
            if self._cache is None:
                self._cache = {"version": _CACHE_VERSION, "root": str(self.repo), "entries": {}}
        return self._cache["entries"]

    def _save(self) -> None:
        if self._dirty and self._cache is not None:
            atomic_write(self.cache_path, json.dumps(self._cache, separators=(",", ":")))
            self._dirty = False

    def _contained(self, value: str) -> tuple[str, Path] | None:
        """Repository-relative path and file for an eligible source file, else None."""
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(self.repo)
            except (OSError, ValueError):
                return None
        parts = PurePosixPath(candidate.as_posix()).parts
        if not parts or ".." in parts or any(part.startswith(".") for part in parts):
            return None
        if any(part in _IGNORED for part in parts[:-1]):
            return None
        path = self.repo.joinpath(*parts)
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.repo):
                return None
            if path.suffix not in _SUFFIXES or path.stat().st_size > _MAX_BYTES:
                return None
        except OSError:
            return None
        return PurePosixPath(*parts).as_posix(), path

    def _entry(self, relative: str, path: Path) -> dict[str, Any]:
        stat = path.stat()
        key = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
        entries = self._entries()
        cached = entries.get(relative)
        if cached and cached.get("key") == key:
            return cached
        entry = {"key": key, "sha256": sha256_file(path), "imports": [], "symbols": []}
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeError, ValueError):
                tree = None
            if tree is not None:
                entry["symbols"] = _symbols(tree)
                entry["imports"] = _imports(tree)
        entries[relative] = entry
        self._dirty = True
        return entry

    def _files(self) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for directory, subdirectories, filenames in os.walk(self.repo):
            subdirectories[:] = sorted(
                name for name in subdirectories if not name.startswith(".") and name not in _IGNORED
            )
            for name in sorted(filenames):
                path = Path(directory) / name
                relative = path.relative_to(self.repo).as_posix()
                if self._contained(relative):
                    files[relative] = path
        return files

    def build(self) -> dict[str, Any]:
        files = self._files()
        entries = self._entries()
        for relative in set(entries) - set(files):
            del entries[relative]
            self._dirty = True
        nodes: list[dict[str, Any]] = []
        hashes: dict[str, str] = {}
        for relative, path in files.items():
            entry = self._entry(relative, path)
            hashes[relative] = entry["sha256"]
            nodes.append(
                {
                    "id": f"file:{relative}",
                    "kind": "file",
                    "path": relative,
                    "name": Path(relative).name,
                    "imports": entry["imports"],
                }
            )
            nodes.extend(
                {
                    "id": f"symbol:{relative}:{symbol['name']}:{symbol['line']}",
                    "kind": "symbol",
                    "path": relative,
                    **symbol,
                }
                for symbol in entry["symbols"]
            )
        self._save()
        return {"root": str(self.repo), "files": hashes, "nodes": nodes}

    def load(self) -> dict[str, Any]:
        return self.build()

    def symbols_for_paths(self, paths: list[str]) -> list[str]:
        names: set[str] = set()
        for value in paths:
            found = self._contained(str(value))
            if found:
                names.update(symbol["name"] for symbol in self._entry(*found)["symbols"])
        self._save()
        return sorted(names)

    def search_symbols(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        lower = query.lower()
        return [
            node
            for node in self.load()["nodes"]
            if node["kind"] != "file" and (lower in node["name"].lower() or node["name"].lower() in lower)
        ][:limit]


def _symbols(tree: ast.AST) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []

    def visit(node: ast.AST, scope: str = "") -> None:
        nested_scope = scope
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = f"{scope}.{node.name}" if scope else node.name
            symbols.append({"name": name, "line": node.lineno, "end_line": node.end_lineno})
            nested_scope = name
        for child in ast.iter_child_nodes(node):
            visit(child, nested_scope)

    visit(tree)
    return symbols


def _imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append("." * node.level + (node.module or ""))
    return imports
