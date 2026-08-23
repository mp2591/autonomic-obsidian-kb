# Security v2

Persistent memory is a capability boundary.

## Hard controls

- repository/project/module/branch/task/session scope is checked before similarity can authorize context;
- quarantined, archived, retracted and conflicted memories cannot enter ordinary retrieval;
- `agent-instruction` memories require explicit authorization, trusted authority and non-hostile provenance taint;
- content-derived instructions do not gain authority by being summarized by an agent;
- evidence objects are content-addressed and verified when read;
- executable validators use argv, an executable allowlist, repository containment, no shell syntax and a timeout;
- healing uses backups and rolls back if validation errors increase;
- hard deletion remains explicit.

## Taint

Taint levels include trusted, derived, external, agent, untrusted, hostile and unknown. Derivations should preserve the highest-risk contributing taint.

## Detection

The heuristic scanner covers known token/key formats, generic secret assignments, entropy-like assignments, role forgery, instruction override, exfiltration, tool coercion and persistence language. Detection is defense in depth; authorization and least privilege remain primary controls.
