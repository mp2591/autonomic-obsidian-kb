# Architecture

## Decision

Use an **Obsidian-first hybrid**:

- Obsidian Markdown is authoritative, portable, diffable, and human-editable.
- SQLite is a derived local index and telemetry store that can be deleted and rebuilt.
- Direct filesystem access is the headless baseline.
- The first-party Obsidian CLI is an optional app-aware adapter, never a hard dependency.
- Explicit graph edges augment retrieval only after lexical and scope gates.
- Embeddings and a daemon are disabled until benchmarks justify their continuing cost.

This arrangement optimizes token savings and correctness before feature breadth.

## Components

| Component | Responsibility | Durable? |
|---|---|---|
| Vault Markdown | Knowledge, summaries, provenance, explicit relationships | Yes |
| `kb.toml` | Small policy/configuration surface | Yes |
| `markdown.py` | Parse/render Obsidian-compatible records and L0–L4 layers | No state |
| `index.py` | SQLite metadata, FTS5, links, usage, decisions | Rebuildable |
| `git_context.py` | Branch, diff, repository, and working-directory signals | Ephemeral |
| `scoring.py` | Task classification, scope/trust gates, relevance score | No state |
| `retrieval.py` | Staged candidate selection and token allocation | Decisions logged |
| `learning.py` | Candidate economics, promotion, inbox, quarantine | Writes Markdown |
| `validation.py` | Schema, provenance, link, contradiction, staleness, safety checks | Reports logged |
| `healing.py` | Safe repairs, backups, stale marking, archive/forget | Writes Markdown |
| `obsidian.py` | Capability-probed first-party CLI bridge | No state |
| `mcp_server.py` | Thin agent protocol adapter | No state |

## State boundaries

The vault is recoverable from Git or ordinary backups. `.kb/index.sqlite3` and `.kb/actions.jsonl` must not be treated as knowledge authority. A fresh machine can run `kb index --rebuild` and recover retrieval behavior from Markdown.

Scope and provenance are security boundaries. A repository-scoped note for repository A cannot enter repository B merely because lexical similarity is high. Module and branch memories require an active path or branch match. Conflicted, superseded, archived, quarantined, and low-confidence untrusted notes fail before ranking.

## Retrieval data flow

```text
task + budget
  -> Git/cwd/path context
  -> task-type classifier
  -> FTS/lexical candidate generation
  -> scope gate
  -> trust gate
  -> weighted ranking
  -> bounded one-hop relationship expansion
  -> L0/L1/L2 selection
  -> utility-per-token stop rule
  -> manifest + decisions + usage
```

Graph traversal is deliberately downstream of a strong lexical seed. This avoids paying to explore a broad graph for a narrow task.

## Lifecycle data flow

```text
agent output / Git state / user correction
  -> candidate
  -> secret and prompt-injection scan
  -> reuse/cost/stability/uniqueness score
  -> quarantine | inbox | active note
  -> validation
  -> use telemetry
  -> update / merge / mark stale / archive / delete
```

Autonomous operations preserve provenance and never silently resolve a semantic contradiction.

## Obsidian-only versus hybrid

An Obsidian-only implementation is attractive for simplicity, but app-backed CLI calls require a running desktop process and do not provide the low-level decision telemetry, deterministic score inspection, or headless reliability needed by coding agents. A hybrid index costs local disk and rebuild time but keeps durable knowledge in Markdown while reducing repeated parsing and query tokens.

## Daemon versus CLI

The vertical slice is CLI-first. Reactive indexing runs on retrieval and is fast for project-scale vaults. Optional Git hooks provide low-cost invalidation hints. A daemon becomes justified only when measured index refresh latency or stale-memory exposure materially exceeds daemon maintenance, synchronization, and attack-surface cost.

## Embeddings versus lexical retrieval

Embeddings can recover paraphrases, but they add model downloads, vector storage, update work, nondeterminism, semantic false positives, and additional prompt context. The extension point is intentionally optional. The acceptance condition is not recall improvement by itself; it is positive **net token savings after correction cost** on the benchmark suite.

## Failure modes and containment

- **Index corruption:** delete `.kb/index.sqlite3`; rebuild from Markdown.
- **Obsidian not running:** filesystem mode remains fully functional.
- **FTS5 unavailable:** deterministic `LIKE` fallback is used and `doctor` reports it.
- **Broken source:** memory is marked stale; source facts are not silently rewritten.
- **Contradiction:** both claims remain, retrieval is blocked, and a conflict report is written.
- **Secret/injection candidate:** note enters quarantine and fails the trust gate.
- **Over-budget context:** allocator lowers the disclosure layer or excludes the note.
- **Cross-project match:** scope gate excludes it before relevance ranking.

## Compatibility validation boundary

The headless retrieval path and the Obsidian application adapter are tested separately. Core CI proves that Markdown authority, SQLite acceleration, scope gates, retrieval, and lifecycle operations work without Obsidian. A second release-blocking job runs the actual official Linux desktop application with `--ozone-platform=headless` and exercises the first-party CLI against the same fixture vault. The job verifies bidirectional mutations and compares Obsidian metadata-cache output with the KB parser and index. See [`testing.md`](testing.md).
