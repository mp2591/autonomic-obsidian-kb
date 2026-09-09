from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from .config import KBConfig
from .context_state import ContextStateStore
from .evidence import EvidenceStore
from .index import KnowledgeIndex
from .learning import Learner, LearningCandidate
from .leases import LeaseStore
from .models import VALID_SCOPES, VALID_TYPES
from .read_policy import conflicting_claim_ids, memory_read_gate
from .retrieval import Retriever
from .telemetry import TelemetryStore
from .util import stable_json
from .validation import Validator

PROTOCOL_VERSION = "2025-11-25"


@dataclass
class MCPServer:
    config: KBConfig

    def tools(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "kb_retrieve",
                "description": "Return minimal scope/trust/validity-safe knowledge for a task under a token budget.",
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
                "inputSchema": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {"type": "string"},
                        "budget": {"type": "integer", "minimum": 80},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "route": {"type": "string"},
                    },
                },
            },
            {
                "name": "kb_context",
                "description": "Return compact repository orientation.",
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
                "inputSchema": {"type": "object", "properties": {"budget": {"type": "integer"}}},
            },
            {
                "name": "kb_remember",
                "description": "Submit a durable-memory candidate through evidence, promotion, and security gates.",
                "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
                "inputSchema": {
                    "type": "object",
                    "required": ["title", "summary"],
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "detail": {"type": "string"},
                        "type": {"type": "string"},
                        "scope": {"type": "string"},
                        "confidence": {"type": "number"},
                        "applies_to": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {
                "name": "kb_feedback",
                "description": "Record outcome feedback for a retrieved memory.",
                "annotations": {"readOnlyHint": False, "destructiveHint": False},
                "inputSchema": {
                    "type": "object",
                    "required": ["retrieval_id", "memory_id", "kind"],
                    "properties": {
                        "retrieval_id": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "kind": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                },
            },
            {
                "name": "kb_evidence_get",
                "description": "Read and digest-verify one evidence object.",
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
                "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
            },
            {
                "name": "kb_validate",
                "description": "Run schema, evidence, provenance, temporal, and non-executable source validators.",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "kb_why",
                "description": "Explain a memory and recent retrieval decisions.",
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
                "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
            },
            {
                "name": "kb_lease_acquire",
                "description": "Acquire a short-lived task lease to avoid duplicate investigations.",
                "annotations": {"readOnlyHint": False, "destructiveHint": False},
                "inputSchema": {
                    "type": "object",
                    "required": ["task", "agent"],
                    "properties": {
                        "task": {"type": "string"},
                        "agent": {"type": "string"},
                        "ttl_minutes": {"type": "integer"},
                    },
                },
            },
            {
                "name": "kb_status",
                "description": "Return dashboard, index, validation, and outcome status.",
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        candidate_schema = tools[2]["inputSchema"]["properties"]
        candidate_schema["type"]["enum"] = sorted(VALID_TYPES)
        candidate_schema["scope"]["enum"] = sorted(VALID_SCOPES)
        candidate_schema["confidence"].update(minimum=0, maximum=1)
        candidate_schema.update(
            {
                "preconditions": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string", "pattern": "^evidence:sha256:[a-f0-9]{64}$"}},
                "provenance": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path"],
                        "properties": {"path": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                "valid_from": {"type": "string"},
                "valid_to": {"type": "string"},
                "version_package": {"type": "string"},
                "version_range": {"type": "string"},
                "claim_key": {"type": "string"},
                "claim_value": {},
            }
        )
        retrieval_schema = tools[0]["inputSchema"]["properties"]
        retrieval_schema.update(
            {
                "session": {"type": "string", "maxLength": 160},
                "epoch": {"type": "string", "maxLength": 160},
                "profile": {"type": "string", "maxLength": 100},
                "at": {"type": "string", "maxLength": 40},
                "versions": {"type": "object", "additionalProperties": {"type": "string"}},
            }
        )
        retrieval_schema["route"]["enum"] = ["none", "lexical", "exact+lexical", "hybrid", "temporal"]
        retrieval_schema["task"]["maxLength"] = 16000
        retrieval_schema["budget"]["maximum"] = 32000
        tools.extend(
            [
                {
                    "name": "kb_context_ack",
                    "description": "Acknowledge retention of a delivered context receipt.",
                    "annotations": {"readOnlyHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["retrieval_id", "session", "epoch"],
                        "properties": {key: {"type": "string"} for key in ("retrieval_id", "session", "epoch")},
                    },
                },
                {
                    "name": "kb_receipt_check",
                    "description": "Revalidate context before acting; does not authorize actions.",
                    "annotations": {"readOnlyHint": True},
                    "inputSchema": {
                        "type": "object",
                        "required": ["retrieval_id"],
                        "properties": {"retrieval_id": {"type": "string"}},
                    },
                },
                {
                    "name": "kb_lease_release",
                    "description": "Release a task lease owned by this agent.",
                    "annotations": {"readOnlyHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["task", "agent"],
                        "properties": {key: {"type": "string"} for key in ("task", "agent")},
                    },
                },
            ]
        )
        for tool in tools:
            tool["inputSchema"]["additionalProperties"] = False
            if tool["name"] in {"kb_context_ack", "kb_receipt_check"}:
                tool["inputSchema"]["properties"]["versions"] = {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                }

        return tools

    def resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": "kb://dashboard", "name": "Knowledge-base dashboard", "mimeType": "application/json"},
            {"uri": "kb://memories", "name": "Active memory catalog", "mimeType": "application/json"},
            {"uri": "kb://evidence", "name": "Evidence catalog", "mimeType": "application/json"},
        ]

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "invalid JSON-RPC request")
        method = request.get("method")
        request_id = request.get("id")
        if "id" not in request:
            return None
        if method == "initialize":
            requested = str(request.get("params", {}).get("protocolVersion", ""))
            negotiated = requested if requested in {"2025-11-25", "2025-06-18"} else "2025-06-18"
            return self._result(
                request_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": "autonomic-obsidian-kb", "version": "0.3.0"},
                },
            )
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tools()})
        if method == "resources/list":
            return self._result(request_id, {"resources": self.resources()})
        if method == "resources/read":
            try:
                return self._result(
                    request_id,
                    {
                        "contents": [
                            {
                                "uri": request["params"]["uri"],
                                "mimeType": "application/json",
                                "text": json.dumps(self.read_resource(str(request["params"]["uri"])), indent=2),
                            }
                        ]
                    },
                )
            except Exception as error:
                return self._error(request_id, -32000, str(error))
        if method == "tools/call":
            params = request.get("params", {})
            try:
                value = self.call_tool(str(params.get("name", "")), dict(params.get("arguments", {})))
                return self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": stable_json(value)}],
                    },
                )
            except Exception as error:
                return self._error(request_id, -32000, str(error))
        if method == "ping":
            return self._result(request_id, {})
        return self._error(request_id, -32601, f"method not found: {method}")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        schema = next((tool["inputSchema"] for tool in self.tools() if tool["name"] == name), None)
        if schema is None:
            raise ValueError("unknown tool")
        if not Draft202012Validator(schema).is_valid(arguments):
            raise ValueError("tool arguments do not match the declared schema")
        with KnowledgeIndex(self.config) as index:
            if name == "kb_retrieve":
                return (
                    Retriever(self.config, index)
                    .retrieve(
                        str(arguments["task"]),
                        budget=arguments.get("budget"),
                        paths=list(arguments.get("paths", [])),
                        agent=str(arguments.get("profile", "mcp")),
                        route_override=arguments.get("route"),
                        session=arguments.get("session", ""),
                        epoch=arguments.get("epoch", ""),
                        at=arguments.get("at", ""),
                        versions=arguments.get("versions"),
                    )
                    .to_agent_dict()
                )
            if name == "kb_context":
                return Retriever(self.config, index).context(arguments.get("budget"), agent="mcp").to_agent_dict()
            if name == "kb_remember":
                return Learner(self.config, index).remember(LearningCandidate.from_dict(arguments))
            if name == "kb_feedback":
                return (
                    TelemetryStore(self.config)
                    .feedback(
                        str(arguments["retrieval_id"]),
                        str(arguments["memory_id"]),
                        str(arguments["kind"]),
                        actor="mcp",
                        notes=str(arguments.get("notes", "")),
                    )
                    .to_dict()
                )
            if name == "kb_evidence_get":
                return self._read_evidence(str(arguments["id"]), index)
            if name == "kb_validate":
                report = Validator(self.config, index).validate()
                visible_paths = {note["path"] for note in self._visible_notes(index)}
                return {
                    "scanned": len(visible_paths),
                    "issues": [issue.to_dict() for issue in report.issues if issue.path in visible_paths],
                }
            if name == "kb_why":
                index.index_vault()
                note = index.get(str(arguments["id"]))
                if not note or not any(n["id"] == note["id"] for n in self._visible_notes(index)):
                    return {"error": "not-found"}
                return index.why(str(arguments["id"]))
            if name == "kb_lease_acquire":
                return (
                    LeaseStore(self.config)
                    .acquire(
                        str(arguments["task"]),
                        str(arguments["agent"]),
                        ttl_minutes=int(arguments.get("ttl_minutes", 30)),
                    )
                    .to_dict()
                )
            if name == "kb_status":
                return {"visible_memories": len(self._visible_notes(index))}
            if name == "kb_lease_release":
                return {"released": LeaseStore(self.config).release(arguments["task"], arguments["agent"])}
            if name in {"kb_context_ack", "kb_receipt_check"}:
                return self._receipt(name, arguments, index)
        raise ValueError(f"unknown tool: {name}")

    def read_resource(self, uri: str) -> Any:
        if uri == "kb://dashboard":
            return self.call_tool("kb_status", {})
        if uri == "kb://evidence":
            with KnowledgeIndex(self.config) as index:
                ids = {
                    str(identity)
                    for note in self._visible_notes(index)
                    for identity in note.get("metadata", {}).get("evidence", [])
                }
                return {"ids": sorted(ids)[:200]}
        if uri == "kb://memories":
            with KnowledgeIndex(self.config) as index:
                index.index_vault()
                return {
                    "memories": [
                        {
                            "id": n["declared_id"] or n["id"],
                            "title": n["title"],
                            "path": n["path"],
                            "type": n["type"],
                            "scope": n["scope"],
                            "status": n["status"],
                        }
                        for n in self._visible_notes(index)[:200]
                    ]
                }
        if uri.startswith("kb://memory/"):
            memory_id = uri[len("kb://memory/") :]
            with KnowledgeIndex(self.config) as index:
                index.index_vault()
                note = index.get(memory_id)
                return (
                    note
                    if note and any(n["id"] == note["id"] for n in self._visible_notes(index))
                    else {"error": "not-found"}
                )
        if uri.startswith("kb://evidence/"):
            evidence_id = uri[len("kb://evidence/") :]
            with KnowledgeIndex(self.config) as index:
                return self._read_evidence(evidence_id, index)
        raise ValueError(f"unknown resource: {uri}")

    def _visible(self, note: dict[str, Any], index: KnowledgeIndex, context=None) -> bool:
        context = context or Retriever(self.config, index).build_context("inspect memory")
        allowed, _ = memory_read_gate(note, context, repo_path=self.config.repo)
        evidence = note.get("metadata", {}).get("evidence", [])
        return (
            allowed
            and isinstance(evidence, list)
            and all(EvidenceStore(self.config).verify(str(identity)) for identity in evidence)
        )

    def _visible_notes(self, index: KnowledgeIndex, context=None) -> list[dict[str, Any]]:
        index.index_vault()
        context = context or Retriever(self.config, index).build_context("inspect memory")
        all_notes = index.all_notes()
        notes = [note for note in all_notes if self._visible(note, index, context)]
        conflicts = conflicting_claim_ids(notes)
        counts = Counter(note["declared_id"] for note in all_notes if note["declared_id"])
        return [note for note in notes if note["id"] not in conflicts and counts.get(note["declared_id"], 0) <= 1]

    def _read_evidence(self, identity: str, index: KnowledgeIndex) -> dict[str, Any]:
        if not any(identity in note.get("metadata", {}).get("evidence", []) for note in self._visible_notes(index)):
            return {"error": "not-found"}
        record = EvidenceStore(self.config).get(identity)
        context = Retriever(self.config, index).build_context("inspect evidence")
        if not record or (record.repository_id and record.repository_id != context.repository_id):
            return {"error": "not-found"}
        return record.to_dict()

    def _receipt(self, name: str, arguments: dict[str, Any], index: KnowledgeIndex) -> dict[str, Any]:
        identity = arguments["retrieval_id"]
        if not re.fullmatch(r"[a-f0-9]{16}", identity):
            raise ValueError("invalid receipt identity")
        path = self.config.runtime_dir / "receipts" / f"{identity}.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        index.index_vault()
        changed = []
        context = Retriever(self.config, index).build_context("check receipt")
        if row["repository_id"] != context.repository_id:
            raise ValueError("receipt repository mismatch")
        original = row.get("manifest", {}).get("context", {})
        context.requested_paths = list(original.get("requested_paths", []))
        context.session = str(row.get("session", ""))
        # Version-specific action checks need fresh host-supplied versions; never reuse old ones implicitly.
        context.versions = dict(arguments.get("versions", {}))
        visible_ids = {note["id"] for note in self._visible_notes(index, context)}
        for memory_id, digest in row["source_revisions"].items():
            note = index.get(memory_id)
            if not note or note["source_hash"] != digest or memory_id not in visible_ids:
                changed.append(memory_id)
        if name == "kb_receipt_check":
            return {"valid": not changed, "changed": changed, "authorizes_action": False}
        if changed or row["session"] != arguments["session"] or row["epoch"] != arguments["epoch"]:
            raise ValueError("receipt is stale or belongs to a different context")
        store = ContextStateStore(self.config.runtime_dir / "context-state")
        prior = store.get(arguments["session"])
        revisions = prior.revisions if prior and prior.epoch == arguments["epoch"] else {}
        revisions.update(row["revisions"])
        store.acknowledge(arguments["session"], arguments["epoch"], revisions)
        return {"acknowledged": len(row["revisions"])}

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(config: KBConfig) -> int:
    server = MCPServer(config)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as error:
            sys.stdout.write(json.dumps(MCPServer._error(None, -32700, str(error)), separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0
