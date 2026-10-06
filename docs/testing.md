# Testing strategy

CI has two independent release gates.

## Core matrix

Python 3.11, 3.12 and 3.13 install the package plus dev dependencies, compile sources/scripts/tests, run `unittest`, run `pytest`, execute CLI indexing/retrieval smoke tests and enforce the deterministic benchmark guardrail.

The suite contains 228 discovered tests. They cover evidence integrity, operation concurrency, incremental indexing, adaptive/no-retrieval routing, RRF, hard scope and temporal gates, task leases, schema/validation/healing, adversarial persistence and legacy API compatibility. `tests/test_review_regressions.py` reproduces each defect fixed after the September 2026 review: repository identity leaks, whole-vault secret scanning, escaped-newline and quoted-key secrets, duplicate-identity takeover, compaction ignoring usage, silent source invalidation, transaction rollback fidelity with external in-place edits, crash reconciliation (including a real subprocess crash), quarantine redaction, privileged remember options, merge/split provenance, temporal over-routing, and benchmark determinism. `tests/test_second_review.py` covers the follow-up review: self-promotion routes, no-clobber moves, path-scoped code-graph parsing, log rotation, backup expiry (rolled-back files are kept), deterministic graph output, lease identity including release of a lease taken before upgrading, and literal path search. `tests/test_third_review.py` covers issue #8 with real filesystem operations: rollback after a human edit on top of a KB rewrite (ENOSPC injected at the ledger write) and after an atomic save to an unrelated note, crash reconciliation that restores only KB-written paths (real subprocess crashes, including one right after a write lands and before anything else runs), a write that fails without being reported as a conflict, editor saves racing `forget` on the hard-link and copy paths (atomic, in-place, and through an open descriptor), heal on inbox and retired notes checked through both the MCP catalog and `Retriever.retrieve` (a stale control note proves a note wrongly marked stale would appear in both), idempotent stale marking, heal reporting a conflict its rollback preserved, a save made after the snapshot but before the KB write, a note deleted before the KB removed it, a crash before any write and right after a move lands, a rollback that fails part-way and is finished by reconcile, copies that fail part-way or entirely where hard links are unsupported, rollback with copy-based snapshots, and deterministic two-reviewer interleavings of `supersede`, `merge`, `split`, `revalidate` and `promote` plus the on-disk status check (single process, not independent processes). `tests/test_fourth_review.py` covers issue #10, each case as its own test method: a failed copy (partial write, fsync or copystat error) while an editor saves at the destination, by atomic replacement or in place, during a move and during rollback restoration; refusal on a filesystem with neither hard links nor no-replace renames; an unavailable before-state during reconcile and its retry; recovery bookkeeping failing before and after a capture; reconcile killed by a real subprocess exit right after a capture; retries that keep earlier conflicts and acknowledgements, including a new capture at a freed name; failing to record a note a crashed move left behind; a conflict copy that cannot be placed; and note names of 223 bytes and at the name limit, ASCII and multibyte.

Tests never discover the repository that contains the working directory: `tests/support.make_vault` binds each vault to an empty non-Git directory unless a test supplies one.

The benchmark step fails on regressions against `benchmarks/results/reference.json`; see [benchmark design](benchmark.md).

## Real Obsidian desktop + official CLI

A separate job downloads the pinned official Obsidian AppImage, verifies the release digest, extracts the bundled first-party CLI, enables CLI mode and registers a fixture vault. The actual desktop process starts with `--ozone-platform=headless` and application-level tests run with container networking disabled.

The contract checks file/property/tag/link/search/task metadata, backlinks, unresolved/orphan/dead-end views, metadata-cache parity, KB parser/index/retrieval parity, official CLI mutations, link-aware rename, bidirectional KB/Obsidian writes, a clean final reindex, production `ObsidianBridge`, and a rendered PNG. Diagnostics and the full CLI transcript are uploaded on every run.

The real-Obsidian job uses the same v2 source tree mounted read-only and includes the Python YAML/JSON-Schema dependencies required by the production parser/validator.

## Release-branch CI policy

Release validation is read-only with respect to the pull-request branch. Temporary bootstrap or repair workflows must not be present in the final PR, and CI must validate the committed source tree directly rather than modifying that tree during the run.
