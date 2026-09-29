# autonomic-obsidian-kb

**Evidence-driven, local-first autonomic knowledge infrastructure for AI agents, with Obsidian as the durable human knowledge surface.**

The system's optimization target is not recall, note count, or vector-search quality. It is **lower total task cost at equal or better correctness and safety**:

```text
total task cost = retrieval + injected context + search/discovery
                + repeated reasoning + correction/recovery
                + latency/tool cost + amortized maintenance + risk
```

A memory is therefore not “text that matched a query.” It is a scoped, temporally applicable, provenance-bearing claim, procedure, decision, constraint, failure, negative result, summary, or episode whose value can be measured against real agent work.

## V3: connected, tested context infrastructure

Version **0.3.0** repairs the gap between architecture claims and the execution path. Query planning, source applicability, safe read gates, whole-representation compilation, explicit partial evidence, delivery counting, and host-acknowledged context deltas now participate in retrieval. The full implementation map, migration contract, and deliberate limitations are in [V3 implementation](docs/v3-implementation.md).

**No empirical agent-token savings are claimed by the regression suite.** Unknown usage stays unknown. Default token counts are labeled estimates; an exact profile requires a registered exact tokenizer. Memory and receipts never authorize actions.

## Architecture

V3 preserves the original invariants—Markdown authority, disposable indexes, hard scope/trust gates, progressive disclosure, conservative healing—and adds the control and evidence layers used by the current conservative control path:

- **Obsidian Markdown remains human-readable semantic authority.**
- **Content-addressed evidence objects** preserve source spans, observations, task episodes, test/command results, and corrections.
- **Append-only memory operations** (`ADD`, `AMEND`, `SUPERSEDE`, `RETRACT`, `MERGE`, `SPLIT`, `REVALIDATE`, `QUARANTINE`, `ARCHIVE`) make semantic evolution auditable.
- **Schema v2** adds memory families, repository identity, explicit valid-time intervals and source applicability, provenance taint, evidence, validators, and instruction authorization while retaining legacy-v1 compatibility.
- **Incremental indexing** skips unchanged Markdown by stat manifest; SQLite/FTS5 remains disposable.
- **Deterministic task-aware retrieval routing** can choose no retrieval, exact+lexical, lexical, hybrid, or temporal paths.
- **Candidate fusion** uses Reciprocal Rank Fusion across exact, lexical, expanded, graph, and optional local-dense channels.
- **Fixed, versioned ranking** keeps learned-policy activation disabled in this release. Feedback and route features remain available for offline evaluation; hard gates never depend on learned weights.
- **Set-level budget allocation** rewards coverage/complementarity and penalizes redundancy, uncertainty, risk, and token cost.
- **Task-planned context compilation** selects whole representations and preserves commands, preconditions, and verification without unsafe sentence splicing.
- **Temporal, version, and source gates** check declared applicability; Git ancestry alone is not continuing validity.
- **Non-executable validation** checks schema, evidence, file existence, and source hashes. Note-defined command execution is disabled.
- **Transactional healing** restores semantic files and events on handled failures; interrupted journals fail closed pending reconciliation.
- **Episodic capture and recurrence consolidation** prevent every observation from becoming canonical memory.
- **Multi-agent leases** reduce duplicate investigations.
- **Task traces, durable feedback/outcomes, isolated replay, and shadow policies** expose measurement without equating a proxy score with demonstrated avoided work.
- **Optional local embeddings** remain evidence-gated and never replace lexical or policy gates.
- **Real Obsidian + official CLI CI** remains required by the release process; use repository rulesets to enforce required checks at merge time.

## Durable versus derived state

```text
Durable / Git-friendly
├── Markdown memories                  human semantic projection
├── .kb-evidence/                      content-addressed evidence objects
├── .kb-memory-events/                 append-only semantic operations
├── .kb-episodes/                      raw task episodes
├── .kb-feedback.jsonl                 durable feedback
└── .kb-outcomes.jsonl                 durable task outcomes

Disposable / rebuildable
└── .kb/
    ├── index.sqlite3                  FTS, graph, decisions, rank examples
    ├── actions.jsonl                  autonomous actions
    ├── traces.jsonl                   task/retrieval spans
    ├── rank-policy.json               learned soft ranking policy
    ├── code-graph.json                repository code projection
    ├── embeddings-*.json              optional dense cache
    ├── receipts/                      source/representation revisions
    ├── context-state/                 host-acknowledged context inventory
    └── leases/                        short-lived multi-agent leases
```

Deleting `.kb/` cannot destroy canonical knowledge or evidence. First-use telemetry migration preserves legacy outcome/feedback rows outside `.kb/`; see the migration contract before cleaning an existing installation.

## Retrieval pipeline

```text
task + budget
    │
    ├─> Git/repository/symbol context
    ├─> query plan: intent, paths, identifiers, errors, temporal hints
    └─> route decision
           │
           ├─ NONE
           ├─ EXACT + LEXICAL
           ├─ LEXICAL
           ├─ HYBRID
           └─ TEMPORAL
                │
                v
       exact / FTS5 BM25 / expanded / optional dense
                │
              RRF fusion
                │
        bounded canonical graph expansion
                │
     HARD scope + trust + authorization + validity gates
                │
       calibrated soft utility reranking
                │
     set-level novelty/coverage/token optimization
                │
       task-specific L0-L3 context compiler
                │
      counted minimal context + diagnostic source receipt
```

The system is allowed to return `no_retrieval_needed`, `insufficient_evidence`, or `conflicting_evidence`. Empty context is a legitimate decision.

## Quick start

```bash
git clone https://github.com/mp2591/autonomic-obsidian-kb.git
cd autonomic-obsidian-kb
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .

kb init ~/Knowledge/agent-memory
export KB_VAULT=~/Knowledge/agent-memory
kb doctor
```

Retrieve with a final-payload budget under the explicitly reported token counter:

```bash
kb --vault "$KB_VAULT" --repo "$PWD" \
  retrieve "Fix the SQLite lock failure in the indexing pipeline" \
  --path src/autonomic_kb/index.py --budget 600
```

## Evidence-first learning

Explicit candidate:

```bash
kb --vault "$KB_VAULT" --repo "$PWD" remember \
  --title "Index connection ownership" \
  --summary "Each index operation owns and closes its SQLite connection." \
  --type invariant --scope repository \
  --source src/autonomic_kb/index.py
```

Capture an episode without immediately canonicalizing every observation:

```bash
kb episode \
  --task "debug sqlite locking" \
  --file src/autonomic_kb/index.py \
  --observation "Lock disappeared after deterministic connection ownership" \
  --failed "Increasing the timeout alone did not fix leaked connections" \
  --outcome success
```

Consolidation can be recurrence-triggered or explicitly forced for a proven high-value episode.

## Feedback and outcome learning

```bash
kb feedback <retrieval-id> <memory-id> helpful
kb feedback <retrieval-id> <memory-id> incorrect --notes "Version changed"
kb calibrate                 # reports explicitly disabled; no policy changes
```

`kb calibrate` reports that learned ranking is disabled. Neither the command nor persisted learned-policy files can activate a replacement policy in this release.

Record descriptive outcomes; qualified matched experiments additionally require explicit experiment identities and independent evaluation:

```bash
kb outcome --task "fix indexing bug" --mode no-kb --success \
  --input-tokens 6000 --output-tokens 900 --maintenance-tokens 0 --searches 12 --file-reads 18

kb outcome --task "fix indexing bug" --mode kb --success \
  --input-tokens 2300 --output-tokens 900 --maintenance-tokens 100 --searches 4 --file-reads 6

kb benchmark --traces
```

For reproducible external-agent experiments, use `kb replay --spec replay.json --allow-execution`; trusted host commands are argv arrays, each arm uses isolated snapshots, and a separate evaluator is mandatory. This is not an OS sandbox.

## Validation and self-healing

```bash
kb validate
kb validation-queue
kb heal             # dry-run
kb heal --apply     # backup + apply + post-validation + rollback on regression
```

Validator types include repository-contained file existence and source hashes. All note-defined command validators are rejected without execution, including formerly allowlisted commands.

## Multi-agent coordination

```bash
kb lease acquire "investigate index deadlock" --ttl 30
kb lease release "investigate index deadlock"
```

Leases are ephemeral coordination, not semantic knowledge.

## Retrieval experiments

Compare routes without injecting them into an agent:

```bash
kb shadow "why does indexing use WAL?" \
  --route lexical --route hybrid --route exact+lexical
```

Optional local dense retrieval is enabled only by configuration and the `embeddings` extra. It is a candidate generator, not an authority mechanism.

## MCP

`kb mcp` serves stdio MCP with backward negotiation for `2025-06-18` and support for the implemented `2025-11-25` resources/tools subset. It exposes retrieval, context, evidence inspection, feedback, validation, leases, scoped catalogs, context acknowledgements, and source receipts. Agent retrieval has one bounded content representation; diagnostic manifests are requested separately. stdio remains the default security boundary.

## Obsidian compatibility

Core operation is headless filesystem/index based. The official Obsidian CLI remains an optional application-aware adapter. CI separately downloads the pinned official AppImage, verifies its digest, enables CLI mode, starts the actual desktop application using `--ozone-platform=headless`, and runs bidirectional mutation, metadata-cache, graph, retrieval, and rendering contracts.

## Development

```bash
python -m pip install -e '.[dev]'
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
pytest
python benchmarks/run.py
```

## Design rule

A feature belongs in the default path only if measured correctness and avoided work exceed its retrieval, context, maintenance, latency, and risk costs.

**The goal is minimum necessary correct knowledge—not maximum memory.**
