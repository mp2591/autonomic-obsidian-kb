from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from .util import atomic_write, sha256_file

_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs", ".toml", ".yaml", ".yml", ".json"}
_IGNORED = {"node_modules", "dist", "build", "__pycache__"}


class RepositoryCodeGraph:
    """Disposable code projection with qualified Python symbols and source invalidation."""

    def __init__(self, repo: Path, cache_path: Path):
        self.repo = repo.resolve()
        self.cache_path = cache_path

    def _files(self) -> dict[str, str]:
        files = {}
        for path in sorted(self.repo.rglob("*")):
            relative = path.relative_to(self.repo)
            if any(part.startswith(".") or part in _IGNORED for part in relative.parts):
                continue
            if path.suffix not in _SUFFIXES or not path.is_file() or path.is_symlink():
                continue
            if not path.resolve().is_relative_to(self.repo) or path.stat().st_size > 2_000_000:
                continue
            files[relative.as_posix()] = sha256_file(path)
        return files

    def build(self) -> dict[str, Any]:
        files = self._files()
        nodes = []
        for relative in files:
            file_node = {
                "id": f"file:{relative}",
                "kind": "file",
                "path": relative,
                "name": Path(relative).name,
                "imports": [],
            }
            nodes.append(file_node)
            if not relative.endswith(".py"):
                continue
            try:
                tree = ast.parse((self.repo / relative).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeError):
                continue

            def visit(node: ast.AST, scope: str = "", path: str = relative) -> None:
                nested_scope = scope
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    name = f"{scope}.{node.name}" if scope else node.name
                    nodes.append(
                        {
                            "id": f"symbol:{path}:{name}:{node.lineno}",
                            "kind": "symbol",
                            "path": path,
                            "name": name,
                            "line": node.lineno,
                            "end_line": node.end_lineno,
                        }
                    )
                    nested_scope = name
                for child in ast.iter_child_nodes(node):
                    visit(child, nested_scope, path)

            visit(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    file_node["imports"].extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    file_node["imports"].append("." * node.level + (node.module or ""))
        data = {"root": str(self.repo), "files": files, "nodes": nodes}
        atomic_write(self.cache_path, json.dumps(data, separators=(",", ":")))
        return data

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("root") == str(self.repo) and data.get("files") == self._files():
                return data
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return self.build()

    def symbols_for_paths(self, paths: list[str]) -> list[str]:
        wanted = {Path(path).as_posix() for path in paths}
        return sorted(
            {str(node["name"]) for node in self.load()["nodes"] if node["kind"] != "file" and node["path"] in wanted}
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        lower = query.lower()
        return [
            node
            for node in self.load()["nodes"]
            if node["kind"] != "file" and (lower in node["name"].lower() or node["name"].lower() in lower)
        ][:limit]
