# Evaluation and benchmark design

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

V2 keeps the original deterministic proxy as a fast regression guardrail, but does not treat it as proof of real token savings.

## Deterministic layer

Measures expected-memory coverage, injected context, precision/false context and proxy no-KB versus assisted token cost on fixed fixtures.

`python benchmarks/run.py` is a regression gate, not only a savings check. It writes `benchmarks/results/latest.json` and fails when aggregate proxy savings are not positive, mean expected coverage is below 0.70, or the results regress against the committed `benchmarks/results/reference.json`:

- per-case or mean expected coverage falls;
- mean precision falls by more than 0.02;
- total false context rises;
- a case injects more than 5% more tokens.

Improvements never fail the gate. The small tolerances absorb BM25 tie ordering that can differ between SQLite builds. Benchmark retrieval ignores uncommitted working-tree changes and code symbols parsed from live files, both of which ordinary retrieval adds to the query, so a developer checkout and clean CI produce identical results. When a deliberate retrieval change moves the results, regenerate the reference with `python benchmarks/run.py --update-reference` in the same commit. `kb benchmark --reference <file>` applies the same comparison to other vaults and task sets.

## Task-outcome layer

`kb outcome` records actual success, model tokens, searches, file reads, commands, retries, corrections, latency, unsafe outcomes and retrieved memory IDs. `kb benchmark --traces` compares paired no-KB/KB outcomes.

## Matched replay

`kb replay --spec` runs explicit argv-array external-agent experiments under fixed repository/task configuration. It never invokes a shell. This is intended for reproducible paired task evaluation.

## Shadow evaluation

`kb shadow` compares candidate retrieval routes without injecting alternatives into the acting agent. This supports safe route/ranker experiments and counterfactual analysis.

Release decisions should consider task-success delta, total-token delta, search/read delta, false-context rate, stale/unsafe retrieval, latency and maintenance cost—not recall alone.
