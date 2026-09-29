# Retrieval v2

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

Retrieval minimizes expected task cost rather than dumping a fixed top-k.

## Inputs

Task text, token budget, cwd, canonical repository identity, Git branch/head/merge-base, changed/requested paths, changed symbols, agent/session identity, and task risk.

## Stages

1. **Query planning:** infer intent, identifiers, paths, error signatures, temporal hints and memory types.
2. **Adaptive route:** `none`, `exact+lexical`, `lexical`, `hybrid`, or `temporal`.
3. **Candidate generation:** exact/path lookup, FTS5 BM25, expanded lexical queries, bounded graph edges and optional local dense retrieval.
4. **RRF fusion:** heterogeneous rankings are combined without pretending their raw scores share a scale.
5. **Hard gates:** status, trust, privileged-instruction authorization, canonical repository identity, module/branch/task/session scope and valid time/commit lineage.
6. **Soft reranking:** interpretable feature policy covers lexical/exact/RRF, task type, path/symbol proximity, confidence, authority, validation, freshness, historical utility, evidence strength and query overlap.
7. **Set allocation:** marginal gain rewards complementary task coverage and evidence while penalizing redundancy, risk and token cost.
8. **Compilation:** task-specific L0-L3 views preserve exact commands/identifiers and omit irrelevant prose.
9. **Observability:** selected/excluded candidates, features, route, budget and task outcome are traceable.

The system may return `no_retrieval_needed`, `insufficient_evidence`, or `conflicting_evidence` rather than manufacturing context.

## Learned ranking

Learned ranking is disabled in this release: ranking uses a fixed, versioned policy, `kb calibrate` returns an explicit disabled result, and `.kb/rank-policy.json` is ignored. Feedback and rank examples are recorded for offline evaluation. If a learned policy is enabled later it may only adjust soft-feature weights; scope, trust, authorization and temporal validity remain non-learnable constraints, and candidate policies are compared in shadow mode first.

## Dense retrieval

Dense retrieval is optional (`.[embeddings]`) and is only a candidate generator. It never bypasses the exact/lexical baseline or hard policy gates. Promotion to the default route requires positive task-level net utility on paired replay.
