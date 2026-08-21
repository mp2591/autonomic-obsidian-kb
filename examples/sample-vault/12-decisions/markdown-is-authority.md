---
id: kb:repository:decision:markdown-authority
title: Markdown is the durable authority
type: decision
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: Obsidian Markdown is canonical; SQLite, FTS, embeddings, and graph stores are disposable derivatives.
confidence: 0.99
authority: user-corrected
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 270
utility: 0.99
claim_key: knowledge-authority
claim_value: markdown
applies_to: ["src/**","docs/**","schema/**"]
provenance: [{"kind":"decision","path":"docs/architecture.md"}]
relations: {"validates":["kb:repository:architecture:retrieval-pipeline"],"related-to":["kb:repository:repository-map:project-layout"]}
invalidation: {"paths":["docs/architecture.md"]}
---

## L0 — Pointer

Markdown is canonical; indexes are caches.

## L1 — Fact

Never make SQLite, FTS, embeddings, or a graph database the only copy of knowledge.

## L2 — Summary

Human-readable Obsidian Markdown preserves provenance, reviewability, Git history, links, and portability. The SQLite index can be deleted and rebuilt. Optional embeddings or graph infrastructure may accelerate candidate generation, but autonomous maintenance must write validated changes back to Markdown.

## L3 — Detail

This decision prevents lock-in and corruption of a hidden machine store. It also lets humans audit autonomous actions in Obsidian and ordinary Git. A derived index may contain usage and retrieval telemetry that would be metadata bloat in notes, but that telemetry cannot redefine the claim itself.

## L4 — Provenance

- `docs/architecture.md`
- Project guiding principle
