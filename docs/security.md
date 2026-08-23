# Security model v2

Persistent memory is a capability boundary. Similarity never grants authority.

## Independent controls

- scope and canonical repository identity before ranking;
- valid-time and commit-lineage applicability;
- authority separate from confidence;
- provenance taint propagated through derived memory;
- explicit authorization for privileged `agent-instruction` memories;
- secret and prompt-injection scanning;
- content-addressed evidence verification;
- repository-contained, allowlisted, argv-only executable validators;
- quarantine/conflict/retraction hard gates;
- backup, operation ledger and rollback-safe healing.

Ordinary repository text, issues, agent observations or external content cannot create trusted executable instructions merely by being summarized. A model classifier may be added as defense in depth, but it must not be the authorization mechanism.

Adversarial regression tests cover injection persistence, hostile taint, cross-repository canaries, forged evidence, validator path escape/shell syntax and memory flooding. See `adversarial-testing.md`.
