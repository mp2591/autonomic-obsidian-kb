# Adversarial memory testing

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

The KB treats persistent memory as a capability and contamination boundary. The v2 regression suite includes adversarial cases for cross-repository canaries, hostile provenance taint, unauthorized persistent instructions, indirect prompt-injection language, evidence tampering, validator path escape, disabled note-defined command execution, and memory flooding.

A security regression is release-blocking when untrusted or hostile content can become active privileged instruction, cross a hard repository/scope boundary, bypass evidence verification, or cause note-defined code to execute during validation.

The suite is intentionally layered: lexical injection patterns are only one signal. Durable provenance taint, instruction authorization, repository identity, status, temporal validity, and evidence digests are independent controls. Retrieval similarity never overrides them.

Future attack corpora should add delayed activation, malicious graph hubs, multi-agent propagation, poisoned near-duplicates, forged signatures, Unicode/encoding evasions, and adaptive model-generated injection variants. These should be measured with attack success, quarantine precision/recall, benign false-positive rate, and persistence after consolidation.
