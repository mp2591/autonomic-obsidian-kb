---
id: kb:repository:solution:close-index-connections
title: Close owned index connections deterministically
type: solution
scope: module
repo: autonomic-obsidian-kb
module: src
status: active
summary: Use KnowledgeIndex as a context manager and close Retriever/Learner lifecycle objects in finally blocks when they own the index.
confidence: 0.96
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 230
utility: 0.92
applies_to: ["src/autonomic_kb/**","tests/**"]
provenance: [{"kind":"source","path":"src/autonomic_kb/index.py"}]
relations: {"fixes":["kb:repository:known-failure:sqlite-lock"]}
invalidation: {"paths":["src/autonomic_kb/index.py"]}
---

## L0 — Pointer

Deterministic SQLite connection ownership.

## L1 — Fact

Use `with KnowledgeIndex(config) as index:`; close wrapper objects in `finally` only when they created the index.

## L2 — Summary

The context manager commits component work before close. A wrapper receiving a shared index sets `_owns_index = False`; its `close()` does nothing. A wrapper creating an index sets `_owns_index = True` and closes it at its API boundary.

## L3 — Detail

This ownership rule avoids both leaks and accidental closure of a transaction shared across retrieval, benchmark, or validation stages.
