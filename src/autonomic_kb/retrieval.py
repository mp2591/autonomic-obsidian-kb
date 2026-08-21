from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import KBConfig
from .git_context import inspect_git
from .index import KnowledgeIndex
from .models import RetrievalItem, RetrievalManifest, TaskContext
from .scoring import classify_task, score_note
from .util import estimate_tokens


class Retriever:
    def __init__(self, config: KBConfig, index: KnowledgeIndex | None = None):
        self.config = config
        self.index = index or KnowledgeIndex(config)
        self._owns_index = index is None

    def close(self) -> None:
        if self._owns_index:
            self.index.close()

    def build_context(
        self,
        task: str,
        requested_paths: list[str] | None = None,
        agent: str = "generic",
        session: str = "",
    ) -> TaskContext:
        git = inspect_git(self.config.repo or Path.cwd())
        repo_name = Path(git.root).name if git.root else (self.config.repo.name if self.config.repo else "")
        paths = requested_paths or []
        module = ""
        if paths:
            first = Path(paths[0])
            module = first.parts[0] if first.parts else ""
        return TaskContext(
            task=task,
            cwd=str(Path.cwd()),
            repo=repo_name,
            project=repo_name,
            module=module,
            branch=git.branch,
            changed_paths=git.changed_paths,
            requested_paths=paths,
            agent=agent,
            session=session,
            task_types=classify_task(task),
        )

    def retrieve(
        self,
        task: str,
        budget: int | None = None,
        depth: str | int = "auto",
        paths: list[str] | None = None,
        agent: str = "generic",
        session: str = "",
        include_uncertain: bool = False,
    ) -> RetrievalManifest:
        self.index.index_vault()
        budget = max(80, int(budget or self.config.default_budget))
        context = self.build_context(task, paths, agent, session)
        candidates = self.index.search(task, self.config.max_candidates)
        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        excluded: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        allow_untrusted = include_uncertain or self.config.allow_untrusted
        for note in candidates:
            score, reasons, gate = score_note(
                note, context, allow_untrusted=allow_untrusted,
                allow_cross_repo=self.config.allow_cross_repo,
            )
            if gate:
                excluded.append({"id": note["id"], "path": note["path"], "reason": gate, "score": 0.0})
                decisions.append({"id": note["id"], "selected": False, "score": 0.0, "reasons": [gate]})
                continue
            if score < self.config.minimum_score:
                reason = f"score {score:.3f} below minimum {self.config.minimum_score:.3f}"
                excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                decisions.append({"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]})
                continue
            scored.append((score, note, reasons))
        scored.sort(key=lambda value: (-value[0], value[1]["token_cost"], value[1]["id"]))

        # One-hop graph expansion is deliberately bounded and only starts from high-signal seeds.
        seed_ids = [note["id"] for score, note, _ in scored[:3] if score >= 0.58]
        existing_ids = {note["id"] for _, note, _ in scored}
        for neighbor in self.index.neighbors(seed_ids, limit=20):
            if neighbor["id"] in existing_ids:
                continue
            score, reasons, gate = score_note(
                neighbor, context, allow_untrusted=allow_untrusted,
                allow_cross_repo=self.config.allow_cross_repo,
            )
            if not gate and score >= self.config.minimum_score + 0.05:
                scored.append((score, neighbor, reasons))
                existing_ids.add(neighbor["id"])
        scored.sort(key=lambda value: (-value[0], value[1]["token_cost"], value[1]["id"]))

        retrieval_id = uuid.uuid4().hex[:16]
        reserved = min(96, max(40, budget // 10))
        remaining = budget - reserved
        items: list[RetrievalItem] = []
        for score, note, reasons in scored:
            layer, text = self._select_layer(note, score, depth)
            token_cost = estimate_tokens(text) + 16
            if token_cost > remaining:
                lower = layer
                while lower > 0 and token_cost > remaining:
                    lower -= 1
                    candidate_text = str(note.get(f"l{lower}") or note.get("summary") or note.get("title"))
                    if candidate_text:
                        layer, text = lower, candidate_text
                        token_cost = estimate_tokens(text) + 16
                if token_cost > remaining:
                    reason = f"needs {token_cost} tokens with {remaining} remaining"
                    excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                    decisions.append({"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]})
                    continue
            marginal = score / max(token_cost, 1) * 100
            if items and marginal < 0.055:
                reason = f"marginal value {marginal:.3f} below token-cost stop threshold"
                excluded.append({"id": note["id"], "path": note["path"], "reason": reason, "score": score})
                decisions.append({"id": note["id"], "selected": False, "score": score, "reasons": reasons + [reason]})
                continue
            metadata = note.get("metadata", {})
            item = RetrievalItem(
                id=note["declared_id"] or note["id"],
                path=note["path"],
                title=note["title"],
                layer=layer,
                text=text.strip(),
                score=score,
                tokens=token_cost,
                reasons=reasons + [f"selected L{layer} within remaining budget"],
                provenance=metadata.get("provenance", []),
                validation=str(metadata.get("validation", note.get("freshness", "unknown"))),
            )
            items.append(item)
            remaining -= token_cost
            decisions.append({"id": note["id"], "selected": True, "score": score, "reasons": item.reasons})
            if remaining < 24:
                break
        used = budget - remaining
        manifest = RetrievalManifest(
            task=task,
            budget=budget,
            used_tokens=min(budget, used),
            items=items,
            excluded=excluded[:50],
            context=context,
            retrieval_id=retrieval_id,
        )
        self.index.record_decisions(retrieval_id, decisions)
        self.index.record_usage(retrieval_id, context.task_hash, [item.to_dict() for item in items])
        self.index.events.emit(
            "retrieve.completed", retrieval_id=retrieval_id, task_hash=context.task_hash,
            budget=budget, used_tokens=manifest.used_tokens,
            selected=[item.id for item in items], excluded=len(excluded),
        )
        return manifest

    def context(self, budget: int | None = None, agent: str = "generic") -> RetrievalManifest:
        git = inspect_git(self.config.repo or Path.cwd())
        changed = ", ".join(git.changed_paths[:10]) or "current repository"
        task = f"Orient {agent} to {Path(git.root).name if git.root else 'the project'}; active paths: {changed}"
        return self.retrieve(task, budget=budget, paths=git.changed_paths[:20], agent=agent)

    @staticmethod
    def _select_layer(note: dict[str, Any], score: float, depth: str | int) -> tuple[int, str]:
        if depth == "auto":
            preferred = 2 if score >= 0.68 else 1 if score >= 0.42 else 0
        else:
            preferred = max(0, min(3, int(depth)))
        for layer in range(preferred, -1, -1):
            text = str(note.get(f"l{layer}") or "").strip()
            if text:
                return layer, text
        return 1, str(note.get("summary") or note.get("title") or "")
