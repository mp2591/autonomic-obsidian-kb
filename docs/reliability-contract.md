# Reliability contract

The knowledge base must reduce rediscovery without manufacturing certainty or destroying human knowledge. This contract defines invariants to exercise with regression tests, not claims that every invariant is already proven.

## Canonical identity and derived state

- Rebuilding or incrementally refreshing an index must yield the same logical records and graph after moves, deletions, and identifier edits.
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
