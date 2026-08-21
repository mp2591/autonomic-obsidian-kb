---
id: kb:repository:command:test-suite
title: Run the complete test suite
type: command
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: Run PYTHONPATH=src python -m unittest discover -s tests -v from the repository root.
confidence: 0.99
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-08-21T00:00:00Z
validated: 2026-08-21T00:00:00Z
freshness: verified
token_cost: 190
utility: 0.95
applies_to: ["src/**","tests/**","pyproject.toml"]
provenance: [{"kind":"ci","path":".github/workflows/ci.yml"},{"kind":"file","path":"README.md"}]
relations: {"related-to":["kb:repository:repository-map:project-layout"]}
invalidation: {"paths":["tests/**","pyproject.toml",".github/workflows/ci.yml"]}
---

## L0 — Pointer

Test command.

## L1 — Fact

Run `PYTHONPATH=src python -m unittest discover -s tests -v`.

## L2 — Summary

The core test suite uses Python’s standard-library `unittest` runner and needs no downloaded test dependency. Run compilation first with `PYTHONPATH=src python -m compileall -q src tests`; then run the suite and the benchmark guardrail.

## L3 — Detail

A publication check also runs `PYTHONPATH=src python -m autonomic_kb --vault examples/sample-vault index --rebuild` and `PYTHONPATH=src python benchmarks/run.py`.
