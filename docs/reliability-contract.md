# Reliability contract

The knowledge base must reduce rediscovery without manufacturing certainty or destroying human knowledge. Version 0.3.1 adds failure-injection regressions for the index and evaluation invariants below. Passing these tests establishes the exercised cases, not complete semantic correctness.

## Canonical identity and derived state

- Rebuilding or incrementally refreshing an index must preserve the same logical notes and unambiguous links after moves, deletions, and identifier edits. Internal duplicate-row IDs are disposable and can differ; duplicate canonical targets stay unresolved.
- Duplicate declared identities must remain visible to diagnostics and unavailable as authoritative agent context; indexing must never silently overwrite another note.
- Ambiguous links must not resolve by filesystem traversal order.

## Durable changes

- A failed KB-owned mutation must not leave a partially changed note or a misleading successful operation record.
- Concurrent writers must preserve unrelated notes. Archive, replacement, and recovery paths must not overwrite an existing file.
- Source validity and context receipts must be rechecked before reuse; a retrieved claim is not an authorization grant.

## Evidence and evaluation

- Unknown usage stays unknown. Cached input is a subset of input, not an additional token charge.
- Replayed arms must start from the same frozen repository state, with separate runtime state and an independent evaluator.
- A regression proxy is not proof of task correctness or measured end-to-end savings.

## Verification

Run compile checks, lint, unit/pytest regressions, the existing benchmark guardrail, and real-Obsidian integration before release. Use commit and tree identities from actual tool results when publishing. Do not infer a merge or passing run from an attempted request or from a PR description.

## Implemented tests and boundaries

`tests/test_reliability_invariants.py` covers failed rebuild/insert rollback, caller-owned transactions, duplicate row-ID collisions, ambiguous/missing-path links, POSIX same-size edits, unknown usage, legacy grouping, validated labels, and per-metric benchmark denominators. `tests/test_replay_integrity.py` covers frozen paired inputs, input validation before execution, malformed metrics, timed-out tasks, executable-file identity, nested vaults, and unsafe snapshot links.

Index refresh uses a SQLite savepoint: other readers retain the old committed projection until completion, and errors roll back the refresh. Markdown and evidence remain authoritative; the database is still disposable. The first refresh after the parser-format change revalidates notes and graph links once. An index refresh failure is surfaced to the caller; it does not authorize serving stale knowledge for a new task.

Change-time metadata catches POSIX in-place edits whose mtime and size were restored. Filesystem timestamp resolution and Windows creation-time behavior limit that signal; use `kb index --rebuild` when exact revalidation is required. Transaction hard links also change ctime, so metadata-only changes verify bytes without repeating YAML/schema/security work.

Link resolution prioritizes exact canonical identities, then explicit vault paths, then unique titles/basenames. Qualified missing paths do not fall back to unrelated folders. `./` and `../` resolve relative to the source note within the vault. Ambiguous links remain unbound rather than guessing based on traversal order. This conservative projection does not claim complete emulation of Obsidian's alias-resolution rules.

The existing reviewer-only lifecycle, transaction reconciliation, and static ranking boundaries are unchanged. CI keeps the original retrieval benchmark thresholds and real-Obsidian assertions, and also exercises an installed wheel from a clean virtual environment with `python -I` outside the repository.
