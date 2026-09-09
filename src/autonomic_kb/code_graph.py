from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .util import sha256_file


@dataclass(slots=True)
class CodeNode:
    id: str
    kind: str
    path: str
    name: str
    line: int = 0
    imports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RepositoryCodeGraph:
    """Dependency-free code intelligence projection.

    Python gets AST-level symbols/imports. Other languages still participate as file nodes.
    The graph is a rebuildable acceleration cache, never semantic authority.
    """

    def __init__(self, repo: Path, cache_path: Path):
        self.repo = repo.resolve()
        self.cache_path = cache_path

    def build(self) -> dict[str, Any]:
        nodes: list[CodeNode] = []
        file_hashes: dict[str, str] = {}
        ignored = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}
        for path in self.repo.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.relative_to(self.repo).parts):
                continue
            relative = path.relative_to(self.repo).as_posix()
            if path.suffix not in {
                ".py",
                ".js",
                ".ts",
                ".tsx",
                ".jsx",
                ".go",
                ".rs",
                ".java",
                ".cs",
                ".toml",
                ".yaml",
                ".yml",
                ".json",
            }:
                continue
            try:
                file_hashes[relative] = sha256_file(path)
            except OSError:
                continue
            nodes.append(CodeNode(id=f"file:{relative}", kind="file", path=relative, name=path.name))
            if path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
                except SyntaxError:
                    continue
                imports: list[str] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        nodes.append(
                            CodeNode(
                                id=f"symbol:{relative}:{node.name}",
                                kind="class" if isinstance(node, ast.ClassDef) else "function",
                                path=relative,
                                name=node.name,
                                line=getattr(node, "lineno", 0),
                            )
                        )
                if imports:
                    # Store imports on the file node.
                    for item in reversed(nodes):
                        if item.id == f"file:{relative}":
                            item.imports = sorted(set(imports))
                            break
        data = {"files": file_hashes, "nodes": [node.to_dict() for node in nodes]}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return data

    def load(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return self.build()
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return self.build()
        cached = data.get("files", {})
        for relative, digest in cached.items():
            path = self.repo / relative
            if not path.exists():
                return self.build()
            try:
                if sha256_file(path) != digest:
                    return self.build()
            except OSError:
                return self.build()
        return data

    def symbols_for_paths(self, paths: list[str]) -> list[str]:
        wanted = {Path(path).as_posix() for path in paths}
        data = self.load()
        return sorted(
            {
                str(node["name"])
                for node in data.get("nodes", [])
                if node.get("kind") != "file" and node.get("path") in wanted
            }
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        lower = query.lower()
        matches = []
        for node in self.load().get("nodes", []):
            if node.get("kind") == "file":
                continue
            name = str(node.get("name", ""))
            if name.lower() in lower or lower in name.lower():
                matches.append(node)
        return matches[:limit]
