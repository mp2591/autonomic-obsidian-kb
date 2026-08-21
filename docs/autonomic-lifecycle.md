# Autonomic lifecycle

The implementation uses small feedback loops rather than forcing every concern into one MAPE-K diagram.

## Self-learning / self-evolving

Sources can submit structured candidates through `kb remember`, JSON/JSONL through `kb learn --file`, or a short-lived Git change map through `kb learn --git`. Candidate economics weighs reuse, rediscovery cost, confidence, stability, uniqueness, savings, and maintenance. Low-value material remains in the inbox. Security findings always route to quarantine.

## Self-validating

`kb validate` checks schema, canonical IDs, links, path applicability, source hashes, expiration, validation age, contradictions, secrets, and persistent instruction attacks. Validation reports are written to SQLite and JSONL telemetry.

Source-of-truth validation adapters can later execute commands or compare external authoritative sources. Those extensions must record evidence, time, and exact version.

## Self-healing

`kb heal` is dry-run by default. Safe actions include:

- add missing mechanical metadata;
- mark source-invalidated knowledge stale and reduce confidence;
- repair a wikilink only when the target is unique;
- quarantine unsafe persisted content;
- preserve a timestamped backup before changes.

Duplicate IDs and semantic contradictions are not auto-resolved. The system preserves both provenances and writes a conflict report.

## Self-organizing

Memory type determines a default folder, while canonical IDs and explicit relationships preserve identity across moves. Indexing rebuilds backlinks and graph edges. The graph is retrieval-supporting metadata, not a requirement for human navigation.

## Self-optimizing

Retrieval decisions and usage are recorded. The current deterministic score uses static weights; future weight updates should use benchmark/live outcomes with guardrails against popularity feedback loops. A frequently retrieved incorrect note should lose utility after correction rather than become entrenched.

## Self-pruning

`kb compact` identifies exact normalized duplicates, unused stale/superseded notes, and old low-utility notes. It is dry-run by default and archives in Markdown rather than deleting. `kb forget` also archives unless hard deletion is explicitly confirmed.

## Self-scoping

Scopes are assigned at creation and enforced before ranking. Narrow scopes do not leak upward or sideways. Repository and branch context come from Git; module context comes from active/requested paths.

## Self-configuring

`kb.toml` controls budgets, thresholds, staleness, archival age, paths, and cross-scope policy. FTS5 is detected at runtime. Obsidian CLI capabilities are probed rather than assumed.

## Self-monitoring

`status`, `stats`, `doctor`, benchmark results, SQLite usage/decision rows, and JSONL autonomous-action events expose current behavior. Every autonomous change records mode, plan, application count, and reason.

## Self-protecting

Prompt-injection persistence and secrets are scanned before promotion and during validation. Untrusted and conflicted notes fail retrieval. Backups, provenance, and non-destructive archival prevent an autonomic action from erasing evidence.

## Lifecycle states

```text
candidate -> quarantined
          -> inbox -> active -> stale -> active (revalidated)
                            -> conflicted -> active/superseded (human resolution)
                            -> superseded -> archived -> deleted (explicit only)
```

Promotion and revalidation should be evidence-driven. Time alone cannot convert a hypothesis into a fact.
