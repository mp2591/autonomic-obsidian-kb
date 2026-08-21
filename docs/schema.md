# Knowledge schema

## Design rule

Keep hot metadata small. Put prose in progressive layers. Put high-cardinality telemetry and rebuildable search data in SQLite.

## Required frontmatter

| Field | Purpose |
|---|---|
| `id` | Stable canonical identity; never inferred after promotion |
| `title` | Human-readable title |
| `type` | Persistence/validation policy class |
| `scope` | Applicability and security boundary |
| `status` | Lifecycle state |
| `summary` | One-sentence L1 fallback |
| `confidence` | 0–1 epistemic confidence |
| `authority` | Provenance authority class |
| `updated` | Last semantic update |

Recommended fields are `created`, `validated`, `freshness`, `repo`, `module`, `branch`, `applies_to`, `provenance`, `relations`, and `invalidation`.

`token_cost` and `utility` may be stored for portability, but the index can recompute them. Usage counts, retrieval decisions, and FTS terms do not belong in frontmatter.

## Progressive layers

- **L0 pointer:** up to roughly 140 characters; enough to decide whether to expand.
- **L1 fact:** one command, invariant, failure signature, or direct answer.
- **L2 summary:** sufficient for the common task path.
- **L3 detail:** rationale, edge cases, procedure, examples.
- **L4 provenance:** raw source pointers, hashes, excerpts, and evidence.

Agents default to L0/L1. Human Obsidian users can read every layer normally.

## Types and persistence

| Type | Persistence and validation |
|---|---|
| `architecture` | durable; validate against code paths and tests |
| `repository-map`, `file-map` | regenerate after structural Git changes |
| `convention` | durable; validate against formatter/config/examples |
| `command`, `workflow` | validate by execution or CI configuration |
| `dependency` | invalidate on lock/package metadata changes |
| `api`, `interface` | validate against schema/source definitions |
| `invariant` | high value; require strong provenance/tests |
| `decision` | durable historical record; supersede, do not rewrite history |
| `known-failure`, `solution` | retain while signature and affected versions apply |
| `environment` | scope narrowly to user/repo/tool/OS |
| `agent-instruction` | trusted sources only; injection scan always |
| `domain`, `terminology` | durable when authoritative |
| `hypothesis` | inbox/task scope; expire or promote after validation |
| `fact` | generic fallback; prefer a more specific type |

## Scope lattice

`global -> user -> repository -> project -> module -> branch -> task -> session`

This is not simply an inheritance hierarchy. Narrow memories require their exact context. A branch memory does not apply merely because its repository matches. A module memory requires a module or applicable-path match. Cross-repository retrieval is disabled by default.

## Authority

- `source-of-truth`: code/config/schema that directly defines behavior;
- `authoritative`: official external or project-owned documentation;
- `verified`: independently checked against a source or test;
- `user-corrected`: explicit user correction;
- `derived`: deterministic inference from sources;
- `agent`: agent-generated and not independently validated;
- `external`: third-party material;
- `untrusted`: retained only for investigation/quarantine.

Confidence and authority are separate. A confident agent statement is not equivalent to source-of-truth evidence.

## Relationships

Only retrieval/maintenance-relevant relationships are stored:

`depends-on`, `implements`, `supersedes`, `contradicts`, `derived-from`, `validates`, `related-to`, `applies-to`, `caused-by`, `fixed-by`, `located-in`, `owned-by`.

Relations are explicit JSON in frontmatter or ordinary Obsidian wikilinks. Graph edges do not override scope or trust gates.

## Invalidation

Example:

```yaml
invalidation: {"paths":["pyproject.toml","src/**"],"source_hashes":{"pyproject.toml":"..."},"branch":false,"expires":"2026-12-01T00:00:00Z"}
```

The system marks a memory stale when a source disappears or changes, a validity period expires, or validation ages beyond policy. It does not rewrite the claim without evidence.

The machine-readable shape is in `schema/memory.schema.json`.
