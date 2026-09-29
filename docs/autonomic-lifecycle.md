# Autonomic lifecycle v2

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

The system uses evidence-backed control loops rather than treating every agent utterance as memory.

## Learn

Capture task episodes and content-addressed evidence. Candidate promotion considers reuse, rediscovery cost, stability, uniqueness, expected savings, maintenance cost, trust, evidence, semantic duplicates and conflicts. Prompt-injected or secret-bearing candidates are quarantined. Privileged instructions require authorization.

## Review

Candidates below the promotion threshold wait in the inbox, and retrieval ignores them. The `remember` result reports the score, the threshold, and why the candidate was not promoted. A reviewer runs `kb inbox` to see candidates and blockers, and `kb promote <id> --reason …` to activate one. Promotion refuses unsafe content, unauthorized privileged instructions, and missing or tampered evidence. `kb supersede <old> <new> --reason …` retires a memory in favor of a replacement; this is how a contradiction is resolved. These commands are CLI-only, require a reason, run transactionally, and append `AMEND` or `SUPERSEDE` operations.

## Consolidate

Repeated or high-value observations may be consolidated from episodes into semantic/procedural memories. Operations are append-only and auditable. Raw episodes/evidence remain available if consolidation is later shown wrong.

## Validate

Schema, evidence digests, links, temporal conflicts, source hashes, repository containment and non-executable source validators are checked. Validation work can be prioritized by stale probability × reuse × harm / cost.

A memory bound to a source file with `--source` is excluded from retrieval once that file changes. `kb validate` reports `dependency-changed` or `dependency-missing` and `kb heal` plans `require-revalidation` instead of silently losing the memory. After confirming it still holds, a reviewer runs `kb revalidate <id> --reason …`, which rebinds the digests and appends a `REVALIDATE` operation. Repository-scoped memories without a repository identity produce a `missing-repository-identity` warning.

## Heal

Dry-run is the default. Mechanical metadata, stale marking, unambiguous link repair and quarantine are eligible. Files are backed up and post-validation runs after apply; the healer rolls back if errors increase. A quarantined note's findings at its new path are not counted as new errors. Semantic contradictions and changed sources remain explicit reviewer work.

## Optimize

Retrieval traces and feedback create labeled rank examples for offline evaluation. Learned-policy calibration is disabled in this release, so feedback does not change ranking. Shadow retrieval and matched task replay allow policy comparison before deployment.

## Prune

Compaction considers near-duplicate coverage, staleness, recorded retrieval usage, utility and replacement evidence; a memory that retrieval still delivers is never archived as unused. Derived summaries can be archived without deleting their evidence or semantic event history.

## Coordinate

Task leases reduce duplicated multi-agent investigations. Leases are ephemeral and never become semantic truth by themselves.
