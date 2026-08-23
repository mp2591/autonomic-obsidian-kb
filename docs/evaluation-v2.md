# Evaluation v2

The deterministic proxy benchmark remains a fast regression test. It is not sufficient evidence for avoided reasoning.

## Real task outcomes

`TaskOutcome` captures mode (`no-kb` or `kb`), success, input/output/cache/maintenance tokens, searches, file reads, commands, retries, corrections, latency, unsafe outcome and retrieved memory IDs. `kb benchmark --traces` reports paired deltas for identical task hashes.

## Matched replay

`kb replay --spec <json>` runs baseline and KB-assisted external argv commands under the same repository/vault environment. Commands may emit a final JSON metrics object. The harness records return code, elapsed time and supplied counters. No shell interpolation is used.

## Shadow retrieval

`kb shadow` compares routes without exposing their contexts to an acting agent. This allows candidate policies to accumulate evidence without task risk.

## Release metrics

A production evaluation should report success delta, token delta, search/read delta, correction rate, unsafe/stale retrieval rate, abstention behavior, p50/p95 retrieval latency, maintenance cost and real-Obsidian compatibility. No single recall metric is a release criterion.
