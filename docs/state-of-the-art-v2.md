# State-of-the-art v2 implementation map

This document maps the 2026 architecture review to implemented controls.

| Review weakness | V2 implementation |
|---|---|
| Proxy-only token benchmark | Paired `TaskOutcome`, `kb benchmark --traces`, matched `kb replay` harness |
| No durable evidence/history | `.kb-evidence`, `.kb-memory-events`, typed operations, optimistic concurrency |
| Schema disagreement | `schema_version: 2`, Draft 2020-12 runtime validation, migration command, legacy compatibility |
| Fixed retrieval scoring | Versioned feature vectors + feedback-trained `RankPolicy`; hard gates non-learnable |
| BM25 magnitude discarded | Raw BM25 retained and normalized; exact/path channels added |
| Lexical-only retrieval | RRF-fused exact/lexical/expanded/graph plus optional local dense channel |
| Same retrieval for every task | `NONE`, `exact+lexical`, `lexical`, `hybrid`, `temporal` router |
| Raw task is only query | Query plan extracts identifiers, paths, error signatures, temporal intent and subgoals |
| Greedy sorted-list budget | Set-level marginal utility with coverage, novelty, evidence, risk and redundancy |
| Static layer injection | Task-specific context compiler under model-profile token budget |
| No abstention | Explicit no-retrieval / insufficient-evidence / conflicting-evidence states |
| Direct candidate canonicalization | Episodic capture + recurrence-gated consolidation |
| Uniform memory lifecycle | Memory `kind`, type-specific validation/authorization and negative-result memory |
| Exact-only duplicates | Near-duplicate and claim conflict cascade before promotion/compaction |
| Weak temporal model | Valid-time intervals, commit lineage and temporal conflict classification |
| Universal time staleness | Source/version triggers dominate; time aging only fallback |
| Repository basename identity | Canonical remote-derived `repository_id`; Git ancestry metadata |
| Flat note graph | Canonical `target_id`, reverse edges, PPR utility and code graph projection |
| No code intelligence | Python AST symbols/imports + generic repository file graph cache |
| Mechanical validation only | Evidence digest, JSON Schema, source/file and allowlisted command validators |
| Self-heal without postcondition | Backup, post-validation and automatic rollback on error regression |
| Simplistic forgetting | Evidence-preserving archive + near-duplicate and expected-value criteria |
| Regex-only memory security | Taint propagation, privileged-instruction authorization, entropy secret detection, hard gates |
| No multi-agent consistency | Operation concurrency checks + task leases |
| Event rows not task traces | Trace/span recorder and task outcomes |
| Weak outcome feedback | Rich feedback kinds + rank-example labels + calibration |
| Thin MCP surface | Resources, structured content, annotations, evidence/feedback/validation/lease tools |
| Obsidian compatibility inference | Existing release-blocking real-application Ozone-headless integration retained |

## Intentionally evidence-gated

The following remain opt-in rather than default:

- sentence-transformer dense retrieval;
- online learned ranking;
- automatic semantic consolidation;
- graph-driven expansion beyond bounded local use;
- external-agent replay commands.

No learned component can bypass hard authorization, provenance, scope, status or temporal gates.
