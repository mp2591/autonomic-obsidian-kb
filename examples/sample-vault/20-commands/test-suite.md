---
id: kb:repository:command:test-suite
title: Run the complete test suite
type: command
scope: repository
repo: autonomic-obsidian-kb
status: active
summary: Run python -m unittest discover -s tests -v from the repository root after installing the package.
confidence: 0.99
authority: verified
created: 2026-08-21T00:00:00Z
updated: 2026-09-29T00:00:00Z
validated: 2026-09-29T00:00:00Z
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

Run `python -m unittest discover -s tests -v` after `python -m pip install -e '.[dev]'`.

## L2 — Summary

After `python -m pip install -e '.[dev]'` (the package needs `jsonschema`, `PyYAML`, and `packaging`), run `python -m unittest discover -s tests -v`. Before publishing, also run `python -m compileall -q src scripts tests`, `ruff check src scripts tests`, and the benchmark regression gate.

## L3 — Detail

A publication check also runs `kb --vault examples/sample-vault index --rebuild` and `python benchmarks/run.py`, which fails on regressions against `benchmarks/results/reference.json`.
