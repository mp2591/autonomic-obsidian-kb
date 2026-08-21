#!/usr/bin/env bash
set -Eeuo pipefail

: "${OBSIDIAN_VERSION:?OBSIDIAN_VERSION must be set}"

WORKSPACE="${WORKSPACE:-/workspace}"
ARTIFACTS="${ARTIFACTS:-/artifacts}"
VAULT_NAME="${OBSIDIAN_VAULT_NAME:-autonomic-kb-ci}"
VAULT="${OBSIDIAN_VAULT_PATH:-/tmp/autonomic-kb-obsidian-vault}"
APP="/opt/obsidian/AppRun"
APP_LOG="$ARTIFACTS/obsidian-app.log"
STARTUP_LOG="$ARTIFACTS/obsidian-startup.json"

mkdir -p "$ARTIFACTS" "$HOME" "$XDG_CONFIG_HOME/obsidian" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"
unset DISPLAY WAYLAND_DISPLAY

if [[ ! -d "$WORKSPACE/src/autonomic_kb" ]]; then
  echo "Repository source is not mounted at $WORKSPACE" >&2
  exit 2
fi

# Re-run the complete dependency-free suite inside the same Linux image that
# hosts Obsidian. This catches environmental assumptions before app startup.
(
  cd "$WORKSPACE"
  PYTHONPATH=src python3 -m unittest discover -s tests -v
) 2>&1 | tee "$ARTIFACTS/container-unit-tests.log"

rm -rf "$VAULT"
mkdir -p "$VAULT" "$VAULT/generated"
cp -a "$WORKSPACE/tests/fixtures/obsidian-vault/." "$VAULT/"

python3 - "$VAULT" "$VAULT_NAME" "$XDG_CONFIG_HOME/obsidian/obsidian.json" <<'PY'
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

vault = Path(sys.argv[1]).resolve()
vault_name = sys.argv[2]
config_path = Path(sys.argv[3])
config_path.parent.mkdir(parents=True, exist_ok=True)
config = {
    "vaults": {
        vault_name: {
            "path": str(vault),
            "ts": int(time.time() * 1000),
            "open": True,
        }
    },
    "cli": True,
}
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

cp "$XDG_CONFIG_HOME/obsidian/obsidian.json" "$ARTIFACTS/obsidian-config.json"

APP_ARGS=(
  --no-sandbox
  --ozone-platform=headless
  --disable-gpu
  --disable-dev-shm-usage
  --enable-logging=stderr
)
python3 - "$APP" "$STARTUP_LOG" "${APP_ARGS[@]}" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

app, output, *arguments = sys.argv[1:]
Path(output).write_text(
    json.dumps(
        {
            "app": app,
            "args": arguments,
            "display": os.environ.get("DISPLAY", ""),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

"$APP" "${APP_ARGS[@]}" >"$APP_LOG" 2>&1 &
APP_PID=$!
echo "$APP_PID" > "$ARTIFACTS/obsidian-app.pid"

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  ps -ef > "$ARTIFACTS/processes-final.txt" 2>&1 || true
  if kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$APP_PID" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$APP_PID" 2>/dev/null || true
  fi
  wait "$APP_PID" 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

sleep 1
if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "Obsidian exited immediately under Ozone headless mode" >&2
  cat "$APP_LOG" >&2 || true
  exit 1
fi

CMDLINE="$(tr '\0' ' ' < "/proc/$APP_PID/cmdline")"
printf '%s\n' "$CMDLINE" > "$ARTIFACTS/obsidian-process-command.txt"
if [[ "$CMDLINE" != *"--ozone-platform=headless"* ]]; then
  echo "The running Obsidian process is missing --ozone-platform=headless: $CMDLINE" >&2
  exit 1
fi
if [[ -n "${DISPLAY-}" || -n "${WAYLAND_DISPLAY-}" ]]; then
  echo "A display server leaked into the real-Obsidian test" >&2
  exit 1
fi

READY=0
for attempt in $(seq 1 120); do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "Obsidian terminated before the CLI became ready" >&2
    cat "$APP_LOG" >&2 || true
    exit 1
  fi
  if output="$(obsidian "vault=$VAULT_NAME" vault info=path 2>"$ARTIFACTS/obsidian-readiness.stderr")" \
      && [[ "$output" == *"$VAULT"* ]]; then
    printf '%s\n' "$output" > "$ARTIFACTS/obsidian-readiness.stdout"
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  echo "Official Obsidian CLI never reached the configured vault" >&2
  cat "$ARTIFACTS/obsidian-readiness.stderr" >&2 || true
  cat "$APP_LOG" >&2 || true
  exit 1
fi

obsidian version > "$ARTIFACTS/obsidian-version.txt" 2> "$ARTIFACTS/obsidian-version.stderr"
obsidian help > "$ARTIFACTS/obsidian-help.txt" 2> "$ARTIFACTS/obsidian-help.stderr"
cp "$XDG_CONFIG_HOME/obsidian/obsidian.json" "$ARTIFACTS/obsidian-config-after-start.json"
python3 - "$VAULT" "$VAULT_NAME" "$XDG_CONFIG_HOME/obsidian/obsidian.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

vault = Path(sys.argv[1]).resolve()
vault_id = sys.argv[2]
config = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
if config.get("cli") is not True:
    raise SystemExit(f"Obsidian did not preserve CLI activation: {config!r}")
entry = config.get("vaults", {}).get(vault_id, {})
if Path(str(entry.get("path", ""))).resolve() != vault:
    raise SystemExit(f"Obsidian did not preserve the registered vault: {config!r}")
PY
ps -ef > "$ARTIFACTS/processes-ready.txt"

cd "$WORKSPACE"
PYTHONPATH=src python3 scripts/test_obsidian_ci.py \
  --vault "$VAULT" \
  --vault-name "$VAULT_NAME" \
  --obsidian /usr/local/bin/obsidian \
  --expected-version "$OBSIDIAN_VERSION" \
  --artifacts "$ARTIFACTS"

obsidian "vault=$VAULT_NAME" dev:errors > "$ARTIFACTS/obsidian-dev-errors.txt" 2>&1 || true
obsidian "vault=$VAULT_NAME" dev:console level=error > "$ARTIFACTS/obsidian-console-errors.txt" 2>&1 || true

# The app must still be alive after all CLI traffic; otherwise a successful
# command could hide a late Electron crash.
if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "Obsidian exited during integration testing" >&2
  cat "$APP_LOG" >&2 || true
  exit 1
fi
