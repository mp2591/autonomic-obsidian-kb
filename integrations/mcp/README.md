# MCP integration

The repository includes a dependency-free stdio JSON-RPC server exposing `kb_retrieve`, `kb_context`, `kb_remember`, `kb_why`, and `kb_status`.

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

The server is local stdio, not a network listener. Writes still pass promotion and security gates. Client configuration formats differ by agent version; use the current agent documentation for the surrounding config file.
