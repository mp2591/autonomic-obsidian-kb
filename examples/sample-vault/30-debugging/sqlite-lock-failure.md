---
id: kb:repository:known-failure:sqlite-lock
title: SQLite index remains locked after a failed command
type: known-failure
scope: module
repo: autonomic-obsidian-kb
module: src
status: active
summary: A leaked KnowledgeIndex connection can leave the runtime database locked; close owned indexes or use the context manager.
confidence: 0.94
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 280
utility: 0.91
applies_to: ["src/autonomic_kb/**","tests/**"]
provenance: [{"kind":"test","path":"tests/test_index.py"}]
relations: {"fixed-by":["kb:repository:solution:close-index-connections"],"caused-by":["unclosed-sqlite-connection"]}
invalidation: {"paths":["src/autonomic_kb/index.py","src/autonomic_kb/retrieval.py","src/autonomic_kb/learning.py","src/autonomic_kb/healing.py"]}
---

## L0 — Pointer

SQLite lock caused by an unclosed index connection.

## L1 — Fact

Every component that creates its own `KnowledgeIndex` must close it; shared indexes are owned by the caller.

## L2 — Summary

Symptoms include `sqlite3.OperationalError: database is locked` during repeated CLI/tests, especially on Windows. `KnowledgeIndex` is a context manager. `Retriever`, `Learner`, `Validator`, `Healer`, and `Compactor` track whether they own an index and close only owned connections. CLI paths use `try/finally` or `with`.

## L3 — Detail

Do not add arbitrary sleep/retry loops first. Confirm connection ownership and transaction commits. WAL reduces reader/writer contention but does not excuse leaked writers. Tests should open and close repeated instances against one temporary vault.

## L4 — Provenance

- [[solutions/close-index-connections]]
- `src/autonomic_kb/index.py`
