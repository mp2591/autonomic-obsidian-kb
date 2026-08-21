---
id: kb:repository:agent-instruction:minimal-context
title: Agent context consumption contract
type: agent-instruction
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: Consume the context manifest first and expand a full note only when the selected layer is insufficient for the concrete task.
confidence: 0.98
authority: user-corrected
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 180
utility: 0.96
applies_to: ["**"]
agents: ["codex","claude-code","gemini-cli","generic"]
provenance: [{"kind":"project-policy","path":"AGENTS.md"}]
relations: {"related-to":["kb:repository:architecture:retrieval-pipeline"]}
invalidation: {"paths":["AGENTS.md","README.md"]}
---

## L0 — Pointer

Use the manifest; expand only on demonstrated need.

## L1 — Fact

Do not retrieve compact context and then immediately read every linked source file.

## L2 — Summary

Start with L0/L1/L2. Expand L3 for edge cases and L4 for high-risk provenance. If the manifest is irrelevant, report the exclusion/retrieval failure so ranking can improve rather than broadening every future prompt.
