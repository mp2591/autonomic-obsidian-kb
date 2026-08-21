# Security model

## Threats

- Persisted prompt injection that changes future agent behavior.
- Secret or credential leakage into a shared vault or Git history.
- Cross-repository or cross-user contamination.
- Low-confidence material becoming canonical through repetition.
- Malicious source files manipulating generated memory.
- Autonomous healing destroying contradictory evidence.
- Optional app/plugin/daemon integrations expanding local attack surface.

## Controls

### Scope before similarity

Repository, project, module, branch, task, and session are security boundaries. A note that fails scope is not “low ranked”; it is rejected. Cross-repository retrieval is disabled by default.

### Provenance and authority

Authority is explicit and independent of confidence. Agent-generated notes begin below verified/source-of-truth material. Contradictions block retrieval until provenance resolves them.

### Content scanning

Candidate and existing notes are scanned for private-key blocks, common access-token formats, generic assigned secrets, instruction overrides, prompt exfiltration, role forgery, and tool coercion. Findings route to quarantine. The scanner is a defense-in-depth heuristic, not a secret-management system.

### No secret memory

Store a pointer such as “credential is provided by environment variable `SERVICE_TOKEN`,” never the value. Repositories should also use a dedicated secret scanner in CI.

### Conservative autonomy

Healing makes timestamped backups. It marks stale rather than inventing replacement facts. It repairs links only when unambiguous. It never silently picks a winner for duplicate IDs or contradictory claims.

### Optional integration isolation

Core operation needs no Obsidian plugin, network service, embeddings model, or daemon. MCP is local stdio. A future network server must add authentication, request-size limits, scope binding, audit logs, and explicit write permissions.

## Trust policy for agent instructions

`agent-instruction` memories should require user-corrected, verified, or project-owned authority before promotion. Instructions derived from arbitrary repository text must remain untrusted because source code and issue content are attacker-controlled inputs in many workflows.

## Prompt-injection persistence response

1. Quarantine the note without executing its instructions.
2. Preserve original path, source, and finding.
3. Inspect the ingestion source and other notes from the same provenance.
4. Revoke leaked credentials through the owning system; deleting a note is not revocation.
5. Revalidate active agent-instruction memories before restoring retrieval.
