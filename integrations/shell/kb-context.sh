#!/usr/bin/env sh
set -eu
: "${KB_VAULT:?Set KB_VAULT to the Obsidian knowledge vault}"
TASK=${*:-"Orient to the current repository and changed paths"}
exec kb --vault "$KB_VAULT" --repo "$PWD" --agent "${KB_AGENT:-shell}" retrieve "$TASK" --budget "${KB_BUDGET:-700}"
