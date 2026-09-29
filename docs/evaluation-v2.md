# Evaluation contract for V3

The corpus benchmark is a deterministic regression proxy, not empirical avoided reasoning.

A `TaskOutcome` records nullable input/output/maintenance usage, cached input as a subset, task success, work counters, and experiment metadata. Missing counters remain unknown. `.kb-outcomes.jsonl` and `.kb-feedback.jsonl` are durable; legacy rows are imported without manufacturing matched observations.

Pairing uses a unique `pair_id`, task identity, and equal nonempty repository/vault snapshots, model, agent, evaluator, and tool configuration. Both arms must have been evaluated. Multiple rows for an arm are rejected as ambiguous, never overwritten. `kb benchmark --traces` separates descriptive pairs from matched usage-complete comparisons, and returns unknown means when none qualify.

`kb replay --spec replay.json --allow-execution` requires baseline, assisted, and independent evaluator argv arrays. Repository, vault, and home directories are separate snapshots. A zero agent exit code without independent verification is not task success. This trusted-host harness is not a sandbox or an automatic provider-credential integration.

`kb shadow` does not record usage/rank training examples. Learned ranking is disabled in this release: `kb calibrate` returns an explicit disabled result and persisted learned-policy files are ignored. A future activation path must use independent train/holdout groups, downstream task evaluation, and explicit promotion.

Report actual delivered context, all agent and memory-management token calls, corrections, task success, safety failures, and latency together. See [V3 verification and limits](v3-implementation.md).
