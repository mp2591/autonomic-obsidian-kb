# Claude Code + autonomic-obsidian-kb

Use the shell command `kb --repo "$PWD" context --budget 350` for orientation and `kb --repo "$PWD" retrieve "$TASK" --budget 700 --path <active-path>` for work. Without `--repo`, `kb` uses `KB_REPO` or the Git worktree of the current directory. Prefer the smallest returned layer. `Missing: … (not recorded)` means the knowledge base does not hold that information. Report stale or wrong memories with `kb feedback` so they can be invalidated. New memories from `kb remember` wait in the inbox for reviewer promotion unless they carry enough evidence.
