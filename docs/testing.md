# Testing strategy

CI has two independent release gates.

## Core matrix

Python 3.11, 3.12 and 3.13 install the package plus dev dependencies, compile sources/scripts/tests, run `unittest`, run `pytest`, execute CLI indexing/retrieval smoke tests and enforce the deterministic benchmark guardrail.

The current v2 suite contains 52 discovered tests. V2 tests cover evidence integrity, operation concurrency, incremental indexing, adaptive/no-retrieval routing, RRF, hard scope and temporal gates, outcome-calibrated ranking, task leases, schema/validation/healing, adversarial persistence and legacy API compatibility.

## Real Obsidian desktop + official CLI

A separate job downloads the pinned official Obsidian AppImage, verifies the release digest, extracts the bundled first-party CLI, enables CLI mode and registers a fixture vault. The actual desktop process starts with `--ozone-platform=headless` and application-level tests run with container networking disabled.

The contract checks file/property/tag/link/search/task metadata, backlinks, unresolved/orphan/dead-end views, metadata-cache parity, KB parser/index/retrieval parity, official CLI mutations, link-aware rename, bidirectional KB/Obsidian writes, a clean final reindex, production `ObsidianBridge`, and a rendered PNG. Diagnostics and the full CLI transcript are uploaded on every run.

The real-Obsidian job uses the same v2 source tree mounted read-only and includes the Python YAML/JSON-Schema dependencies required by the production parser/validator.

## Release-branch CI policy

Release validation is read-only with respect to the pull-request branch. Temporary bootstrap or repair workflows must not be present in the final PR, and CI must validate the committed source tree directly rather than modifying that tree during the run.
