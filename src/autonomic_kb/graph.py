from __future__ import annotations

import json
from typing import Any

from .index import KnowledgeIndex


def graph_data(index: KnowledgeIndex, include_archived: bool = False) -> dict[str, Any]:
    statuses = None if include_archived else {"active", "inbox", "stale", "conflicted"}
    notes = index.all_notes(statuses)
    allowed = {note["id"] for note in notes}
    nodes = [
        {
            "id": note["declared_id"] or note["id"], "path": note["path"], "title": note["title"],
            "type": note["type"], "scope": note["scope"], "status": note["status"],
        }
        for note in notes
    ]
    edges = []
    for row in index.connection.execute("SELECT source_id,target,relation FROM links ORDER BY source_id,target"):
        if row["source_id"] in allowed:
            edges.append({"source": row["source_id"], "target": row["target"], "relation": row["relation"]})
    return {"nodes": nodes, "edges": edges}


def render_graph(index: KnowledgeIndex, format: str = "json", include_archived: bool = False) -> str:
    data = graph_data(index, include_archived)
    if format == "json":
        return json.dumps(data, indent=2)
    if format == "dot":
        lines = ["digraph knowledge {", "  rankdir=LR;"]
        for node in data["nodes"]:
            label = str(node["title"]).replace('"', '\\"')
            lines.append(f'  "{node["id"]}" [label="{label}\\n{node["type"]}/{node["scope"]}"];')
        for edge in data["edges"]:
            relation = str(edge["relation"]).replace('"', '\\"')
            lines.append(f'  "{edge["source"]}" -> "{edge["target"]}" [label="{relation}"];')
        lines.append("}")
        return "\n".join(lines)
    if format == "mermaid":
        lines = ["graph LR"]
        aliases = {node["id"]: f"N{index}" for index, node in enumerate(data["nodes"])}
        for node in data["nodes"]:
            label = str(node["title"]).replace('"', "'")
            lines.append(f'  {aliases[node["id"]]}["{label}"]')
        for edge in data["edges"]:
            source = aliases.get(edge["source"], f'X{abs(hash(edge["source"]))}')
            target = aliases.get(edge["target"], f'X{abs(hash(edge["target"]))}')
            lines.append(f'  {source} -- "{edge["relation"]}" --> {target}')
        return "\n".join(lines)
    raise ValueError(f"unsupported graph format: {format}")
