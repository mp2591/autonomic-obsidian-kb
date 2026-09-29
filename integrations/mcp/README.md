# MCP integration

`kb mcp` runs a local stdio JSON-RPC server (it uses the package's `jsonschema` dependency to validate tool arguments). It exposes `kb_retrieve`, `kb_context`, `kb_remember`, `kb_feedback`, `kb_evidence_get`, `kb_validate`, `kb_why`, `kb_status`, `kb_lease_acquire`, `kb_lease_release`, `kb_context_ack`, and `kb_receipt_check`, plus `kb://` resources for the dashboard, visible memories, and evidence.

Example generic client configuration:

```json
{
  "mcpServers": {
    "autonomic-kb": {
      "command": "kb",
      "args": ["--vault", "/absolute/path/to/vault", "--repo", "/absolute/path/to/repo", "mcp"]
    }
  }
}
```

The server is local stdio, not a network listener. The repository is fixed when the server starts, so pass `--repo` (or set `KB_REPO`) for the project it serves.

Writes still pass promotion and security gates. `kb_remember` cannot assign authority, taint, or value estimates; a candidate without enough evidence lands in the inbox and is not retrieved until a reviewer promotes it with the CLI. Review operations (`kb inbox`, `kb promote`, `kb revalidate`, `kb supersede`) are deliberately not MCP tools.

A tool that fails returns a normal result with `isError: true` and the message as text, so the model can correct its call. Unknown tools and malformed requests return JSON-RPC errors. Client configuration formats differ by agent version; use the current agent documentation for the surrounding config file.
