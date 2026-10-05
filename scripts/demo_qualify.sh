#!/usr/bin/env bash
# Formal demo qualification: reset -> start -> build a workflow in the browser -> webhook trigger -> live runs -> approval -> metrics ->
# replay -> fork -> tour of the UI -> crash recovery. Everything runs against a throwaway demo directory and simulated GitHub/Slack.
#   scripts/demo_qualify.sh [out-dir]       (needs backend/.venv, frontend/node_modules, the built UI and a Playwright Chromium)
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-/tmp/pq-qualify}; PQ=${PQ:-/tmp/pq-demo}; PORT=${PORT:-8765}; BASE=http://127.0.0.1:$PORT
PQBIN=$ROOT/backend/.venv/bin; PY=$PQBIN/python; EVID=$ROOT/scripts/demo_qualify_evidence.py
mkdir -p "$OUT"; : > "$OUT/summary.txt"; FAILED=0
note() { echo "$*" | tee -a "$OUT/summary.txt"; }
stage() { local name=$1; shift; local log="$OUT/$name.log"; if "$@" > "$log" 2>&1; then note "PASS  $name"; else note "FAIL  $name   (see $log)"; FAILED=1; fi; }
wait_http() { for _ in $(seq 1 60); do curl -sf "$BASE/api/health" >/dev/null && return 0; sleep 1; done; return 1; }

note "# demo qualification $(date -u +%FT%TZ)  head $(git -C "$ROOT" rev-parse --short HEAD)"
pkill -f "patchquest demo start --dir $PQ" 2>/dev/null; sleep 1
stage 01-reset      "$PQBIN/patchquest" demo reset --dir "$PQ"
( "$PQBIN/patchquest" demo start --dir "$PQ" --port "$PORT" > "$OUT/server.log" 2>&1 & echo $! > "$OUT/server.pid" )
stage 02-start      wait_http
stage 03-seeded     "$PY" "$EVID" "$BASE" "$PQ" seed
cd "$ROOT/frontend" || exit 2
stage 04-builder    node scripts/qualify_builder.mjs "$BASE" "$OUT/shots-builder"
stage 05-trigger    "$PQBIN/patchquest" demo trigger --dir "$PQ" --port "$PORT"
stage 06-live-wait  node scripts/qualify_live.mjs "$BASE" "$OUT/shots-live" wait
stage 07-nothing-sent-before-approval  bash -c "$PY $EVID $BASE $PQ transcript | tee $OUT/transcript-before.json | $PY -c 'import json,sys; t=json.loads(sys.stdin.read()); sys.exit(0 if t[\"github_comments\"]==0 and t[\"slack_messages\"]==0 else 1)'"
stage 08-approve    node scripts/qualify_live.mjs "$BASE" "$OUT/shots-live" approve
stage 09-side-effects-once-each  bash -c "$PY $EVID $BASE $PQ transcript | tee $OUT/transcript-after.json | $PY -c 'import json,sys; t=json.loads(sys.stdin.read()); sys.exit(0 if t[\"github_comments\"]==1 and t[\"slack_messages\"]==1 else 1)'"
stage 09a-trigger-the-ui-built-workflow  "$PQBIN/patchquest" demo trigger --dir "$PQ" --port "$PORT" --label ui-built
stage 09b-ui-built-run-waits   env TAG=ui node scripts/qualify_live.mjs "$BASE" "$OUT/shots-live" wait
stage 09c-ui-built-approve     env TAG=ui node scripts/qualify_live.mjs "$BASE" "$OUT/shots-live" approve
stage 09d-two-workflows-two-posts-each  bash -c "$PY $EVID $BASE $PQ transcript | tee $OUT/transcript-final.json | $PY -c 'import json,sys; t=json.loads(sys.stdin.read()); sys.exit(0 if t[\"github_comments\"]==2 and t[\"slack_messages\"]==2 else 1)'"
stage 10-replay     "$PY" "$EVID" "$BASE" "$PQ" replay
stage 11-fork       "$PY" "$EVID" "$BASE" "$PQ" fork
stage 12-metrics    "$PY" "$EVID" "$BASE" "$PQ" metrics
stage 13-ui-tour    node scripts/qualify_live.mjs "$BASE" "$OUT/shots-tour" tour
cd "$ROOT/backend" || exit 2
export PATCHQUEST_DB=$PQ/patchquest.db
stage 14-trajectory bash -c "RID=\$(curl -s $BASE/api/runs | $PY -c 'import json,sys; print([r for r in json.load(sys.stdin) if \"Prices like\" in r[\"task\"]][-1][\"id\"])'); $PQBIN/patchquest trajectory \$RID"
stage 15-context-eval "$PQBIN/patchquest" eval context
kill "$(cat "$OUT/server.pid")" 2>/dev/null
unset PATCHQUEST_DB
stage 16-crash-recovery "$PQBIN/patchquest" demo crash
note "FAILED=$FAILED"
exit $FAILED
