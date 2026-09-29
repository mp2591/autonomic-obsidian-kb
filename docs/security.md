# Local memory security boundary

V3 is designed for a trusted local, single-user host and controlled stdio clients, not a multi-tenant remote service.

Hard read gates cover schema, scope, authorization of privileged instruction records, hostile taint, statuses, temporal/source applicability, evidence integrity, duplicate identities, and conflicting applicable claims. Candidate limits are applied after eligibility. MCP resource, evidence, catalog, and inspection reads use the same policy. Extra MCP candidate fields cannot self-assign authority. An uncertainty option cannot override privileged-instruction authorization or hostile-taint blocks.

Secret scanning happens before durable candidate, episode, evidence, feedback, outcome, and operation writes. It includes metadata, not only visible body text. Scanners have finite coverage; review sensitive content before syncing. KB state paths and source validators must stay inside their declared roots, and external state symlinks are rejected.

**Note-defined command execution is removed.** Validation is not an execution interface. Replay requires explicit operator opt-in, trusted argv, separate snapshots, and an evaluator; it is not an OS sandbox. Host tool permissions must be enforced outside memory. Agent context and receipts explicitly return `authorizes_action: false`.

Hashes bind evidence fields and detect inconsistent identities; they do not authenticate producers or establish factual truth. Authority metadata and evaluation-kind evidence are trusted-host assertions, not signed grants. Cooperating KB writers use a single-writer lock and rollback journals, while external editor conflicts require reconciliation. Prepared journals block reads and writes after an interrupted transaction.

See [the complete implementation and limitations](v3-implementation.md), especially migration of legacy content-only evidence IDs. Do not silently upgrade old evidence to authenticated provenance.
