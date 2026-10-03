#!/usr/bin/env bash
# Build the wheel, install it into a brand-new virtualenv, and prove it works with the source tree out of the way:
#   patchquest doctor, a deterministic evaluation task, and a bundled-data check (the corpus ships in the wheel).
# Usage: scripts/clean_install_check.sh   (needs python3 and network access for dependencies)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 -m venv "$WORK/build-venv"
"$WORK/build-venv/bin/pip" install --quiet build
"$WORK/build-venv/bin/python" -m build --wheel --outdir "$WORK/dist" "$ROOT/backend" >/dev/null
WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "built: $(basename "$WHEEL")"

python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet "$WHEEL"

# Run from an empty directory with a scrubbed PYTHONPATH so nothing can import from the checkout.
mkdir "$WORK/empty" && cd "$WORK/empty"
unset PYTHONPATH
export HOME="$WORK/home" PATCHQUEST_DB="$WORK/pq.db"
mkdir -p "$HOME"
PQ="$WORK/venv/bin/patchquest"

"$WORK/venv/bin/python" - <<'PY'
import patchquest, pathlib
where = pathlib.Path(patchquest.__file__).resolve()
assert "site-packages" in where.parts, f"imported from the source tree: {where}"
print("imported from", where.parent)
PY

"$PQ" doctor --json > "$WORK/doctor.json" || true   # doctor may warn about optional tools; it must not crash
python3 - "$WORK/doctor.json" <<'PY'
import json, sys
checks = json.load(open(sys.argv[1]))
assert isinstance(checks, (list, dict)) and checks, "doctor produced no checks"
print("doctor ran:", len(checks if isinstance(checks, list) else checks.get("checks", checks)), "checks")
PY

"$PQ" eval list --json | python3 -c "import sys, json; n = len(json.load(sys.stdin)); assert n == 14, n; print('bundled corpus:', n, 'tasks')"
"$PQ" eval run --provider scripted --filter bugfix-leap-year --json | python3 -c "
import sys, json
for line in sys.stdin:
    if line.startswith('{'):
        d = json.loads(line)
overall = d['summary']['overall']
assert overall['success'] == overall['tasks'] == 1, overall
print('deterministic task: success')"
"$PQ" admin init --org Smoke --workspace main --owner you --json >/dev/null && echo "migrations + identity: ok"
"$PQ" backup create "$WORK/backup.db" >/dev/null && "$PQ" backup verify "$WORK/backup.db" >/dev/null && echo "backup + verify: ok"
echo "CLEAN INSTALL OK"
