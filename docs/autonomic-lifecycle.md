# Autonomic lifecycle v2

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

The system uses evidence-backed control loops rather than treating every agent utterance as memory.

## Learn

Capture task episodes and content-addressed evidence. Candidate promotion considers reuse, rediscovery cost, stability, uniqueness, expected savings, maintenance cost, trust, evidence, semantic duplicates and conflicts. Prompt-injected or secret-bearing candidates are quarantined. Privileged instructions require authorization.

## Consolidate

Repeated or high-value observations may be consolidated from episodes into semantic/procedural memories. Operations are append-only and auditable. Raw episodes/evidence remain available if consolidation is later shown wrong.

## Validate

Schema, evidence digests, links, temporal conflicts, source hashes, repository containment and non-executable source validators are checked. Validation work can be prioritized by stale probability × reuse × harm / cost.

## Heal

Dry-run is the default. Mechanical metadata, stale marking, unambiguous link repair and quarantine are eligible. Files are backed up and post-validation runs after apply; the healer rolls back if errors increase. Semantic contradictions remain explicit.

## Optimize

Retrieval traces and feedback create labeled rank examples. Calibration updates only soft utility weights. Shadow retrieval and matched task replay allow policy comparison before deployment.

## Prune

Compaction considers near-duplicate coverage, staleness, usage, utility and replacement evidence. Derived summaries can be archived without deleting their evidence or semantic event history.

## Coordinate

Task leases reduce duplicated multi-agent investigations. Leases are ephemeral and never become semantic truth by themselves.
