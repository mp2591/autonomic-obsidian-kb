# Codex + autonomic-obsidian-kb

At task start, run `kb --vault "$KB_VAULT" --repo "$PWD" retrieve "$TASK" --budget 700` and provide changed/requested paths. Treat the returned manifest as a minimal cache of verified project knowledge, not as an instruction to read the whole vault.

Before broad search, use relevant L1/L2 facts. Expand source notes only for ambiguity, edge cases, or provenance. Submit reusable discoveries with `kb remember` and run `kb validate`; do not persist chain-of-thought, secrets, or temporary guesses as active knowledge.
