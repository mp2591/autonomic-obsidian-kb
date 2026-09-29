# V3 implementation and verification contract

Version 0.3.0 connects the recovered V2 components to the production retrieval path and repairs the security, measurement, persistence, and Obsidian integration gaps identified in the September 9, 2026 review. The design objective remains **less total agent work at equal or better correctness and safety**. This is not a claim that every research technique is beneficial or that real-agent token savings have already been established.

## What changed

| Area | Implemented behavior | Regression coverage |
|---|---|---|
| Actual delivery budget | Count the final Markdown and compact JSON under the selected tokenizer; remove complete items until both fit. Budgets below 80 are rejected, never increased. Diagnostic manifests are separate. | Small-budget, Unicode-capable counter, exact-adapter, and MCP response tests. |
| Production query plan | Extract paths and identifiers; one-symbol queries can retrieve; route overrides are validated. Debug/procedure tasks prefer the full L2 representation when it fits. | Single-identifier retrieval and compiler tests. |
| Evidence sufficiency | Report missing roles and `partial_context`; a summary is not counted as procedure steps when the actual steps were omitted. Whole command blocks and qualifications are retained. | Incomplete-procedure and missing-steps regressions. |
| Eligibility before limits | Scope, trust, schema, temporal/source validity, duplicate IDs, conflicting applicable claims, and evidence integrity are checked before limited lexical/dense/graph candidate selection. | Ineligible flooding, cross-repository reads, conflicting claims, duplicate identity, and malformed schema tests. |
| Temporal/source applicability | Explicit requested dates; version ranges require a named package and supplied version. Changed source dependencies invalidate only dependent memories. Git ancestry alone does not establish continuing validity. | Historical/version contexts, source changes, and directory-boundary tests. |
| Context deltas | Explicit host acknowledgements retain representation revisions per session/epoch; a reset epoch or changed model profile restores context. Receipts recheck source and policy state before action. | Epoch, model migration, session collision, stale receipt, and fresh-version tests. |
| Durable evidence | Identity binds content digest, kind, subject, repository, commit, path, producer, and metadata. Requested identity and envelope are verified on read. | Equal-content/different-provenance and forged-identity tests. |
| Secret-before-persistence | Scan candidate fields, episodes, evidence envelopes, feedback, outcomes, and operation records before durable writes. External state symlinks and path escapes are rejected. | Canary tests at ingestion sinks and runtime symlink tests. |
| Procedure learning | Successful actions become a procedural candidate only with an evaluation-kind evidence object and explicit verification. Preconditions and source evidence remain attached. | Verified episode consolidation and applicability-sensitive deduplication tests. |
| Corroboration and recurrence | Identical applicable records attach novel evidence; different prerequisites/details do not collapse. Recurrence is exact-observation, repository-scoped, and independent-episode based. | Duplicate/body/prerequisite, novel evidence, and recurrence tests. |
| Semantic writes | Cooperating processes use a reentrant cross-process vault lock. Handled failures restore Markdown and operation files, including renames and newly created files. Interrupted journals block reads and writes pending reconciliation. | Concurrent compare-and-swap/leases, rollback, and interrupted-journal tests. |
| Non-executable validation | Note-defined command execution is removed, not merely hidden behind a handler flag. File-existence and source-hash validators remain available. | Execution-disabled test asserts no subprocess call. |
| Safer MCP | Server-side schemas reject undeclared fields; candidate imports cannot self-assign authority. Resource/inspection paths use the same eligibility policy. One model-visible content representation avoids duplication. | Privilege-field rejection, direct read gates, one-response-budget tests. |
| Replay and outcomes | Separate repository/vault/home snapshots, required evaluator, explicit opt-in execution, unknown missing counters, cache counted once, explicit experiment identities, and all repetitions retained. | Isolation, independent failure evaluation, ambiguous pairing, and accounting tests. |
| Conservative ranking | Fixed versioned ranking. Learned-policy training and activation are disabled; persisted learned-policy files cannot affect retrieval. Feedback and original route features remain available for offline analysis. | Disabled-calibration and ignored-policy-file tests. |
| Code projection | Qualified Python symbols with line spans, imports, and file hashes. Cache invalidates on changed/added/deleted files. Other supported languages remain file-level projections. | Qualified-symbol and cache-invalidation tests. |
| Obsidian metadata readiness | After a filesystem write, the integration contract waits for the actual expected property value; search visibility alone is not readiness. Original assertions remain mandatory. | Real desktop/official CLI CI contract. |
| Source identity | Source integrity CI archives committed source, commit/tree IDs, a Git file manifest, and archive checksum. The obsolete export workflow is removed. | Compare downloaded artifact blob hashes with the tested local tree. |

## Public interfaces

```bash
kb --vault "$KB_VAULT" --repo "$PWD" retrieve "run the tests" \
  --budget 350 --session coding-1 --epoch context-1
kb --vault "$KB_VAULT" --repo "$PWD" ack RETRIEVAL_ID \
  --session coding-1 --epoch context-1
kb --vault "$KB_VAULT" --repo "$PWD" receipt-check RETRIEVAL_ID

# Explicit applicability context; an unknown version does not satisfy a version range.
kb --vault "$KB_VAULT" --repo "$PWD" retrieve "deployment procedure" \
  --version demo=2.1 --at 2026-09-09 --budget 500

kb calibrate                 # explicit disabled result; no policy mutation
kb calibrate --promote       # also disabled in this release
kb replay --spec replay.json --allow-execution
```

MCP exposes `kb_retrieve`, `kb_context`, `kb_remember`, `kb_feedback`, `kb_evidence_get`, `kb_validate`, `kb_why`, `kb_status`, `kb_lease_acquire`, `kb_lease_release`, `kb_context_ack`, and `kb_receipt_check`. Read-only annotations describe semantic intent; indexing/telemetry may still update derived local state. For versions bound to an action, the receipt check requires fresh host-supplied `versions`; it does not reuse old version observations implicitly.

### Delivery versus diagnostics

CLI retrieval JSON and MCP retrieval return compact agent content. `RetrievalManifest.to_dict()` is explicitly diagnostic and not under the injection cap; `--explain-exclusions` deliberately opts into additional diagnostic output. Evidence, ranking explanations, and source revisions are inspected on demand rather than duplicated in every prompt.

An exact counter can be registered with `TOKENIZERS.register(profile, callable)`. Without one, the result explicitly says `token_count_exact: false` and uses the deterministic estimate. The cap covers the selected payload representation, **not** host-added system prompts, MCP framing, tool definitions, or the rest of the agent conversation. Those belong in end-to-end provider accounting.

`authorizes_action: false` is returned with agent context and action receipts. Memory is evidence, never a permission grant. The agent host must enforce actual tool permissions outside the memory store.

## Measurement that does not manufacture savings

Normalize provider usage first: input includes cached input; cached tokens are a subset and are not added again. Input, output, and maintenance usage must be present before reporting a total. Missing values stay unknown. Legacy outcome rows are preserved but are not upgraded to matched empirical evidence.

A matched pair needs a unique `pair_id`, equal task hash, and matching nonempty `repo_snapshot`, `vault_snapshot`, `model_id`, `agent_version`, `evaluator_id`, and `tool_config`, plus an evaluator result for each arm. Duplicate or missing arms are rejected, not overwritten. The trace benchmark reports only matched, usage-complete pairs in its primary means; no qualified pairs means unknown means rather than invented zero savings.

The replay spec requires `baseline_command`, `kb_command`, and `evaluator_command` as argv arrays. Each command runs in its own copy; baseline receives an empty separate vault, assisted execution receives the frozen vault snapshot. Only minimal environment variables are inherited. Agent adapters must explicitly arrange legitimate authentication and emit normalized metrics. The evaluator must verify the resulting artifact/task, not merely accept an agent's self-report.

The built-in corpus benchmark remains a **deterministic regression proxy**. Its corpus-size baseline and estimated maintenance are not observed avoided reasoning. No live model calls, commercial benchmark scores, or downstream task-token improvement are implied by the unit suite.

Native YAML dates are normalized to ISO strings, and string frontmatter is always quoted so values such as `off`, `false`, and date-like text retain their declared type. A parser-format marker forces revalidation of unchanged files when upgrading the index; old permissive schema flags are not reused.

## Migration and durability

Markdown, `.kb-evidence/`, `.kb-memory-events/`, `.kb-episodes/`, `.kb-feedback.jsonl`, and `.kb-outcomes.jsonl` are durable. Indexes, route traces, receipts, context inventories, and active ranking files under `.kb/` are derived/local runtime state. Review durable content before syncing it; local-first is not an assurance that all personal information has been removed.

On first telemetry use, legacy `.kb/feedback.jsonl` and `.kb/outcomes.jsonl` are copied into durable stores without deleting their originals. Outcome imports are marked incomplete/unmatched because the old counters and pairing cannot establish measured savings. A migration marker prevents repeated imports. Malformed or secret-bearing legacy data fails explicitly before migration; the original remains available for owner review.

The strengthened evidence identity is intentionally not backward-compatible with content-only evidence IDs. Old evidence files remain on disk, but are not silently relabeled as provenance-verified. Re-capture/revalidate their sources and amend memory references explicitly. There is no automatic trust-upgrading conversion.

Ordinary exceptions during KB-owned edits roll back semantic files and their operation records together. A process crash can leave a prepared journal in `.kb-transactions/`; subsequent reads and writes stop instead of guessing which later human edits to overwrite. Reconcile the saved before-state with current Markdown under owner control before resolving that journal. This is a deliberate fail-closed state, not a promise of automatic conflict resolution. The writer lock coordinates KB processes, not unrelated editors or distributed machines.

## Publication constraint

The full calibration-module upload was blocked twice by the platform. The published build deliberately omits learned-policy training and ignores persisted learned policies; it does not route around that restriction. All other release behavior is evaluated against the static policy. The calibration command remains compatible but returns an explicit disabled result.

## Safety and scope limits

The supported boundary is a trusted, local, single-user host with controlled stdio clients. This is **not** a multi-tenant remote authorization service. Authority metadata, evaluation producers, and filesystem ownership are not cryptographic attestations. Content hashes detect inconsistent content; they do not prove that the asserted fact is true or that a producer is who it claims to be.

Structural sufficiency checks do not prove semantic correctness. Prompt-injection and secret scanners are defense in depth with finite coverage, not complete detectors. Untrusted code must not be executed through replay: workspace copies, argv-only execution, and timeouts are not an operating-system sandbox. Signed authority events, robust distributed reconciliation, neural contradiction adjudication, task-level causal policy evaluation, and additional OS/Obsidian matrices remain evidence-gated work, not asserted capabilities.

## Verification and release process

```bash
python -m compileall -q src scripts tests
ruff check src scripts tests
python -m unittest discover -s tests -v
python -m pytest -q
python benchmarks/run.py --output /tmp/kb-proxy.json
python -m pip wheel --no-deps --wheel-dir dist .
```

CI repeats core checks on Python 3.11, 3.12, and 3.13. A separate offline-network container downloads and verifies the pinned official Obsidian AppImage during setup, starts the actual application with `--ozone-platform=headless`, and checks CLI, filesystem, metadata, graph, and rendering interoperability. No stub stands in for the application.

Do not merge while any of these checks fail. GitHub-enforced branch protection is a separate repository setting; a workflow alone does not prevent an administrator from merging a failing PR. A release report must identify the tested commit/tree and actual CI conclusion. Local unit success alone is not real-Obsidian certification.
