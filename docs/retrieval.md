# Retrieval architecture

## Inputs

`kb retrieve` consumes task text, a hard budget, optional requested paths, current working directory, Git root/branch/diff, agent identity, and session identity.

## Stage 1: classification

A deterministic low-cost classifier maps task terms to memory types such as commands, architecture, APIs, failures, solutions, dependencies, and conventions. The classifier is a ranking aid, not an authority decision.

## Stage 2: candidate generation

SQLite FTS5 searches title, summary, L1, L2, L3, and path. If FTS5 is unavailable, a lexical SQL fallback is used. Empty context queries can scan active metadata, but normal retrieval never starts from the entire body corpus.

Embeddings are deliberately absent from the default path. An embedding backend should be added only as a parallel candidate generator and should still pass every later gate.

## Stage 3: hard gates

1. Status: quarantined, archived, superseded, and conflicted notes are excluded.
2. Trust: untrusted or confidence below 0.35 is excluded unless explicitly requested.
3. Repository/project: mismatches are excluded unless cross-repo policy is enabled.
4. Module: requires module or applicable-path match.
5. Branch: requires exact active branch.
6. Task/session: requires exact identity.

These gates prevent a high lexical score from turning contamination into context.

## Stage 4: ranking

Accepted candidates are scored by lexical relevance, scope, active path, type, confidence, authority, validation, freshness, and historical utility. The score is deterministic and its components are returned in the `why` trail.

## Stage 5: bounded graph expansion

Only the three strongest seeds above a relevance threshold can expand one relationship hop. Expanded notes are rescored and gated. This allows a failure note to bring in its `fixed-by` solution without traversing the entire vault.

## Stage 6: disclosure and allocation

Auto depth uses L2 only for strong candidates, L1 for ordinary useful facts, and L0 for marginal pointers. A candidate that does not fit is downgraded before exclusion. Selection ends when the budget is exhausted or marginal score per token falls below the stop threshold.

## Stage 7: observability

Every considered note receives an inclusion or exclusion decision with score and reasons. `kb why <id>` shows recent decisions. Usage records include retrieval ID, task hash, rank, score, layer, and token estimate.

## Expansion contract

The manifest is sufficient by default. An agent should read the source Markdown or request a deeper layer only when:

- a selected fact is ambiguous;
- provenance is needed for a high-risk decision;
- the task reaches an edge case explicitly named by the summary;
- validation status is not strong enough for the action.

This contract prevents “retrieve summary, then immediately dump every source” behavior.
