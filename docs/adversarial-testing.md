# Adversarial memory testing

The KB treats persistent memory as a capability and contamination boundary. The v2 regression suite includes adversarial cases for cross-repository canaries, hostile provenance taint, unauthorized persistent instructions, indirect prompt-injection language, evidence tampering, validator path escape, shell syntax in executable validators, and memory flooding.

A security regression is release-blocking when untrusted or hostile content can become active privileged instruction, cross a hard repository/scope boundary, bypass evidence verification, or cause an executable validator to escape the configured repository and allowlist.

The suite is intentionally layered: lexical injection patterns are only one signal. Durable provenance taint, instruction authorization, repository identity, status, temporal validity, and evidence digests are independent controls. Retrieval similarity never overrides them.

Future attack corpora should add delayed activation, malicious graph hubs, multi-agent propagation, poisoned near-duplicates, forged signatures, Unicode/encoding evasions, and adaptive model-generated injection variants. These should be measured with attack success, quarantine precision/recall, benign false-positive rate, and persistence after consolidation.
