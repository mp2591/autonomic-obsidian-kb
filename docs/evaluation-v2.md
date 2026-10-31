# Evaluation contract (0.3.1)

The corpus benchmark is a deterministic regression proxy, not empirical avoided reasoning. Passing it does not establish downstream task success or real-agent token savings.

## Usage and outcomes

`TaskOutcome` and `kb outcome` keep omitted input, output, maintenance, cached-input, work counters, and latency unknown. Provide explicit zeros only for observations actually known to be zero. Cached input is a subset of input and is never added again. Totals require known input, output, and maintenance and a true completeness flag. A caller's explicit incomplete flag cannot be overwritten by present counters.

New records use `accounting_version: 2`. Earlier `.kb-outcomes.jsonl` rows are preserved but excluded from token-complete comparisons, because previous versions defaulted omitted maintenance to zero. Their independently recorded success outcomes may still qualify for correctness analysis. Legacy runtime imports remain descriptive/unmatched. Totals are recomputed from validated counters, not trusted from a precomputed `total_tokens` field. Invalid JSONL is reported with a line number; repair it before benchmarking rather than silently losing failed tasks.

## Matching and denominators

A matched pair needs a new, unique `pair_id`, equal nonempty task identity, matching nonempty repository/vault fingerprints, model ID, agent version, evaluator ID, and tool configuration, plus an evaluator result for each arm. Duplicate arms are rejected, never overwritten. Explicit pair IDs and old task hashes are separate grouping namespaces.

`kb benchmark --traces` reports:

- `pairs` / `matched_pairs`: all matched evaluated pairs, irrespective of missing usage.
- `token_pairs` / `complete_usage_pairs`: matched pairs with usable totals for both arms.
- `metric_pairs`: the observed denominator for each success, token, search, and file-read delta.

Success means use all matched pairs, including evaluated failed and timed-out agents. Token/work means use only observed counters and return null when none exist. Missing usage can still bias the cost subset, so report its denominator alongside correctness; never infer equal success or zero cost from missing values. `descriptive_pairs` includes legacy/unmatched comparisons, and `rejected` explains discarded records or ambiguous arms.

## Replay

`kb replay --spec replay.json --allow-execution` accepts trusted baseline, assisted, and independent-evaluator argv arrays. All task/argv/timeout/pair-ID fields are checked before the first command. Omit `pair_id` to create a new one for each run; reusing a recorded ID is rejected.

A single captured repository seed supplies both working directories. Copies are verified against the seed fingerprint, including executable permissions, and the assisted starting state is checked again after the baseline. Vault and home directories are separate. A nested KB vault is excluded from both working-tree copies and supplied separately only to the assisted arm. The repository cannot equal or live inside the vault. Use a standalone checkout; `.git` worktree-pointer files are rejected because they can point back to an original shared Git database.

Ignored runtime/virtual-environment directories are pruned before symlink checks. Included links to external files and directory links are rejected; internal file links are materialized as independent copies. Every command has its own finite positive timeout. Failed or timed-out agents still run the independent evaluator; incomplete evaluation remains unqualified. A zero agent exit code or an agent-provided `success` field is not task success. Malformed metric payloads preserve the independently evaluated outcome with explicit metric errors and unknown totals.

This is a **trusted-host experimental harness, not an OS sandbox**. Commands run as the host user; evaluator identity is the configured argv hash, not a cryptographic attestation of test code. The host must keep its evaluator and inputs trustworthy, disable baseline KB tools, isolate credentials, and control process trees/network/filesystem access when stronger isolation is required. A source capture is not an atomic filesystem snapshot, although both arms use the same captured bytes. Existing Git history can still contain memory files; excluding the working-tree vault is not an information-flow guarantee.

## Policy evaluation

`kb shadow` does not record usage/rank training examples. Learned ranking remains disabled: `kb calibrate` returns an explicit disabled result and persisted learned-policy files are ignored. Any future activation path needs independent train/holdout groups, downstream task evaluation, and explicit promotion.

Report all agent and memory-management calls, delivered context, corrections, correctness, observed safety failures, and latency together. See [reliability guarantees](reliability-contract.md).
