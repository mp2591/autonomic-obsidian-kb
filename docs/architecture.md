# Architecture

`autonomic-obsidian-kb` v0.2 is an evidence-preserving adaptive memory compiler for AI agents. Obsidian Markdown remains the durable human semantic projection; all retrieval accelerators are derived and rebuildable.

## Durable state

- Markdown memories: current human-readable claims, procedures, decisions, constraints, failures, negative results and summaries.
- `.kb-evidence/`: content-addressed source/test/observation/correction evidence.
- `.kb-memory-events/`: append-only typed semantic operations (`ADD`, `AMEND`, `SUPERSEDE`, `RETRACT`, `MERGE`, `SPLIT`, `REVALIDATE`, `QUARANTINE`, `ARCHIVE`, `NOOP`).
- `.kb-episodes/`: task episodes used for recurrence-sensitive consolidation.

## Derived state

`.kb/` contains SQLite/FTS5, link projections, rank examples, traces, feedback, outcomes, learned soft ranking policy, code graph, optional embeddings, backups and short-lived leases. It may be deleted and rebuilt without destroying canonical knowledge or evidence.

## Retrieval control loop

```text
task + Git/repository/symbol context
  -> query plan
  -> route: NONE | exact+lexical | lexical | hybrid | temporal
  -> exact/FTS/expanded/optional dense candidate generators
  -> Reciprocal Rank Fusion
  -> bounded canonical graph expansion
  -> HARD scope/trust/authorization/temporal gates
  -> calibrated soft utility scoring
  -> redundancy-aware set-level token allocation
  -> task-specific context compiler
  -> proof-bearing minimal manifest
  -> agent outcome/feedback
  -> shadow/offline policy calibration
```

Hard policy gates are deliberately outside the learned ranker. A learned policy cannot make a cross-repository, quarantined, unauthorized instruction, or temporally invalid memory eligible.

## Autonomic loop

Episodes and evidence are captured first. Candidate promotion is type- and trust-aware. Validators can test files, hashes and allowlisted commands inside the repository. Validation failures can mark memories stale. Healing is dry-run by default, backs up files, emits operations, revalidates, and rolls back if error count increases. Consolidation and compaction preserve evidence rather than treating the latest summary as the only historical record.

## Obsidian boundary

The core remains headless and filesystem-driven. The first-party Obsidian CLI is an optional app-aware adapter. Release CI separately launches the official pinned desktop application using Ozone headless mode and verifies parser/index/retrieval/mutation parity against the live Obsidian metadata cache.

## Non-goals

- a global unscoped vector memory;
- maximum recall;
- a required graph database;
- automatic semantic conflict resolution;
- letting retrieved text grant itself instruction/tool authority;
- making SQLite, vectors or model summaries the sole source of truth.
