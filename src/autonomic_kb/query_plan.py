from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import TaskContext
from .util import terms

_PATH = re.compile(r"(?:^|\s)([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)")
_SYMBOL = re.compile(r"\b(?:[A-Z][A-Za-z0-9_]{2,}|[a-z_][a-z0-9_]{2,}\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")
_ERROR = re.compile(r"(?:error|exception|failed|failure|traceback|locked|timeout)[:\s]+([^\n]{3,160})", re.I)
_TEMPORAL = re.compile(r"\b(as of|before|after|previous|old|version|release|branch|commit|historical)\b", re.I)


@dataclass(slots=True)
class QueryPlan:
    intent: str
    task_types: list[str]
    identifiers: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    error_signatures: list[str] = field(default_factory=list)
    temporal: bool = False
    subgoals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


def build_query_plan(context: TaskContext) -> QueryPlan:
    task = context.task
    identifiers = sorted(set(match.group(0) for match in _SYMBOL.finditer(task)))[:16]
    paths = sorted(set(context.requested_paths + [m.group(1) for m in _PATH.finditer(task)]))[:20]
    errors = [m.group(1).strip() for m in _ERROR.finditer(task)][:8]
    types = context.task_types
    if "known-failure" in types or "solution" in types: intent = "debug"
    elif "command" in types or "workflow" in types: intent = "procedure"
    elif "architecture" in types or "decision" in types: intent = "explain"
    elif paths or identifiers: intent = "locate"
    else: intent = "retrieve"
    subgoals = []
    if intent == "debug": subgoals = ["failure signature", "cause", "validated fix", "regression check"]
    elif intent == "procedure": subgoals = ["preconditions", "command or steps", "postcondition"]
    elif intent == "explain": subgoals = ["decision", "rationale", "constraints"]
    return QueryPlan(intent, types, identifiers, paths, errors, bool(_TEMPORAL.search(task)), subgoals)
