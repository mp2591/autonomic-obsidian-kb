from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from .config import KBConfig
from .dashboard import dashboard_data
from .evidence import EvidenceStore
from .index import KnowledgeIndex
from .learning import Learner, LearningCandidate
from .leases import LeaseStore
from .retrieval import Retriever
from .telemetry import TelemetryStore
from .validation import Validator

PROTOCOL_VERSION = "2025-11-25"


@dataclass
class MCPServer:
    config: KBConfig

    def tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "kb_retrieve", "description": "Return minimal scope/trust/validity-safe knowledge for a task under a token budget.",
             "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
             "inputSchema": {"type": "object", "required": ["task"], "properties": {
                 "task": {"type": "string"}, "budget": {"type": "integer", "minimum": 80},
                 "paths": {"type": "array", "items": {"type": "string"}}, "route": {"type": "string"}}}},
            {"name": "kb_context", "description": "Return compact repository orientation.",
             "annotations": {"readOnlyHint": True, "idempotentHint": True},
             "inputSchema": {"type": "object", "properties": {"budget": {"type": "integer"}}}},
            {"name": "kb_remember", "description": "Submit a durable-memory candidate through evidence, promotion, and security gates.",
             "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
             "inputSchema": {"type": "object", "required": ["title", "summary"], "properties": {
                 "title": {"type": "string"}, "summary": {"type": "string"}, "detail": {"type": "string"},
                 "type": {"type": "string"}, "scope": {"type": "string"}, "confidence": {"type": "number"},
                 "applies_to": {"type": "array", "items": {"type": "string"}}}}},
            {"name": "kb_feedback", "description": "Record outcome feedback for a retrieved memory.",
             "annotations": {"readOnlyHint": False, "destructiveHint": False},
             "inputSchema": {"type": "object", "required": ["retrieval_id", "memory_id", "kind"], "properties": {
                 "retrieval_id": {"type": "string"}, "memory_id": {"type": "string"}, "kind": {"type": "string"},
                 "notes": {"type": "string"}}}},
            {"name": "kb_evidence_get", "description": "Read and digest-verify one evidence object.",
             "annotations": {"readOnlyHint": True, "idempotentHint": True},
             "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
            {"name": "kb_validate", "description": "Run schema, evidence, provenance, temporal, safety, and executable validators.",
             "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}},
            {"name": "kb_why", "description": "Explain a memory and recent retrieval decisions.",
             "annotations": {"readOnlyHint": True, "idempotentHint": True},
             "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
            {"name": "kb_lease_acquire", "description": "Acquire a short-lived task lease to avoid duplicate investigations.",
             "annotations": {"readOnlyHint": False, "destructiveHint": False},
             "inputSchema": {"type": "object", "required": ["task", "agent"], "properties": {
                 "task": {"type": "string"}, "agent": {"type": "string"}, "ttl_minutes": {"type": "integer"}}}},
            {"name": "kb_status", "description": "Return dashboard, index, validation, and outcome status.",
             "annotations": {"readOnlyHint": True, "idempotentHint": True},
             "inputSchema": {"type": "object", "properties": {}}},
        ]

    def resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": "kb://dashboard", "name": "Knowledge-base dashboard", "mimeType": "application/json"},
            {"uri": "kb://memories", "name": "Active memory catalog", "mimeType": "application/json"},
            {"uri": "kb://evidence", "name": "Evidence catalog", "mimeType": "application/json"},
        ]

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method"); request_id = request.get("id")
        if method == "notifications/initialized": return None
        if method == "initialize":
            requested = str(request.get("params", {}).get("protocolVersion", ""))
            negotiated = requested if requested in {"2025-11-25", "2025-06-18"} else "2025-06-18"
            return self._result(request_id, {"protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": {"name": "autonomic-obsidian-kb", "version": "0.2.0"}})
        if method == "tools/list": return self._result(request_id, {"tools": self.tools()})
        if method == "resources/list": return self._result(request_id, {"resources": self.resources()})
        if method == "resources/read":
            try: return self._result(request_id, {"contents": [{"uri": request["params"]["uri"], "mimeType": "application/json",
                                                                  "text": json.dumps(self.read_resource(str(request["params"]["uri"])), indent=2)}]})
            except Exception as error: return self._error(request_id, -32000, str(error))
        if method == "tools/call":
            params = request.get("params", {})
            try:
                value = self.call_tool(str(params.get("name", "")), dict(params.get("arguments", {})))
                return self._result(request_id, {"content": [{"type": "text", "text": json.dumps(value, indent=2)}],
                                                 "structuredContent": value if isinstance(value, dict) else {"value": value}})
            except Exception as error: return self._error(request_id, -32000, str(error))
        if method == "ping": return self._result(request_id, {})
        return self._error(request_id, -32601, f"method not found: {method}")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with KnowledgeIndex(self.config) as index:
            if name == "kb_retrieve":
                return Retriever(self.config, index).retrieve(str(arguments["task"]), budget=arguments.get("budget"),
                    paths=list(arguments.get("paths", [])), agent="mcp", route_override=arguments.get("route")).to_dict()
            if name == "kb_context": return Retriever(self.config, index).context(arguments.get("budget"), agent="mcp").to_dict()
            if name == "kb_remember": return Learner(self.config, index).remember(LearningCandidate.from_dict(arguments))
            if name == "kb_feedback": return TelemetryStore(self.config).feedback(str(arguments["retrieval_id"]), str(arguments["memory_id"]),
                                                                                     str(arguments["kind"]), actor="mcp", notes=str(arguments.get("notes", ""))).to_dict()
            if name == "kb_evidence_get":
                record = EvidenceStore(self.config).get(str(arguments["id"])); return record.to_dict() if record else {"error": "not-found"}
            if name == "kb_validate": return Validator(self.config, index).validate().to_dict()
            if name == "kb_why": index.index_vault(); return index.why(str(arguments["id"]))
            if name == "kb_lease_acquire": return LeaseStore(self.config).acquire(str(arguments["task"]), str(arguments["agent"]),
                                                                                     ttl_minutes=int(arguments.get("ttl_minutes", 30))).to_dict()
            if name == "kb_status": return dashboard_data(self.config)
        raise ValueError(f"unknown tool: {name}")

    def read_resource(self, uri: str) -> Any:
        if uri == "kb://dashboard": return dashboard_data(self.config)
        if uri == "kb://evidence": return {"ids": EvidenceStore(self.config).list_ids()}
        if uri == "kb://memories":
            with KnowledgeIndex(self.config) as index:
                index.index_vault(); return {"memories": [{"id": n["declared_id"] or n["id"], "title": n["title"], "path": n["path"],
                                                             "type": n["type"], "scope": n["scope"], "status": n["status"]}
                                                            for n in index.all_notes({"active", "stale", "conflicted"})]}
        if uri.startswith("kb://memory/"):
            memory_id = uri[len("kb://memory/"):]
            with KnowledgeIndex(self.config) as index: index.index_vault(); return index.get(memory_id) or {"error": "not-found"}
        if uri.startswith("kb://evidence/"):
            evidence_id = uri[len("kb://evidence/"):]
            record = EvidenceStore(self.config).get(evidence_id); return record.to_dict() if record else {"error": "not-found"}
        raise ValueError(f"unknown resource: {uri}")

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]: return {"jsonrpc": "2.0", "id": request_id, "result": result}
    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]: return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(config: KBConfig) -> int:
    server = MCPServer(config)
    for line in sys.stdin:
        if not line.strip(): continue
        try:
            request = json.loads(line); response = server.handle(request)
            if response is not None: sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n"); sys.stdout.flush()
        except json.JSONDecodeError as error:
            sys.stdout.write(json.dumps(MCPServer._error(None, -32700, str(error)), separators=(",", ":")) + "\n"); sys.stdout.flush()
    return 0
