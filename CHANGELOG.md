# Changelog

## Unreleased

Fixes from the September 2026 repository review. Each defect has a reproduction in `tests/test_review_regressions.py`; defects from the follow-up review are reproduced in `tests/test_second_review.py`.

### Follow-up review

- `[security] allow_privileged_remember = false` now means no self-vouching on any path. Before, it refused only the CLI privileged options, while `kb learn --file` with self-assigned value estimates, consolidation of an episode backed by self-asserted `evaluation` evidence, and a `remember` padded with self-created evidence still produced active memories. With the switch off, every candidate waits in the inbox for `kb promote`, and library `force` no longer bypasses it. The earlier claim that the switch required review "for everything" was not true until this change. Review is mandatory and auditable, not authenticated: reviewer commands are ordinary CLI commands, so restrict agents to MCP to control who reviews.
- Archiving, quarantine, and reviewer moves never overwrite an existing file. A taken name gets a suffix from a hash of the full memory identity, and moves use link-then-unlink. Before, the suffix came from the first characters of the identity or path, which most notes share, so a third note with the same file name silently replaced the second.
- Retrieval no longer walks and hashes the whole repository to add code symbols. The code graph parses only the active paths and caches each file by its stat identity; the whole-repository projection prunes ignored directories and re-parses only changed files. In a 6,700-file repository the first retrieval after an edit fell from 48–51 s to 0.3 s. (The earlier performance figures used a vault with no code repository and did not cover this.)
- Runtime logs rotate at 5 MB and `kb stats` reads only their tail; the embedding cache drops vectors for notes that no longer exist and is written atomically; heal backups, which keep unredacted originals, expire after 30 days. Files set aside under `.kb/rolled-back/` are kept, because one may be the only copy of a note saved during a failed transaction.
- Mermaid graph output is deterministic, and exact-path search treats `%` and `_` literally.
- Design docs no longer describe allowlisted command validators, learned ranking, or feedback/outcomes under `.kb/` as current behavior; `docs/schema.md` states the repository-identity requirement and gives a `validity` example that passes schema validation.

### Breaking

- A repository-, project-, module- or branch-scoped memory with neither `repository_id` nor `repo` is now excluded wherever a repository is active. That includes every memory written by 0.3.0 `kb remember` without `--repo` against a vault outside Git, which was the documented `CLAUDE.md` flow. `kb validate` lists them as `missing-repository-identity`; give each a `repository_id` (or legacy `repo`) or change its scope to `global` or `user`.
- `kb` exits with an error when it cannot find a vault (no `--vault`, no `KB_VAULT`, and no parent directory containing `kb.toml` or `.obsidian`), and `--vault` must name an existing directory. Run `kb init <path>` first.
- Without `--repo` or `KB_REPO`, the scoping repository is the Git worktree of the current directory rather than the one containing the vault. Pass `--repo` explicitly where the working directory is not the project (hooks, schedulers, MCP launchers).
- `kb init` creates its starter note with `global` scope and the identity `kb:global:repository-map:knowledge-base`.
- `kb remember --force`, an elevated `--authority` or `--taint`, and `--authorize-instruction` now require `--reason`; scripts using them must add one.
- `kb lease acquire` and MCP `kb_lease_acquire` refuse the default `generic` agent identity; pass `--agent` or set `KB_AGENT` (the 0.3.0 README example omitted it). A lease taken under `generic` before upgrading can still be released without `--agent`.
- `kb learn --file` ignores caller-supplied value estimates (`reuse_likelihood`, `rediscovery_cost`, `stability`, `uniqueness`, `token_savings`, `maintenance_cost`), and its `validators` no longer raise the promotion score, so imported candidates score like MCP candidates. Imported validators are still written to the note and checked by `kb validate`.
- With `[security] allow_privileged_remember = false`, no candidate from any path is promoted automatically; every one waits in the inbox for `kb promote`. Memories that activated through those routes before upgrading stay active.
- The first write after upgrading removes heal backup sets under `.kb/backups/` older than 30 days. Copy out any you want to keep before upgrading.
- The index gains derived columns and a parser-format bump, so the first command after upgrading re-reads every note and re-verifies evidence once.

### Security and scoping

- The scoping repository comes from `--repo`, then `KB_REPO`, then the Git worktree of the working directory. It is never taken from the vault's location: a vault kept in Git gave every project the vault's identity, and a vault outside Git gave memories no identity, so they leaked into unrelated repositories.
- A repository-, project-, module- or branch-scoped memory without a repository identity is excluded wherever a repository is active, and `kb validate` warns about it. Legacy `repo:` names also match the remote repository name, so checkouts in differently named directories work.
- Secret scanning inspects every string of a structured value. Scanning a JSON dump let escaping hide a secret that started a line, and `kb remember` then persisted it into quarantine, evidence, and the index.
- Keyed secrets are recognized after a quoted key, as in dict and JSON renderings. A frontmatter line such as `password: …` passed the read gate and was returned by `kb://memory/<id>`.
- A note declaring an already-indexed identity can no longer take over that identity's index row. Before, the newcomer's content could be served as the canonical memory for one retrieval, and the identity stayed with the newcomer.
- `kb` no longer treats an arbitrary working directory as a vault; without `--vault`, `KB_VAULT`, or a directory containing `kb.toml` or `.obsidian`, it exits with an error instead of creating state directories.

### Durability and lifecycle

- Semantic transactions snapshot notes and operation records as hard links instead of reading, secret-scanning, and journaling the whole vault. One human note containing a credential no longer blocks every write, and journals are removed after commit.
- Every vault walk skips hidden directories and symlinks, so transaction snapshots are never indexed or validated.
- `kb heal --apply` can quarantine a secret-bearing note; the moved note's findings are no longer counted as new errors.
- `kb compact` and `kb forget` run transactionally with atomic writes; compaction sees recorded usage and no longer archives memories that retrieval still delivers.
- New reviewer commands `kb inbox`, `kb promote`, `kb revalidate` (emits `REVALIDATE`), and `kb supersede` (emits `SUPERSEDE`) close the path from inbox and changed sources back to retrievable knowledge. They are CLI-only and require a reason.
- `kb validate` reports `dependency-changed` and `dependency-missing` for memories that retrieval excludes after a source edit, and `kb heal` plans `require-revalidation`.
- `kb remember` validates type, scope, authority, taint, and value ranges before writing, and its result explains the promotion decision (score, threshold, reason).
- Options that let a caller vouch for its own candidate (`--force`, elevated `--authority`/`--taint`, `--authorize-instruction`) are recorded with actor and reason in the ledger, and `[security] allow_privileged_remember = false` disables them.
- `kb merge` and `kb split` retire overlapping or overloaded memories with provenance and emit `MERGE`/`SPLIT`; every documented ledger operation except `NOOP` now has a built-in producer.
- Rollback keeps external in-place edits (a file that still has its recorded inode) and sets aside files created during a failed transaction under `.kb/rolled-back/`, so a failed transaction no longer blocks the vault. `kb reconcile` inspects and resolves journals left by a crash, including the 0.3.0 inline format; journals interrupted while snapshotting are discarded automatically. Reconciliation treats journal contents as untrusted: a recorded path that leaves the vault or its snapshot, or a snapshot symlink, makes it refuse the journal without writing anything.
- Heal redacts secret values when it quarantines a note, keeping the unredacted original only in local backups; `kb init` writes a vault `.gitignore` for local state and `kb doctor` checks it.

### Retrieval

- Only explicit historical intent (`as of <date>`, `previously`, `before version 2.0`, `--at`) routes temporally, and the temporal route keeps the exact/path channel. Before, ordinary words such as "after" or "commit" dropped path-based candidates.
- Path similarity compares whole path components (`index.py` no longer matches `00-index/…`); `./` stripping no longer turns `.github` into `github`.
- The task classifier keeps question words and contractions, so clues such as "where" and "didn't" can match.
- Missing evidence roles are labeled `not recorded` or `not delivered` when the budget allows, and final-budget exclusions are visible in diagnostics.
- When the final payload exceeds the budget, the last memory is re-compiled at a smaller complete layer before it is dropped. Before, a relevant command note could yield empty context even at a 330-token budget because its JSON-escaped L2 did not fit.
- MCP tool failures return `isError` results; unknown tools return JSON-RPC errors; the server reports the package version.

### Performance

Measured on a synthetic 2,000-note vault against 0.3.0:

| Operation | 0.3.0 | Now |
|---|---|---|
| `kb remember` (CLI, wall clock) | 5.6 s | 0.47 s |
| `kb retrieve` (CLI, wall clock, warm) | 1.18 s | 0.40 s |
| Retrieval, in process, warm | 1.14 s | 0.18 s |
| First retrieval, including index build | 4.96 s | 3.62 s |
| MCP `kb://memories` | 2.50 s | 0.15 s |
| `kb retrieve` inside a 6,700-file repository, first call after editing a file | 48–51 s | 0.3 s |

The first four rows use a synthetic vault with no code repository; the last row adds the code-graph cost that retrieval pays inside a real project.

Writes reindex incrementally instead of reparsing the vault; a replaced file is detected by inode. Content-safety verdicts are stored at index time. Evidence checks are cached on file identity including ctime, so warm figures assume an unchanged evidence store. MCP catalog reads verify evidence in one batch. The YAML parser uses libyaml when available.

### Benchmark

`benchmarks/run.py` is now a regression gate against the committed `results/reference.json`: coverage, precision, false context, and injected tokens. It writes results to an untracked `results/latest.json`, and ignores uncommitted working-tree changes, which previously made results differ between developer checkouts and CI. On a clean checkout, retrieval quality on the benchmark is unchanged from 0.3.0.

## 0.3.0 — September 9, 2026

Connect query planning, source applicability, safe read policy, whole-representation compilation, final payload counting, structural sufficiency, context acknowledgements, and action-time source receipts to production entrypoints. Add provenance-bound evidence, applicability-aware deduplication and corroboration, verified procedural candidates, scoped recurrence, and changed-source code-graph invalidation.

Remove all note-defined validator execution. Reject undeclared MCP privilege fields, prevent direct-read policy bypasses, reject secret-bearing metadata before durable writes, and block unsafe state symlinks. Serialize cooperating writers; roll back semantic files and event records together; fail closed on interrupted transactions.

Correct usage normalization and pairing, preserve unknown counters and legacy telemetry, isolate replay arms and require an evaluator, preserve original rank features, prevent shadow learning contamination, and keep learned-policy training/activation disabled in this release. Package the schema and enforce it during indexing. Wait for actual Obsidian metadata readiness and archive exact committed source identities in CI.

See `docs/v3-implementation.md` for behavior, migration, supported scope, and non-guarantees. Regression success does not establish empirical agent-token savings or complete semantic/security correctness.
