# autonomic-obsidian-kb

**Evidence-driven, local-first autonomic knowledge infrastructure for AI agents, with Obsidian as the durable human knowledge surface.**

The system's optimization target is not recall, note count, or vector-search quality. It is **lower total task cost at equal or better correctness and safety**:

```text
total task cost = retrieval + injected context + search/discovery
                + repeated reasoning + correction/recovery
                + latency/tool cost + amortized maintenance + risk
```

A memory is therefore not “text that matched a query.” It is a scoped, temporally applicable, provenance-bearing claim, procedure, decision, constraint, failure, negative result, summary, or episode whose value can be measured against real agent work.

## V2 architecture

V2 preserves the original invariants—Markdown authority, disposable indexes, hard scope/trust gates, progressive disclosure, conservative healing—and adds the control and evidence layers needed for a genuinely autonomic system:

- **Obsidian Markdown remains human-readable semantic authority.**
- **Content-addressed evidence objects** preserve source spans, observations, task episodes, test/command results, and corrections.
- **Append-only memory operations** (`ADD`, `AMEND`, `SUPERSEDE`, `RETRACT`, `MERGE`, `SPLIT`, `REVALIDATE`, `QUARANTINE`, `ARCHIVE`) make semantic evolution auditable.
- **Schema v2** adds memory families, repository identity, bitemporal validity, provenance taint, evidence, validators, and instruction authorization while retaining legacy-v1 compatibility.
- **Incremental indexing** skips unchanged Markdown by stat manifest; SQLite/FTS5 remains disposable.
- **Adaptive retrieval routing** can choose no retrieval, exact+lexical, lexical, hybrid, or temporal paths.
- **Candidate fusion** uses Reciprocal Rank Fusion across exact, lexical, expanded, graph, and optional local-dense channels.
- **Outcome-calibrated ranking** learns only soft utility weights from feedback; scope, trust, authorization, and validity are hard non-learnable constraints.
- **Set-level budget allocation** rewards coverage/complementarity and penalizes redundancy, uncertainty, risk, and token cost.
- **Task-specific context compilation** preserves exact commands/identifiers while compressing irrelevant detail.
- **Temporal and Git-lineage gates** distinguish current truth from historical truth.
- **Executable validation** is allowlisted, repository-contained, timeout-bounded, and evidence-producing.
- **Rollback-safe healing** restores backups if automatic repair makes validation worse.
- **Episodic capture and recurrence consolidation** prevent every observation from becoming canonical memory.
- **Multi-agent leases** reduce duplicate investigations.
- **Task traces, feedback, paired outcomes, shadow retrieval, and matched replay** turn memory policy into a measurable control problem.
- **Optional local embeddings** remain evidence-gated and never replace lexical or policy gates.
- **Real Obsidian + official CLI CI** remains release-blocking.

## Durable versus derived state

```text
Durable / Git-friendly
├── Markdown memories                  human semantic projection
├── .kb-evidence/                      content-addressed evidence objects
├── .kb-memory-events/                 append-only semantic operations
└── .kb-episodes/                      raw task episodes

Disposable / rebuildable
└── .kb/
    ├── index.sqlite3                  FTS, graph, decisions, rank examples
    ├── actions.jsonl                  autonomous actions
    ├── traces.jsonl                   task/retrieval spans
    ├── feedback.jsonl                 retrieval feedback
    ├── outcomes.jsonl                 paired task outcomes
    ├── rank-policy.json               learned soft ranking policy
    ├── code-graph.json                repository code projection
    ├── embeddings-*.json              optional dense cache
    └── leases/                        short-lived multi-agent leases
```

Deleting `.kb/` cannot destroy canonical knowledge or evidence.

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
      proof-bearing minimal context manifest
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

Retrieve under a hard budget:

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
kb calibrate
```

`kb calibrate` trains only soft ranking weights. It cannot learn to bypass repository scope, trust, privileged-instruction authorization, quarantine, or temporal validity.

Record matched real-agent outcomes:

```bash
kb outcome --task "fix indexing bug" --mode no-kb --success \
  --input-tokens 6000 --searches 12 --file-reads 18

kb outcome --task "fix indexing bug" --mode kb --success \
  --input-tokens 2300 --searches 4 --file-reads 6

kb benchmark --traces
```

For reproducible external-agent experiments, use `kb replay --spec replay.json`; commands are argv arrays and are never passed through a shell.

## Validation and self-healing

```bash
kb validate
kb validation-queue
kb heal             # dry-run
kb heal --apply     # backup + apply + post-validation + rollback on regression
```

Validator types include repository-contained file existence, source hashes, and allowlisted argv-based command execution. Shell syntax is rejected.

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

`kb mcp` serves stdio MCP with backward negotiation for `2025-06-18` and support for `2025-11-25` resources/tools. It exposes retrieval, context, evidence inspection, feedback, validation, leases, dashboards, and memory inspection. stdio remains the default security boundary.

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
