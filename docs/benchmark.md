# Evaluation and benchmark design

V2 keeps the original deterministic proxy as a fast regression guardrail, but does not treat it as proof of real token savings.

## Deterministic layer

Measures expected-memory coverage, injected context, precision/false context and proxy no-KB versus assisted token cost on fixed fixtures.

## Task-outcome layer

`kb outcome` records actual success, model tokens, searches, file reads, commands, retries, corrections, latency, unsafe outcomes and retrieved memory IDs. `kb benchmark --traces` compares paired no-KB/KB outcomes.

## Matched replay

`kb replay --spec` runs explicit argv-array external-agent experiments under fixed repository/task configuration. It never invokes a shell. This is intended for reproducible paired task evaluation.

## Shadow evaluation

`kb shadow` compares candidate retrieval routes without injecting alternatives into the acting agent. This supports safe route/ranker experiments and counterfactual analysis.

Release decisions should consider task-success delta, total-token delta, search/read delta, false-context rate, stale/unsafe retrieval, latency and maintenance cost—not recall alone.
