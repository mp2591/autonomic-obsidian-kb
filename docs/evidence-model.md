# Evidence, episodes and memory operations

V2 separates current semantic projection from the evidence and operations that created it.

## Evidence objects

Evidence is content-addressed as `evidence:sha256:<digest>` and stored under `.kb-evidence/<prefix>/<digest>.json`. Reading an evidence object re-computes the content digest. Tampered or missing evidence is a validation warning and cannot silently count as proof.

Evidence kinds can represent source observations, task episodes, command/test results, user corrections or externally snapshotted material. Provenance taint belongs to the derivation chain: summarizing untrusted input does not make it trusted.

## Memory operations

Semantic mutations are append-only records under `.kb-memory-events/`:

`ADD`, `AMEND`, `SUPERSEDE`, `RETRACT`, `MERGE`, `SPLIT`, `REVALIDATE`, `QUARANTINE`, `ARCHIVE`, `NOOP`.

Each operation records actor, basis evidence, previous/new digest, reason and policy version. An optional expected previous digest supplies optimistic concurrency for multiple agents.

## Episodes

`.kb-episodes/` stores raw task-boundary records: task, repository/commit, inspected files, commands, observations, failed hypotheses, successful actions, correction and outcome. Episodes do not automatically become canonical facts. Consolidation is recurrence/evidence gated.
