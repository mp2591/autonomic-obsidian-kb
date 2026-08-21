---
id: kb:repository:architecture:retrieval-pipeline
title: Token-budgeted retrieval pipeline
type: architecture
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: Retrieval classifies the task, generates lexical candidates, applies scope and trust gates, ranks, expands one graph hop, and allocates the cheapest sufficient layer.
confidence: 0.97
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 360
utility: 0.96
applies_to: ["src/autonomic_kb/retrieval.py","src/autonomic_kb/scoring.py","src/autonomic_kb/index.py"]
provenance: [{"kind":"source","path":"src/autonomic_kb/retrieval.py"},{"kind":"test","path":"tests/test_retrieval.py"}]
relations: {"depends-on":["kb:repository:decision:markdown-authority"],"related-to":["kb:repository:repository-map:project-layout"]}
invalidation: {"paths":["src/autonomic_kb/retrieval.py","src/autonomic_kb/scoring.py","src/autonomic_kb/index.py"]}
---

## L0 — Pointer

Gate, rank, expand, then budget layers.

## L1 — Fact

Scope and trust are hard gates; graph expansion is one hop from strong seeds; context stops when marginal score per token is too low.

## L2 — Summary

The retriever inspects Git branch/diff, cwd, requested paths, agent, and session. FTS5 generates candidates. Repository/module/branch/task scopes and trust status are checked before weighted ranking. Only the strongest lexical seeds can add one relationship hop, and every neighbor is gated and rescored. Auto depth selects L0, L1, or L2. If a note is too large, the allocator lowers its layer before excluding it.

## L3 — Detail

The architecture intentionally rejects top-k full-note dumping. The budget reserves manifest overhead. Each candidate records component reasons, inclusion/exclusion, layer, and estimated tokens. `kb why` reads this decision trail. Embeddings remain an opt-in candidate generator and cannot bypass later gates.

## L4 — Provenance

- `src/autonomic_kb/retrieval.py`
- `src/autonomic_kb/scoring.py`
- `docs/retrieval.md`
