---
id: kb:repository:repository-map:project-layout
title: Project layout and entry points
type: repository-map
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: The CLI starts in cli.py; retrieval is retrieval.py plus scoring.py; Markdown remains authoritative and index.py is disposable acceleration.
confidence: 0.98
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 330
utility: 0.94
applies_to: ["src/**","tests/**"]
agents: ["codex","claude-code","gemini-cli"]
provenance: [{"kind":"file","path":"README.md"},{"kind":"file","path":"src/autonomic_kb/cli.py"}]
relations: {"related-to":["kb:repository:decision:markdown-authority","kb:repository:command:test-suite"]}
invalidation: {"paths":["src/**","pyproject.toml"]}
---

## L0 — Pointer

CLI, retrieval, lifecycle, and source-of-truth map.

## L1 — Fact

`cli.py` is the agent entry point; `retrieval.py`/`scoring.py` choose context; Markdown is authoritative; `index.py` is rebuildable.

## L2 — Summary

The package is under `src/autonomic_kb`. `cli.py` exposes the command surface. `config.py`, `git_context.py`, and `security.py` establish current scope and trust. `index.py` parses vault Markdown into a disposable SQLite/FTS cache. `scoring.py` rejects incompatible scope before ranking, and `retrieval.py` allocates progressive layers under a hard budget. `learning.py`, `validation.py`, and `healing.py` implement memory lifecycle. Tests are standard-library `unittest` files under `tests`.

## L3 — Detail

The core must remain usable without Obsidian running. `obsidian.py` is only an optional app-aware adapter. `.kb/index.sqlite3` and `.kb/actions.jsonl` are runtime state, not knowledge. New durable facts enter through `Learner.remember` so economics and security gates run first. Semantic contradictions must remain visible until provenance resolves them.

## L4 — Provenance

- [[decisions/markdown-is-authority]]
- [[commands/test-suite]]
- `README.md`
- `src/autonomic_kb/`
