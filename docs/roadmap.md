# Roadmap

Extensions are ordered by evidence and risk, not novelty.

## Near term

- Execute validation adapters for commands, package metadata, JSON Schema/OpenAPI, and source hashes.
- Add Git commit/PR ingestion with durable candidate extraction and user-correction capture.
- Add retrieval feedback (`helpful`, `incorrect`, `expanded`) and utility decay.
- Expand the real-Obsidian compatibility matrix to macOS, Windows, ARM64 Linux, and selected plugin fixtures.
- Add branch/rebase-aware invalidation and rename detection using Git object IDs.
- Expand benchmark corpora and include real agent traces with actual token accounting.

## Evidence-gated

- Local embedding candidate generation for paraphrase misses.
- Learned ranking weights with monotonic trust/scope constraints.
- Filesystem watcher/daemon for large multi-agent vaults.
- Global + per-repository federated vaults with explicit trust domains.
- Optional graph database only if bounded SQLite edges become a measured bottleneck.
- Static and dynamic summary refresh when source changes can be validated automatically.

## Longer term

- Multi-agent lease/deduplication for concurrent investigations.
- Bayesian confidence updates from validation and correction history.
- Counterfactual task replay to estimate rediscovery avoided.
- Signed provenance and policy-enforced promotion for shared enterprise vaults.
- Safe external-authority refresh with source pinning, snapshots, and citation checks.

Every roadmap item must pass the same acceptance test: positive net token savings, preserved or improved correctness, and manageable local-first operational cost.
