from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .index import KnowledgeIndex


def graph_data(index: KnowledgeIndex, include_archived: bool = False) -> dict[str, Any]:
    statuses = None if include_archived else {"active", "inbox", "stale", "conflicted"}
    notes = index.all_notes(statuses)
    allowed = {note["id"] for note in notes}
    nodes = [
        {
            "id": note["declared_id"] or note["id"],
            "internal_id": note["id"],
            "path": note["path"],
            "title": note["title"],
            "kind": note.get("kind", "semantic"),
            "type": note["type"],
            "scope": note["scope"],
            "status": note["status"],
        }
        for note in notes
    ]
    edges = []
    for row in index.connection.execute(
        "SELECT source_id,target,target_id,relation,provenance FROM links ORDER BY source_id,target"
    ):
        if row["source_id"] in allowed:
            edges.append(
                {
                    "source": row["source_id"],
                    "target": row["target_id"] or row["target"],
                    "raw_target": row["target"],
                    "relation": row["relation"],
                    "provenance": row["provenance"],
                    "resolved": bool(row["target_id"]),
                }
            )
    return {"nodes": nodes, "edges": edges}


def personalized_pagerank(
    index: KnowledgeIndex, seeds: list[str], damping: float = 0.85, iterations: int = 20
) -> list[dict[str, Any]]:
    data = graph_data(index)
    ids = {node["internal_id"] for node in data["nodes"]}
    seed_ids = {index.get(seed)["id"] for seed in seeds if index.get(seed)}
    if not seed_ids:
        return []
    outgoing: dict[str, set[str]] = defaultdict(set)
    for edge in data["edges"]:
        source, target = edge["source"], edge["target"]
        if source in ids and target in ids:
            outgoing[source].add(target)
    teleport = {node: (1 / len(seed_ids) if node in seed_ids else 0.0) for node in ids}
    score = dict(teleport)
    for _ in range(iterations):
        nxt = {node: (1 - damping) * teleport[node] for node in ids}
        for source, value in score.items():
            targets = outgoing.get(source)
            if targets:
                share = damping * value / len(targets)
                for target in targets:
                    nxt[target] += share
            else:
                for seed in seed_ids:
                    nxt[seed] += damping * value / len(seed_ids)
        score = nxt
    ranked = []
    for memory_id, value in sorted(score.items(), key=lambda pair: (-pair[1], pair[0])):
        note = index.get(memory_id)
        if note:
            ranked.append({"id": note["declared_id"] or note["id"], "path": note["path"], "score": round(value, 8)})
    return ranked


def render_graph(index: KnowledgeIndex, format: str = "json", include_archived: bool = False) -> str:
    data = graph_data(index, include_archived)
    if format == "json":
        return json.dumps(data, indent=2)
    if format == "dot":
        lines = ["digraph knowledge {", "  rankdir=LR;"]
        aliases = {node["internal_id"]: node["id"] for node in data["nodes"]}
        for node in data["nodes"]:
            label = str(node["title"]).replace('"', '\\"')
            lines.append(f'  "{node["internal_id"]}" [label="{label}\\n{node["type"]}/{node["scope"]}"];')
        for edge in data["edges"]:
            relation = str(edge["relation"]).replace('"', '\\"')
            lines.append(f'  "{edge["source"]}" -> "{edge["target"]}" [label="{relation}"];')
        lines.append("}")
        return "\n".join(lines)
    if format == "mermaid":
        lines = ["graph LR"]
        aliases = {node["internal_id"]: f"N{i}" for i, node in enumerate(data["nodes"])}
        for node in data["nodes"]:
            label = str(node["title"]).replace('"', "'")
            lines.append(f'  {aliases[node["internal_id"]]}["{label}"]')
        for edge in data["edges"]:
            source = aliases.get(edge["source"], f"X{abs(hash(edge['source']))}")
            target = aliases.get(edge["target"], f"X{abs(hash(edge['target']))}")
            lines.append(f'  {source} -- "{edge["relation"]}" --> {target}')
        return "\n".join(lines)
    raise ValueError(f"unsupported graph format: {format}")
