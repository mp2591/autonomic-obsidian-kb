# Retrieval v2

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

Retrieval is a constrained decision system, not top-k similarity.

1. Refresh the stat-backed incremental index.
2. Detect Git repository, canonical remote identity, branch/head/merge-base, changed paths and code symbols.
3. Build a query plan with intent, identifiers, paths, error signatures, temporal language and subgoals.
4. Select a route or abstain.
5. Generate exact/lexical/expanded/optional-dense candidates.
6. Fuse heterogeneous rankings with Reciprocal Rank Fusion.
7. Expand a bounded canonical graph neighborhood.
8. Enforce status, trust, privileged-instruction, repository, scope and temporal gates.
9. Apply a versioned/calibrated soft utility policy.
10. Select a complementary set under the hard token budget.
11. Compile task-specific views from progressive layers.
12. Emit evidence, validation and uncertainty with the context.
13. Persist decisions/features for `why`, feedback and shadow evaluation.

The soft ranker can be retrained from labeled retrieval feedback. Hard policy gates are deliberately excluded from learning.
