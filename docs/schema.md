# Knowledge schema v2

Schema v2 keeps hot frontmatter compact while moving large evidence and event histories into content-addressed durable stores.

Required v2 fields are `schema_version`, `id`, `title`, `type`, `kind`, `scope`, `status`, `summary`, `confidence`, `authority`, and `updated`. The canonical machine schema is `schema/memory.schema.json` (JSON Schema 2020-12). Legacy v1 notes remain readable and can be migrated explicitly.

## Memory families

- episodic
- semantic
- procedural
- decision
- constraint
- failure
- negative
- summary
- instruction

Types specialize lifecycle/validation behavior (`command`, `workflow`, `known-failure`, `negative-result`, `agent-instruction`, etc.).

## Scope and identity

`global -> user -> repository -> project -> module -> branch -> task -> session` is an applicability lattice, not simple inheritance. Repository-scoped knowledge may carry a canonical `repository_id`; narrow scopes require their active context.

## Temporal validity

```yaml
validity:
  valid_from: 2026-08-01T00:00:00Z
  valid_to: null
  as_of_commit: abc123
  version_range: ">=1.2,<2"
```

Valid time is separate from when the KB first recorded or last validated the memory. Disjoint temporal versions are not automatically treated as contradictions.

## Evidence and provenance

`evidence` stores content-addressed evidence IDs. `provenance` describes source derivation. `taint` propagates source trust. Privileged `agent-instruction` memories additionally require explicit authorization and trusted provenance.

## Progressive views

L0 pointer, L1 direct fact, L2 common-path summary, L3 detail and L4 provenance remain human-readable in Markdown. Retrieval may compile a smaller task-specific view from them.
