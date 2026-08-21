# autonomic-obsidian-kb

**A local-first, Obsidian-centric knowledge infrastructure layer that minimizes the total token cost of AI-agent work.**

This is not a generic note application, a chat-history store, or a “retrieve the top-k chunks” demonstration. Its optimization target is the smallest amount of correct, high-value knowledge that prevents an agent from searching, rereading, debugging, or reasoning through the same material again.

```text
total task cost = retrieval tokens
                + injected-context tokens
                + search/discovery tokens
                + repeated-reasoning tokens
                + correction/recovery tokens
                + amortized maintenance cost
```

Markdown files in an Obsidian vault are the durable source of truth. SQLite FTS5 is a disposable acceleration layer. The first-party Obsidian CLI is an optional bridge for app-aware features; direct filesystem operation keeps the core usable by headless Codex, Claude Code, Gemini CLI, CI, and ordinary shell agents.

## What is implemented

- A zero-runtime-dependency Python CLI with the requested `kb` command surface.
- Deterministic Obsidian Markdown parsing and a compact frontmatter schema.
- A rebuildable SQLite index with FTS5 and a lexical fallback.
- Scope, trust, branch, repository, module, and path gates before ranking.
- Task classification, bounded graph expansion, recency, authority, confidence, validation, usage, and utility-per-token ranking.
- L0–L4 progressive disclosure with hard token-budget allocation and a marginal-value stop rule.
- Candidate-memory economics, promotion, inbox routing, deduplication, and security quarantine.
- Validation for malformed metadata, duplicate IDs, contradictions, broken links, obsolete paths, stale sources, expiration, secrets, and persistent prompt injection.
- Conservative healing with backups; ambiguous contradictions are reported rather than overwritten.
- Compaction and soft-forgetting through human-visible Obsidian archive notes.
- Explainability and observability through `why`, `status`, `stats`, `doctor`, SQLite decisions, and JSONL events.
- Optional first-party Obsidian CLI capability probing and a dependency-free MCP stdio server.
- A sample vault, regression tests, deterministic proxy benchmarks, Git hooks, and agent integration examples.

## Quick start

Python 3.11 or newer is required.

```bash
git clone https://github.com/mp2591/autonomic-obsidian-kb.git
cd autonomic-obsidian-kb
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install -e .

kb init ~/Knowledge/agent-memory
export KB_VAULT=~/Knowledge/agent-memory
kb doctor
```

Index an existing Obsidian vault and retrieve only what a task can justify:

```bash
kb --vault /path/to/vault --repo /path/to/project index
kb --vault /path/to/vault --repo /path/to/project \
  retrieve "Fix the SQLite lock failure in the indexing pipeline" \
  --path src/autonomic_kb/index.py --budget 600
```

The default output is an agent-readable context manifest. JSON is available for wrappers:

```bash
kb --json --vault "$KB_VAULT" retrieve "run the test suite" --budget 300
```

## CLI

```text
kb init [PATH]
kb index [--rebuild]
kb context [--budget N]
kb retrieve TASK [--budget N] [--depth auto|0|1|2|3] [--path PATH ...]
kb remember --title TITLE --summary FACT [--detail TEXT] [--type TYPE] [--scope SCOPE]
kb learn --file candidates.jsonl | --git
kb validate
kb heal [--apply]
kb compact [--apply]
kb status
kb stats
kb graph [--format json|dot|mermaid]
kb scope [--path PATH ...]
kb why ID
kb forget ID [--hard --yes]
kb doctor
kb benchmark --tasks benchmarks/tasks.json
kb obsidian [--capabilities]
kb mcp
```

Destructive or ambiguous operations are not autonomous by default. `heal` and `compact` produce plans unless `--apply` is supplied. `forget` archives by default. Contradictions and duplicate canonical IDs require provenance-based resolution.

## Minimal record format

The required hot-path metadata is intentionally small:

```markdown
---
id: kb:repository:command:test-suite
title: Test suite command
type: command
scope: repository
status: active
summary: Run the complete suite with python -m unittest discover -s tests -v.
confidence: 0.98
authority: verified
updated: 2026-08-21T00:00:00Z
applies_to: ["src/**", "tests/**"]
provenance: [{"kind":"file","path":"pyproject.toml"}]
invalidation: {"paths":["pyproject.toml","tests/**"]}
---

## L0 — Pointer

Test command.

## L1 — Fact

Run `python -m unittest discover -s tests -v`.

## L2 — Summary

The suite is dependency-free and uses the standard-library test runner.
```

The fenced block is documentation rather than an indexed memory. See [`docs/schema.md`](docs/schema.md) and [`schema/memory.schema.json`](schema/memory.schema.json).

## Retrieval is staged, not top-k dumping

1. Inspect repository, branch, changed paths, requested paths, agent, and session.
2. Classify the task and query the lexical index.
3. Reject incompatible scopes and unsafe or conflicted memories.
4. Rank accepted candidates by relevance, applicability, trust, freshness, and expected utility per token.
5. Expand at most one graph hop from only the strongest seeds.
6. Start at L0/L1; spend on L2 only when relevance justifies it.
7. Stop when the next candidate’s marginal value is below its token cost.
8. Record selection and exclusion reasons for `kb why`.

This means an irrelevant high-recall result is treated as a cost, not a success.

## Architecture

```text
Agent / shell / MCP
        |
        v
   kb task context ---- Git branch, diff, cwd, requested paths
        |
        v
scope + trust gates ---- prevent cross-project contamination and unsafe persistence
        |
        v
hybrid candidate set --- SQLite FTS5 + metadata + bounded explicit graph edges
        |
        v
utility/token ranking -- confidence, authority, freshness, validation, use history
        |
        v
L0/L1/L2 allocator ---- hard budget + marginal-value stopping
        |
        v
context manifest ------ smallest sufficient agent representation

Obsidian Markdown <---- durable human-readable knowledge and provenance
        |
        +---- disposable SQLite index / logs / validation state
        +---- optional first-party Obsidian CLI bridge (desktop app running)
```

Why not embeddings by default? For a small or medium project vault, metadata and lexical signals are cheaper, deterministic, inspectable, and usually sufficient. Embeddings become an opt-in candidate generator only after benchmarks show that missed reuse costs more than embedding/index/query maintenance and false-positive context.

Why not require a daemon? A daemon adds lifecycle, synchronization, and security costs. The implemented CLI is reactive and deterministic. A watcher/daemon is a roadmap option for vaults where measured invalidation latency outweighs those costs.

## Agent integration

Point agents at the vault through `KB_VAULT`, then make retrieval a narrow preflight rather than a large static prompt:

```bash
export KB_VAULT="$HOME/Knowledge/agent-memory"
export KB_REPO="$PWD"
kb --vault "$KB_VAULT" --repo "$KB_REPO" context --budget 450
kb --vault "$KB_VAULT" --repo "$KB_REPO" retrieve "$TASK" --budget 800
```

- Codex: [`AGENTS.md`](AGENTS.md) and [`integrations/codex/AGENTS.md`](integrations/codex/AGENTS.md)
- Claude Code: [`CLAUDE.md`](CLAUDE.md)
- Gemini CLI: [`GEMINI.md`](GEMINI.md)
- Generic shell: [`integrations/shell/kb-context.sh`](integrations/shell/kb-context.sh)
- MCP: [`integrations/mcp/README.md`](integrations/mcp/README.md)
- Git invalidation hooks: [`integrations/git-hooks`](integrations/git-hooks)

Agents do not need to understand vault internals. They call `kb retrieve`, consume the manifest, and expand only when the task demonstrates a need.

## Development and verification

```bash
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m autonomic_kb --vault examples/sample-vault index --rebuild
PYTHONPATH=src python benchmarks/run.py
```

CI runs compilation, unit tests, a CLI smoke test, and the benchmark guardrail on Python 3.11–3.13.

## Documentation map

- [`docs/research/obsidian-cli-ecosystem.md`](docs/research/obsidian-cli-ecosystem.md): current first-party CLI and ecosystem research, headless boundaries, and architecture consequences.
- [`docs/architecture.md`](docs/architecture.md): components, state boundaries, tradeoffs, and failure modes.
- [`docs/token-economics.md`](docs/token-economics.md): objective function, scoring economics, and measurement.
- [`docs/schema.md`](docs/schema.md): minimal frontmatter, optional cold metadata, layers, types, scopes, and relations.
- [`docs/retrieval.md`](docs/retrieval.md): gates, ranking, budget allocation, graph expansion, and explanations.
- [`docs/autonomic-lifecycle.md`](docs/autonomic-lifecycle.md): learning, validation, healing, organization, optimization, and pruning loops.
- [`docs/security.md`](docs/security.md): scope as a security boundary, prompt-injection persistence, provenance, and secret handling.
- [`docs/benchmark.md`](docs/benchmark.md): benchmark design and interpretation.
- [`docs/roadmap.md`](docs/roadmap.md): evidence-gated extensions.

## Guiding rule

A feature belongs only when its measured correctness benefit and avoided rediscovery exceed its retrieval, context, and maintenance cost.

The goal is not maximum memory. The goal is **minimum necessary knowledge delivered at maximum usefulness**.
