from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from .config import KBConfig
from .index import KnowledgeIndex
from .learning import Learner, LearningCandidate
from .retrieval import Retriever

PROTOCOL_VERSION = "2025-06-18"


@dataclass
class MCPServer:
    config: KBConfig

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "kb_retrieve",
                "description": "Return minimal, scope-safe knowledge for a task under a token budget.",
                "inputSchema": {
                    "type": "object", "required": ["task"],
                    "properties": {
                        "task": {"type": "string"}, "budget": {"type": "integer", "minimum": 80},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {
                "name": "kb_context",
                "description": "Return compact repository orientation for the current working state.",
                "inputSchema": {"type": "object", "properties": {"budget": {"type": "integer"}}},
            },
            {
                "name": "kb_remember",
                "description": "Submit a durable-memory candidate; promotion and security gates still apply.",
                "inputSchema": {
                    "type": "object", "required": ["title", "summary"],
                    "properties": {
                        "title": {"type": "string"}, "summary": {"type": "string"},
                        "detail": {"type": "string"}, "type": {"type": "string"},
                        "scope": {"type": "string"}, "confidence": {"type": "number"},
                        "applies_to": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {
                "name": "kb_why",
                "description": "Explain a memory and its recent retrieval inclusion/exclusion decisions.",
                "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
            },
            {
                "name": "kb_status",
                "description": "Return index, vault, scope, and usage status.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._result(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "autonomic-obsidian-kb", "version": "0.1.0"},
            })
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tools()})
        if method == "tools/call":
            params = request.get("params", {})
            try:
                value = self.call_tool(str(params.get("name", "")), dict(params.get("arguments", {})))
                return self._result(request_id, {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]})
            except Exception as error:  # MCP boundary must return structured failure.
                return self._error(request_id, -32000, str(error))
        if method == "ping":
            return self._result(request_id, {})
        return self._error(request_id, -32601, f"method not found: {method}")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with KnowledgeIndex(self.config) as index:
            if name == "kb_retrieve":
                manifest = Retriever(self.config, index).retrieve(
                    str(arguments["task"]), budget=arguments.get("budget"),
                    paths=list(arguments.get("paths", [])), agent="mcp",
                )
                return manifest.to_dict()
            if name == "kb_context":
                return Retriever(self.config, index).context(arguments.get("budget"), agent="mcp").to_dict()
            if name == "kb_remember":
                candidate = LearningCandidate.from_dict(arguments)
                return Learner(self.config, index).remember(candidate)
            if name == "kb_why":
                index.index_vault()
                return index.why(str(arguments["id"]))
            if name == "kb_status":
                index.index_vault()
                return index.stats()
        raise ValueError(f"unknown tool: {name}")

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
            response = MCPServer._error(None, -32700, str(error))
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0
