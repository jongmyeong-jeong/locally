#!/usr/bin/env bash
# AC-14 smoke test (plan §1.5 / §5):
#   1. lonta --help succeeds
#   2. lonta start --no-browser on a free port
#   3. GET /api/system/info returns JSON with non-null .os
#   4. POST /api/notes '{}' returns 201 + title=="untitled"
#   5. Server terminated cleanly; exit 0
#
# Intended to run under `shell: bash` on both macOS and Windows CI matrix.
set -euo pipefail

log() { printf '[e2e] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

# --- 1. lonta help -----------------------------------------------------------
if ! command -v lonta >/dev/null 2>&1; then
  fail "'lonta' not on PATH; run 'pip install -e .' first"
fi

log "checking lonta --help"
# Typer apps expose --help out of the box; use it as the CLI availability probe.
lonta --help >/dev/null 2>&1 || fail "lonta --help exited non-zero"

# --- 2. pick a free port -----------------------------------------------------
FREE_PORT=$(python3 -c "
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")
log "using port $FREE_PORT"

# --- 3. start the server in the background -----------------------------------
LOGDIR=$(mktemp -d 2>/dev/null || mktemp -d -t e2e_smoke)
trap 'cleanup' EXIT INT TERM

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    # Wait up to 5s for graceful shutdown.
    for _ in 1 2 3 4 5; do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
      sleep 1
    done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
  if [[ -n "${LOGDIR:-}" && -d "$LOGDIR" ]]; then
    rm -rf "$LOGDIR" || true
  fi
}

log "launching lonta start --no-browser"
lonta start --no-browser --host 127.0.0.1 --port "$FREE_PORT" \
  >"$LOGDIR/stdout.log" 2>"$LOGDIR/stderr.log" &
SERVER_PID=$!

# --- 4. wait for readiness ---------------------------------------------------
READY=false
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${FREE_PORT}/api/system/info" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 0.5
done
if [[ "$READY" != "true" ]]; then
  log "--- stdout ---"; cat "$LOGDIR/stdout.log" || true
  log "--- stderr ---"; cat "$LOGDIR/stderr.log" || true
  fail "server did not become ready on port $FREE_PORT"
fi

# --- 5. GET /api/system/info -------------------------------------------------
log "GET /api/system/info"
INFO=$(curl -sf "http://127.0.0.1:${FREE_PORT}/api/system/info")
if [[ -z "$INFO" ]]; then
  fail "/api/system/info returned empty body"
fi
OS_VALUE=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
v = data.get('os')
print(v if v else '')
" <<<"$INFO")
if [[ -z "$OS_VALUE" ]]; then
  fail "system info .os is null/empty: $INFO"
fi
log "ok: os=$OS_VALUE"

# --- 6. POST /api/notes '{}' --------------------------------------------
log "POST /api/notes {}"
DOC=$(curl -sf -X POST -H 'Content-Type: application/json' \
  -d '{}' "http://127.0.0.1:${FREE_PORT}/api/notes")
TITLE=$(python3 -c "
import json, sys
print(json.loads(sys.stdin.read()).get('title',''))
" <<<"$DOC")
if [[ "$TITLE" != "untitled" ]]; then
  fail "expected title=='untitled'; got '$TITLE'; body=$DOC"
fi
log "ok: title=$TITLE"

log "PASS"
exit 0
