from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .applicability import requested_time
from .calibration import RankPolicy
from .code_graph import RepositoryCodeGraph
from .compiler import compile_task_view
from .config import KBConfig
from .context_state import ContextStateStore
from .evidence import EvidenceStore
from .git_context import inspect_git
from .index import KnowledgeIndex
from .models import RetrievalItem, RetrievalManifest, TaskContext
from .observability import TraceRecorder
from .query_plan import build_query_plan
from .read_policy import conflicting_claim_ids, memory_read_gate
from .scoring import classify_risk, classify_task, feature_vector, score_note
from .security import reject_secrets
from .semantic import LocalEmbeddingBackend
from .sufficiency import assess_sufficiency
from .tokenizer import TOKENIZERS
from .util import atomic_write, jaccard, sha256_text, stable_json, terms

_TEMPORAL = re.compile(
    r"\b(previous|old|older|before|after|as of|version|release|branch|commit|historical|then)\b", re.I
)
_EXACTISH = re.compile(
    r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+|\b[A-Z][A-Za-z0-9_]{2,}\b|\b[a-z_][a-z0-9_]*\([^)]*\)|\b\w+\.\w+\b)"
)


def reciprocal_rank_fusion(result_sets: dict[str, list[dict[str, Any]]], k: int = 60) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, rows in result_sets.items():
        seen = set()
        for rank, row in enumerate(rows, 1):
            identity = str(row["id"])
            if identity in seen:
                continue
            seen.add(identity)
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
        self.code_graph = (
            RepositoryCodeGraph(config.repo, config.runtime_dir / "code-graph.json") if config.repo else None
        )
        self.rank_policy = RankPolicy.load(config)

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def build_context(
        self, task: str, requested_paths: list[str] | None = None, agent: str = "generic", session: str = ""
    ) -> TaskContext:
        git = inspect_git(self.config.repo or Path.cwd())
        repo_name = Path(git.root).name if git.root else (self.config.repo.name if self.config.repo else "")
        paths = requested_paths or []
        module = Path(paths[0]).parts[0] if paths and Path(paths[0]).parts else ""
        active_paths = paths + git.changed_paths
        symbols = self.code_graph.symbols_for_paths(active_paths[:20]) if self.code_graph and active_paths else []
        return TaskContext(
            task=task,
            cwd=str(Path.cwd()),
            repo=repo_name,
            repository_id=git.repository_id,
            project=repo_name,
            module=module,
            branch=git.branch,
            head=git.head,
            merge_base=git.merge_base,
            changed_paths=git.changed_paths,
            requested_paths=paths,
            changed_symbols=symbols[:40],
            agent=agent,
            session=session,
            task_types=classify_task(task),
            risk=classify_risk(task),
        )

    def choose_route(self, context: TaskContext) -> str:
        stripped = context.task.strip()
        if (not terms(stripped) or stripped.lower() in {"hello", "hi", "thanks"}) and not context.requested_paths:
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
            plan = build_query_plan(context)
            sets["exact"] = self.index.search_exact(context.task, min(40, self.config.max_candidates))
            for identifier in plan.identifiers:
                sets["exact"].extend(self.index.search_exact(identifier, 12))
            for path in context.requested_paths[:8]:
                sets.setdefault("path", []).extend(self.index.search_exact(path, 12))
        symbol_hint = " ".join(context.changed_symbols[:8])
        lexical_query = f"{context.task} {symbol_hint}".strip()
        sets["lexical"] = self.index.search_lexical(lexical_query, self.config.max_candidates)
        if route in {"hybrid", "temporal"}:
            # Cheap semantic proxy remains available without model dependencies.
            expansion = " ".join(context.task_types[:4])
            if expansion:
                sets["expanded"] = self.index.search_lexical(
                    f"{context.task} {expansion}", self.config.max_candidates // 2
                )
            if "dense" in self.config.retrieval_routes and self.embedding.available:
                sets["dense"] = self.embedding.search(self.index, context.task, min(80, self.config.max_candidates))
        return sets

    def retrieve(
        self,
        task: str,
        budget: int | None = None,
        depth: str | int = "auto",
        paths: list[str] | None = None,
        agent: str = "generic",
        session: str = "",
        include_uncertain: bool = False,
        route_override: str | None = None,
        *,
        epoch: str = "",
        at: str = "",
        versions: dict[str, str] | None = None,
        record: bool = True,
    ) -> RetrievalManifest:
        reject_secrets(task)
        budget = self.config.default_budget if budget is None else int(budget)
        if budget < 80:
            raise ValueError("budget must be at least 80; it is never silently increased")
        self.index.index_vault()
        context = self.build_context(task, paths, agent, session)
        plan = build_query_plan(context)
        context.requested_paths = plan.paths
        context.at = at or requested_time(task)
        context.versions = dict(versions or {})
        route = route_override or self.choose_route(context)
        if route not in {"none", "exact+lexical", "lexical", "hybrid", "temporal"}:
            raise ValueError("unsupported retrieval route")
        trace_id = self.traces.new_trace_id()
        retrieval_id = uuid.uuid4().hex[:16]
        with self.traces.span(
            trace_id, "retrieval", retrieval_id=retrieval_id, route=route, budget=budget, task_hash=context.task_hash
        ):
            notes = self.index.all_notes()
            counts = Counter(note["declared_id"] for note in notes if note["declared_id"])
            eligible = []
            gate_exclusions = []
            evidence_store = EvidenceStore(self.config)
            for note in notes:
                allowed, reason = memory_read_gate(
                    note,
                    context,
                    allow_untrusted=include_uncertain or self.config.allow_untrusted,
                    allow_cross_repo=self.config.allow_cross_repo,
                    repo_path=self.config.repo,
                )
                if counts.get(note["declared_id"], 0) > 1:
                    allowed, reason = False, "duplicate canonical identity"
                evidence = note.get("metadata", {}).get("evidence", [])
                if (
                    allowed
                    and evidence
                    and (
                        not isinstance(evidence, list)
                        or not all(evidence_store.verify(str(identity)) for identity in evidence)
                    )
                ):
                    allowed, reason = False, "evidence missing or invalid"
                if allowed:
                    eligible.append(note["id"])
                else:
                    gate_exclusions.append({"id": note["id"], "reason": reason})
            eligible_set = set(eligible)
            conflicts = conflicting_claim_ids([note for note in notes if note["id"] in eligible_set])
            eligible = [identity for identity in eligible if identity not in conflicts]
            gate_exclusions.extend(
                {"id": identity, "reason": "unresolved contradiction"} for identity in sorted(conflicts)
            )
            try:
                self.index.set_eligible(eligible)
                result_sets = self._candidate_sets(context, route)
                fused = reciprocal_rank_fusion(result_sets, self.config.rrf_k)
                seed_ids = [row["id"] for row in fused[:3]]
                if seed_ids and route in {"hybrid", "temporal", "exact+lexical"}:
                    result_sets["graph"] = self.index.neighbors(seed_ids, limit=24)
                    fused = reciprocal_rank_fusion(result_sets, self.config.rrf_k)
            finally:
                self.index.set_eligible(None)
            scored: list[tuple[float, dict[str, Any], list[str]]] = []
            excluded: list[dict[str, Any]] = list(gate_exclusions)
            decisions: list[dict[str, Any]] = []
            allow_untrusted = include_uncertain or self.config.allow_untrusted
            for note in fused:
                score, reasons, gate = score_note(
                    note,
                    context,
                    allow_untrusted,
                    self.config.allow_cross_repo,
                    self.config.repo,
                    self.rank_policy.weights,
                    self.rank_policy.intercept,
                    calibrated=self.rank_policy.version == "rank-v3-learned",
                )
                if gate:
                    excluded.append({"id": note["id"], "path": note["path"], "reason": gate, "score": 0.0})
                    decisions.append({"id": note["id"], "selected": False, "score": 0.0, "reasons": [gate]})
                elif score < self.config.minimum_score:
                    reason = f"score {score:.3f} below minimum {self.config.minimum_score:.3f}"
                    excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                    decisions.append(
                        {"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]}
                    )
                else:
                    scored.append((score, note, reasons))
            scored.sort(key=lambda value: (-value[0], value[1]["token_cost"], value[1]["id"]))
            reserved = min(88, max(32, budget // 12))
            remaining = budget - reserved
            items, remaining = self._allocate_set(scored, remaining, depth, context, decisions, excluded, agent)
            source_notes = {note["id"]: note for _, note, _ in scored}
            for item in items:
                note = source_notes[item.id]
                item.revision = sha256_text(stable_json([note["source_hash"], agent, item.layer, item.text]))

            def sufficiency():
                selected = [dict(source_notes[item.id], delivered_text=item.text) for item in items]
                return assess_sufficiency(context, selected)

            state, missing = sufficiency()
            if route == "none":
                state, missing = "no_retrieval_needed", []
            elif not items and any("contradiction" in str(row["reason"]) for row in excluded):
                state = "conflicting_evidence"
            manifest = RetrievalManifest(
                task=task,
                budget=budget,
                used_tokens=0,
                items=items,
                excluded=excluded[:80],
                context=context,
                retrieval_id=retrieval_id,
                route=route,
                state=state,
                trace_id=trace_id,
                missing_evidence=missing,
                token_count_exact=TOKENIZERS.count("", agent).exact,
            )

            # Bound both emitted representations, never the diagnostic manifest.
            def delivered_cost():
                return max(
                    TOKENIZERS.count(manifest.to_markdown(), agent).tokens,
                    TOKENIZERS.count(stable_json(manifest.to_agent_dict()), agent).tokens,
                )

            while items and delivered_cost() > budget:
                removed = items.pop()
                excluded.append({"id": removed.id, "reason": "final payload budget"})
                manifest.state, manifest.missing_evidence = sufficiency()
            if delivered_cost() > budget:
                manifest.delivery = "Insufficient budget for context."
            manifest.used_tokens = delivered_cost()
            if manifest.used_tokens > budget:
                raise ValueError("budget cannot fit the minimum response with this tokenizer")
            delivered_items = list(items)
            inventory = ContextStateStore(self.config.runtime_dir / "context-state")
            previous = inventory.get(session) if session and epoch else None
            if previous and previous.epoch == epoch:
                manifest.items = [item for item in items if previous.revisions.get(item.id) != item.revision]
                if delivered_items and not manifest.items:
                    manifest.state, manifest.missing_evidence = "unchanged_context", []
                manifest.used_tokens = delivered_cost()
            # Trace data is not injected. A receipt records the selected representation
            # and source revision for acknowledgement and action-time checks.
            if record:
                receipt = {
                    "session": session,
                    "epoch": epoch,
                    "repository_id": context.repository_id,
                    "revisions": {item.id: item.revision for item in delivered_items},
                    "source_revisions": {item.id: source_notes[item.id]["source_hash"] for item in delivered_items},
                    "manifest": manifest.to_dict(),
                }
                atomic_write(self.config.runtime_dir / "receipts" / f"{retrieval_id}.json", stable_json(receipt))
                selected_ids = {item.id for item in manifest.items}
                for _, note, _ in scored:
                    self.index.record_rank_example(
                        retrieval_id,
                        note["id"],
                        feature_vector(note, context, self.config.repo),
                        note["id"] in selected_ids,
                    )
            else:
                return manifest
            for decision in decisions:
                decision["selected"] = decision["id"] in selected_ids
            self.index.record_decisions(retrieval_id, decisions)
            self.index.record_usage(retrieval_id, context.task_hash, [item.to_dict() for item in manifest.items])
            self.index.events.emit(
                "retrieve.completed",
                retrieval_id=retrieval_id,
                trace_id=trace_id,
                task_hash=context.task_hash,
                route=route,
                state=state,
                budget=budget,
                used_tokens=manifest.used_tokens,
                selected=[item.id for item in items],
                excluded=len(excluded),
            )
            return manifest

    def _allocate_set(
        self,
        scored: list[tuple[float, dict[str, Any], list[str]]],
        remaining: int,
        depth: str | int,
        context: TaskContext,
        decisions: list[dict[str, Any]],
        excluded: list[dict[str, Any]],
        agent: str,
    ) -> tuple[list[RetrievalItem], int]:
        items: list[RetrievalItem] = []
        covered_types: set[str] = set()
        pool = list(scored)
        while pool and remaining >= 20:
            best_index = -1
            best_gain = -1.0
            best_payload: tuple[int, str, int, float] | None = None
            for idx, (score, note, _) in enumerate(pool):
                preferred, _ = self._select_layer(note, score, depth)
                if depth == "auto" and build_query_plan(context).intent in {"debug", "procedure"}:
                    preferred = max(preferred, 2)
                layer, text = compile_task_view(note, context.task, preferred, max(0, remaining - 14), agent)
                if not text:
                    continue
                token_cost = TOKENIZERS.count(text, agent).tokens + 14
                if token_cost > remaining:
                    continue
                redundancy = max((jaccard(text, item.text) for item in items), default=0.0)
                novelty = 1.0 - redundancy
                type_bonus = (
                    0.09 if note.get("type") in context.task_types and note.get("type") not in covered_types else 0.0
                )
                evidence = note.get("metadata", {}).get("evidence", [])
                evidence_bonus = min(0.06, 0.015 * len(evidence)) if isinstance(evidence, list) else 0.0
                risk_penalty = 0.08 if context.risk == "high" and not evidence else 0.0
                gain = (
                    score
                    + 0.16 * novelty
                    + type_bonus
                    + evidence_bonus
                    - risk_penalty
                    - (token_cost / max(remaining, 1)) * 0.12
                )
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
                id=note["declared_id"] or note["id"],
                path=note["path"],
                title=note["title"],
                layer=layer,
                text=text.strip(),
                score=score,
                tokens=token_cost,
                reasons=reasons
                + [f"set allocation novelty={1 - redundancy:.2f}", f"selected L{layer} within remaining budget"],
                provenance=metadata.get("provenance", []),
                validation=str(metadata.get("validation", note.get("freshness", "unknown"))),
                evidence=[str(value) for value in evidence],
                uncertainty=round(max(0.0, 1.0 - float(note.get("confidence", 0.5))), 3),
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
        preferred = (2 if score >= 0.7 else 1 if score >= 0.4 else 0) if depth == "auto" else max(0, min(3, int(depth)))
        for layer in range(preferred, -1, -1):
            text = str(note.get(f"l{layer}") or "").strip()
            if text:
                return layer, text
        return 1, str(note.get("summary") or note.get("title") or "")
