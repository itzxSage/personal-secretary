#!/bin/bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FIXTURE_ARG="fixtures/tomorrow-plan.json"
SANDBOX_ARG="fixtures/google"
BASE_URL=""
RUN_ID="local"

while [ $# -gt 0 ]; do
    case "$1" in
        --fixture)
            FIXTURE_ARG="$2"
            shift 2
            ;;
        --calendar-sandbox)
            SANDBOX_ARG="$2"
            shift 2
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        *)
            RUN_ID="$1"
            shift
            ;;
    esac
done

case "$FIXTURE_ARG" in
    /*) FIXTURE="$FIXTURE_ARG" ;;
    *) FIXTURE="$ROOT/$FIXTURE_ARG" ;;
esac
case "$SANDBOX_ARG" in
    /*) SANDBOX_DIR="$SANDBOX_ARG" ;;
    *) SANDBOX_DIR="$ROOT/$SANDBOX_ARG" ;;
esac
FIXTURES_DIR=$(dirname "$FIXTURE")
RUN_DIR="$ROOT/artifacts/verification/$RUN_ID"
PORT=""
SERVER_PID=""

mkdir -p "$RUN_DIR"

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID"
        wait "$SERVER_PID" || true
        printf 'terminated pid=%s\n' "$SERVER_PID" > "$RUN_DIR/cleanup.txt"
    else
        printf 'no live service process remained\n' > "$RUN_DIR/cleanup.txt"
    fi
}
trap cleanup EXIT

cd "$ROOT"
test -f "$FIXTURE"
test -f "$SANDBOX_DIR/sandbox.json"

if [ -z "$BASE_URL" ]; then
    PORT=$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
    export SECRETARY_SLICE_FIXTURES="$FIXTURES_DIR"
    uv run uvicorn secretary_service.slice.api:app \
        --host 127.0.0.1 --port "$PORT" > "$RUN_DIR/slice-api.log" 2>&1 &
    SERVER_PID=$!
    BASE_URL="http://127.0.0.1:${PORT}"
fi

for _ in {1..50}; do
    if curl --silent --fail "$BASE_URL/health" >/dev/null; then
        break
    fi
    sleep 0.1
done
if [ -n "$SERVER_PID" ] && ! kill -0 "$SERVER_PID" 2>/dev/null; then
    printf 'slice API failed to start; see %s\n' "$RUN_DIR/slice-api.log" >&2
    exit 1
fi

uv run python - "$RUN_DIR" "$FIXTURE" <<'PY'
import json
import pathlib
import sys

run_dir = pathlib.Path(sys.argv[1])
fixture = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
proposal_id = fixture["interpretations"][0]["proposal_id"]
(run_dir / "capture-request.json").write_text(
    json.dumps(fixture["initial_capture"], indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(run_dir / "approve-request.json").write_text(
    json.dumps(
        {"proposal_id": proposal_id, "approval": fixture["approval"]},
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
forged = dict(fixture["approval"], signature="")
(run_dir / "forged-approve-request.json").write_text(
    json.dumps({"proposal_id": proposal_id, "approval": forged}) + "\n",
    encoding="utf-8",
)
(run_dir / "replan-request.json").write_text(
    json.dumps(fixture["replan"], indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(run_dir / "cleanup-request.json").write_text(
    json.dumps(
        {"proposal_id": proposal_id, "rollback": fixture["rollback"]},
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

CAPTURE_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/capture.headers" \
    --output "$RUN_DIR/capture.json" \
    --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data @"$RUN_DIR/capture-request.json" \
    "$BASE_URL/capture")
test "$CAPTURE_STATUS" = "200"

FORGED_STATUS=$(curl --silent --show-error \
    --output "$RUN_DIR/forged-approve.json" \
    --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data @"$RUN_DIR/forged-approve-request.json" \
    "$BASE_URL/approve")
test "$FORGED_STATUS" = "403"

APPROVE_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/approve.headers" \
    --output "$RUN_DIR/approve.json" \
    --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data @"$RUN_DIR/approve-request.json" \
    "$BASE_URL/approve")
test "$APPROVE_STATUS" = "200"

REPLAN_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/replan.headers" \
    --output "$RUN_DIR/replan.json" \
    --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data @"$RUN_DIR/replan-request.json" \
    "$BASE_URL/replan")
test "$REPLAN_STATUS" = "200"

CLEANUP_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/cleanup.headers" \
    --output "$RUN_DIR/cleanup.json" \
    --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data @"$RUN_DIR/cleanup-request.json" \
    "$BASE_URL/cleanup")
test "$CLEANUP_STATUS" = "200"

cleanup
trap - EXIT

uv run python - "$RUN_DIR" "$FIXTURE" <<'PY'
import json
import pathlib
import sys

run_dir = pathlib.Path(sys.argv[1])
fixture = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
golden = fixture["golden"]

capture = json.loads((run_dir / "capture.json").read_text(encoding="utf-8"))
approve = json.loads((run_dir / "approve.json").read_text(encoding="utf-8"))
replan = json.loads((run_dir / "replan.json").read_text(encoding="utf-8"))
cleanup = json.loads((run_dir / "cleanup.json").read_text(encoding="utf-8"))

assert capture == golden["preview"], "capture response differs from golden preview"
assert approve == golden["apply"], "approve response differs from golden apply"
assert replan == golden["replan"], "replan response differs from golden replan"
assert cleanup == golden["cleanup"], "cleanup response differs from golden cleanup"

report = {
    "slice": "tomorrow-planning",
    "gates": {
        "capture_preview": "passed",
        "approve_apply": "passed",
        "forged_known_device_approval": "rejected_403",
        "replan_preserve": "passed",
        "cleanup_rollback": "passed",
    },
    "http_statuses": {
        "capture": 200,
        "approve": 200,
        "replan": 200,
        "cleanup": 200,
    },
}
(run_dir / "tomorrow-plan-report.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

printf 'tomorrow-plan E2E passed: %s\n' "$RUN_DIR"
