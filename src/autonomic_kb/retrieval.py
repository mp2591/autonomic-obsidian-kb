from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .config import KBConfig
from .code_graph import RepositoryCodeGraph
from .calibration import RankPolicy
from .git_context import inspect_git
from .index import KnowledgeIndex
from .models import RetrievalItem, RetrievalManifest, TaskContext
from .observability import TraceRecorder
from .scoring import classify_risk, classify_task, feature_vector, score_note
from .semantic import LocalEmbeddingBackend
from .util import estimate_tokens, jaccard, terms

_TEMPORAL = re.compile(r"\b(previous|old|older|before|after|as of|version|release|branch|commit|historical|then)\b", re.I)
_EXACTISH = re.compile(r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+|\b[A-Z][A-Za-z0-9_]{2,}\b|\b[a-z_][a-z0-9_]*\([^)]*\)|\b\w+\.\w+\b)")


def reciprocal_rank_fusion(result_sets: dict[str, list[dict[str, Any]]], k: int = 60) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, rows in result_sets.items():
        for rank, row in enumerate(rows, 1):
            identity = str(row["id"])
            target = merged.setdefault(identity, dict(row))
            target.setdefault("route_sources", [])
            if route not in target["route_sources"]:
                target["route_sources"].append(route)
            target["rrf"] = float(target.get("rrf", 0.0)) + 1.0 / (k + rank)
            for field in ("lexical", "exact", "bm25", "graph_relation", "graph_source"):
                if field in row and field not in target:
                    target[field] = row[field]
                elif field in row and field in {"lexical", "exact"}:
                    target[field] = max(float(target.get(field, 0.0)), float(row[field]))
    if not merged:
        return []
    max_rrf = max(float(item.get("rrf", 0.0)) for item in merged.values()) or 1.0
    for item in merged.values():
        item["rrf_norm"] = float(item.get("rrf", 0.0)) / max_rrf
    return sorted(merged.values(), key=lambda item: (-float(item.get("rrf", 0.0)), item["id"]))


class Retriever:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None
        self.traces = TraceRecorder(config.trace_path)
        self.embedding = LocalEmbeddingBackend(config)
        self.code_graph = RepositoryCodeGraph(config.repo, config.runtime_dir / "code-graph.json") if config.repo else None
        self.rank_policy = RankPolicy.load(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def build_context(self, task: str, requested_paths: list[str] | None = None, agent: str = "generic", session: str = "") -> TaskContext:
        git = inspect_git(self.config.repo or Path.cwd())
        repo_name = Path(git.root).name if git.root else (self.config.repo.name if self.config.repo else "")
        paths = requested_paths or []
        module = Path(paths[0]).parts[0] if paths and Path(paths[0]).parts else ""
        active_paths = paths + git.changed_paths
        symbols = self.code_graph.symbols_for_paths(active_paths[:20]) if self.code_graph and active_paths else []
        return TaskContext(
            task=task, cwd=str(Path.cwd()), repo=repo_name, repository_id=git.repository_id, project=repo_name,
            module=module, branch=git.branch, head=git.head, merge_base=git.merge_base,
            changed_paths=git.changed_paths, requested_paths=paths, changed_symbols=symbols[:40], agent=agent, session=session,
            task_types=classify_task(task), risk=classify_risk(task),
        )

    def choose_route(self, context: TaskContext) -> str:
        stripped = context.task.strip()
        if len(terms(stripped)) <= 1 and not context.requested_paths:
            return "none"
        if _TEMPORAL.search(stripped):
            return "temporal"
        if _EXACTISH.search(stripped) or context.requested_paths:
            return "exact+lexical"
        if any(kind in context.task_types for kind in ("known-failure", "solution", "architecture", "decision")):
            return "hybrid"
        return "lexical"

    def _candidate_sets(self, context: TaskContext, route: str) -> dict[str, list[dict[str, Any]]]:
        sets: dict[str, list[dict[str, Any]]] = {}
        if route == "none":
            return sets
        if "exact" in route or route == "hybrid":
            sets["exact"] = self.index.search_exact(context.task, min(40, self.config.max_candidates))
            for path in context.requested_paths[:8]:
                sets.setdefault("path", []).extend(self.index.search_exact(path, 12))
        symbol_hint = " ".join(context.changed_symbols[:8])
        lexical_query = f"{context.task} {symbol_hint}".strip()
        sets["lexical"] = self.index.search_lexical(lexical_query, self.config.max_candidates)
        if route in {"hybrid", "temporal"}:
            # Cheap semantic proxy remains available without model dependencies.
            expansion = " ".join(context.task_types[:4])
            if expansion:
                sets["expanded"] = self.index.search_lexical(f"{context.task} {expansion}", self.config.max_candidates // 2)
            if "dense" in self.config.retrieval_routes and self.embedding.available:
                sets["dense"] = self.embedding.search(self.index, context.task, min(80, self.config.max_candidates))
        return sets

    def retrieve(
        self, task: str, budget: int | None = None, depth: str | int = "auto", paths: list[str] | None = None,
        agent: str = "generic", session: str = "", include_uncertain: bool = False, route_override: str | None = None,
    ) -> RetrievalManifest:
        self.index.index_vault()
        budget = max(80, int(budget or self.config.default_budget))
        context = self.build_context(task, paths, agent, session)
        route = route_override or self.choose_route(context)
        trace_id = self.traces.new_trace_id()
        retrieval_id = uuid.uuid4().hex[:16]
        with self.traces.span(trace_id, "retrieval", retrieval_id=retrieval_id, route=route, budget=budget, task_hash=context.task_hash):
            result_sets = self._candidate_sets(context, route)
            fused = reciprocal_rank_fusion(result_sets, self.config.rrf_k)
            # Strong seeds add a bounded graph channel; graph never bypasses gates.
            seed_ids = [row["id"] for row in fused[:3]]
            if seed_ids and route in {"hybrid", "temporal", "exact+lexical"}:
                result_sets["graph"] = self.index.neighbors(seed_ids, limit=24)
                fused = reciprocal_rank_fusion(result_sets, self.config.rrf_k)
            scored: list[tuple[float, dict[str, Any], list[str]]] = []
            excluded: list[dict[str, Any]] = []
            decisions: list[dict[str, Any]] = []
            allow_untrusted = include_uncertain or self.config.allow_untrusted
            for note in fused:
                score, reasons, gate = score_note(
                    note, context, allow_untrusted, self.config.allow_cross_repo, self.config.repo,
                    self.rank_policy.weights, self.rank_policy.intercept,
                )
                if gate:
                    excluded.append({"id": note["id"], "path": note["path"], "reason": gate, "score": 0.0})
                    decisions.append({"id": note["id"], "selected": False, "score": 0.0, "reasons": [gate]})
                elif score < self.config.minimum_score:
                    reason = f"score {score:.3f} below minimum {self.config.minimum_score:.3f}"
                    excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                    decisions.append({"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]})
                else:
                    scored.append((score, note, reasons))
            scored.sort(key=lambda value: (-value[0], value[1]["token_cost"], value[1]["id"]))
            for score, note, _ in scored:
                self.index.record_rank_example(retrieval_id, note["id"], feature_vector(note, context, self.config.repo), False)
            reserved = min(88, max(32, budget // 12))
            remaining = budget - reserved
            items, remaining = self._allocate_set(scored, remaining, depth, context, decisions, excluded, agent)
            for item in items:
                internal = self.index.get(item.id)
                if internal:
                    self.index.record_rank_example(retrieval_id, internal["id"], feature_vector(internal, context, self.config.repo), True)
            used = budget - remaining
            if route == "none":
                state = "no_retrieval_needed"
            elif not items and any("conflict" in str(item.get("reason", "")) for item in excluded):
                state = "conflicting_evidence"
            elif not items:
                state = "insufficient_evidence"
            else:
                state = "sufficient_context"
            manifest = RetrievalManifest(
                task=task, budget=budget, used_tokens=min(budget, used), items=items, excluded=excluded[:80],
                context=context, retrieval_id=retrieval_id, route=route, state=state, trace_id=trace_id,
            )
            self.index.record_decisions(retrieval_id, decisions)
            self.index.record_usage(retrieval_id, context.task_hash, [item.to_dict() for item in items])
            self.index.events.emit("retrieve.completed", retrieval_id=retrieval_id, trace_id=trace_id,
                                   task_hash=context.task_hash, route=route, state=state, budget=budget,
                                   used_tokens=manifest.used_tokens, selected=[item.id for item in items], excluded=len(excluded))
            return manifest

    def _allocate_set(
        self, scored: list[tuple[float, dict[str, Any], list[str]]], remaining: int, depth: str | int,
        context: TaskContext, decisions: list[dict[str, Any]], excluded: list[dict[str, Any]], agent: str,
    ) -> tuple[list[RetrievalItem], int]:
        items: list[RetrievalItem] = []
        covered_types: set[str] = set()
        pool = list(scored)
        while pool and remaining >= 20:
            best_index = -1
            best_gain = -1.0
            best_payload: tuple[int, str, int, float] | None = None
            for idx, (score, note, _) in enumerate(pool):
                layer, text = self._select_layer(note, score, depth)
                token_cost = estimate_tokens(text, agent) + 14
                while layer > 0 and token_cost > remaining:
                    layer -= 1
                    text = str(note.get(f"l{layer}") or note.get("summary") or note.get("title"))
                    token_cost = estimate_tokens(text, agent) + 14
                if token_cost > remaining:
                    continue
                redundancy = max((jaccard(text, item.text) for item in items), default=0.0)
                novelty = 1.0 - redundancy
                type_bonus = 0.09 if note.get("type") in context.task_types and note.get("type") not in covered_types else 0.0
                evidence = note.get("metadata", {}).get("evidence", [])
                evidence_bonus = min(0.06, 0.015 * len(evidence)) if isinstance(evidence, list) else 0.0
                risk_penalty = 0.08 if context.risk == "high" and not evidence else 0.0
                gain = score + 0.16 * novelty + type_bonus + evidence_bonus - risk_penalty - (token_cost / max(remaining, 1)) * 0.12
                if gain > best_gain:
                    best_index, best_gain, best_payload = idx, gain, (layer, text, token_cost, redundancy)
            if best_index < 0 or best_payload is None:
                break
            score, note, reasons = pool.pop(best_index)
            layer, text, token_cost, redundancy = best_payload
            marginal_per_token = best_gain / max(token_cost, 1)
            if items and marginal_per_token < 0.0035:
                reason = f"set marginal utility {marginal_per_token:.4f} below stop threshold"
                excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                decisions.append({"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]})
                continue
            metadata = note.get("metadata", {})
            evidence = metadata.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]
            route_sources = note.get("route_sources", [])
            if isinstance(route_sources, str):
                route_sources = [route_sources]
            item = RetrievalItem(
                id=note["declared_id"] or note["id"], path=note["path"], title=note["title"], layer=layer,
                text=text.strip(), score=score, tokens=token_cost,
                reasons=reasons + [f"set allocation novelty={1-redundancy:.2f}", f"selected L{layer} within remaining budget"],
                provenance=metadata.get("provenance", []), validation=str(metadata.get("validation", note.get("freshness", "unknown"))),
                evidence=[str(value) for value in evidence], uncertainty=round(max(0.0, 1.0 - float(note.get("confidence", 0.5))), 3),
                route_sources=[str(value) for value in route_sources],
            )
            items.append(item)
            covered_types.add(str(note.get("type", "")))
            remaining -= token_cost
            decisions.append({"id": note["id"], "selected": True, "score": score, "reasons": item.reasons})
        return items, remaining

    def context(self, budget: int | None = None, agent: str = "generic") -> RetrievalManifest:
        git = inspect_git(self.config.repo or Path.cwd())
        changed = ", ".join(git.changed_paths[:10]) or "current repository"
        task = f"Orient {agent} to {Path(git.root).name if git.root else 'the project'}; active paths: {changed}"
        return self.retrieve(task, budget=budget, paths=git.changed_paths[:20], agent=agent)

    @staticmethod
    def _select_layer(note: dict[str, Any], score: float, depth: str | int) -> tuple[int, str]:
        if depth == "auto":
            preferred = 2 if score >= 0.7 else 1 if score >= 0.4 else 0
        else:
            preferred = max(0, min(3, int(depth)))
        for layer in range(preferred, -1, -1):
            text = str(note.get(f"l{layer}") or "").strip()
            if text:
                return layer, text
        return 1, str(note.get("summary") or note.get("title") or "")
