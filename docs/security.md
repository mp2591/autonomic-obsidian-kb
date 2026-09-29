# Local memory security boundary

V3 is designed for a trusted local, single-user host and controlled stdio clients, not a multi-tenant remote service.

Hard read gates cover schema, scope, authorization of privileged instruction records, hostile taint, statuses, temporal/source applicability, evidence integrity, duplicate identities, and conflicting applicable claims. Candidate limits are applied after eligibility. MCP resource, evidence, catalog, and inspection reads use the same policy. Extra MCP candidate fields cannot self-assign authority. An uncertainty option cannot override privileged-instruction authorization or hostile-taint blocks.

Secret scanning happens before durable candidate, episode, evidence, feedback, outcome, and operation writes. It includes metadata, not only visible body text, and scans every string of a structured value as written, plus each `key: value` pair; scanning a JSON dump let escape sequences hide a secret that started a line, and a quoted key hid a frontmatter credential from the read gate. Scanners have finite coverage; review sensitive content before syncing. KB state paths and source validators must stay inside their declared roots, and external state symlinks are rejected.

**Note-defined command execution is removed.** Validation is not an execution interface. Replay requires explicit operator opt-in, trusted argv, separate snapshots, and an evaluator; it is not an OS sandbox. Host tool permissions must be enforced outside memory. Agent context and receipts explicitly return `authorizes_action: false`.

Hashes bind evidence fields and detect inconsistent identities; they do not authenticate producers or establish factual truth. Authority metadata and evaluation-kind evidence are trusted-host assertions, not signed grants. Cooperating KB writers use a single-writer lock and rollback journals, while external editor conflicts require reconciliation. A transaction snapshots notes and operation records as hard links (copies where linking is unsupported) instead of reading the vault, so an existing note that contains a credential no longer blocks unrelated writes. Rollback restores files the KB replaced and keeps files edited in place by other programs. Snapshot links reference existing files and are removed after the transaction, but a copy fallback or a crash leaves before-state under `.kb-transactions/` until `kb reconcile` resolves it; that journal blocks reads and writes meanwhile.

Healing quarantines unsafe notes and redacts secret values in the vault copy; the unredacted original is kept only in local `.kb/backups/`. `kb init` writes a vault `.gitignore` for local state, and `kb doctor` reports when it is missing. Redaction does not remove a credential from Git history, so rotate it.

CLI options that let a caller vouch for its own candidate (`--force`, elevated `--authority`/`--taint`, `--authorize-instruction`) require `--reason` and are recorded with the actor in the ledger; MCP cannot set them, and `[security] allow_privileged_remember = false` removes them from the CLI.

Repository scope is a hard read gate: a repository-, project-, module- or branch-scoped memory without a repository identity is excluded wherever a repository is active, and the scoping repository comes from `--repo`, `KB_REPO`, or the working directory, never from the vault's location.

See [the complete implementation and limitations](v3-implementation.md), especially migration of legacy content-only evidence IDs. Do not silently upgrade old evidence to authenticated provenance.
