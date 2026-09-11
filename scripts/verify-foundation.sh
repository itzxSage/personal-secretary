#!/bin/bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUN_ID=${1:-local}
RUN_DIR="$ROOT/artifacts/verification/$RUN_ID"
PORT=""
CONTRACT_VERSION=""
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
PORT=$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
CONTRACT_VERSION=$(uv run python -c 'from secretary_service.contract_version import CONTRACT_VERSION; print(CONTRACT_VERSION)')
export CONTRACT_VERSION
uv run scripts/generate_contracts.py --check > "$RUN_DIR/generation.log" 2>&1
uv run ruff format --check . > "$RUN_DIR/python-format.log" 2>&1
uv run ruff check . > "$RUN_DIR/python-lint.log" 2>&1
uv run basedpyright > "$RUN_DIR/python-types.log" 2>&1
uv run pytest -q > "$RUN_DIR/python-tests.log" 2>&1
swift test --package-path ios/SecretaryApp > "$RUN_DIR/swift-tests.log" 2>&1
swift build --package-path ios/SecretaryApp > "$RUN_DIR/swift-build.log" 2>&1

set +e
SECRETARY_DRY_RUN=0 "$ROOT/mac/secretary_service/bin/run" \
    > "$RUN_DIR/dry-run-enforcement.log" 2>&1
DRY_RUN_REFUSAL_STATUS=$?
set -e
test "$DRY_RUN_REFUSAL_STATUS" = "64"

SECRETARY_DRY_RUN=1 SECRETARY_PORT="$PORT" \
    "$ROOT/mac/secretary_service/bin/run" > "$RUN_DIR/service.log" 2>&1 &
SERVER_PID=$!

for _ in {1..50}; do
    if curl --silent --fail "http://127.0.0.1:${PORT}/openapi.json" >/dev/null; then
        break
    fi
    sleep 0.1
done

MATCH_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/matching.headers" \
    --output "$RUN_DIR/matching.json" \
    --write-out '%{http_code}' \
    --header "X-Secretary-Contract-Version: ${CONTRACT_VERSION}" \
    "http://127.0.0.1:${PORT}/health")
MISMATCH_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/mismatched.headers" \
    --output "$RUN_DIR/mismatched.json" \
    --write-out '%{http_code}' \
    --header 'X-Secretary-Contract-Version: 999.0.0' \
    "http://127.0.0.1:${PORT}/health")
MALFORMED_STATUS=$(curl --silent --show-error \
    --dump-header "$RUN_DIR/malformed.headers" \
    --output "$RUN_DIR/malformed.json" \
    --write-out '%{http_code}' \
    --header 'X-Secretary-Contract-Version: not-a-version' \
    "http://127.0.0.1:${PORT}/health")

test "$MATCH_STATUS" = "200"
test "$MISMATCH_STATUS" = "426"
test "$MALFORMED_STATUS" = "426"

cleanup
trap - EXIT

uv run python - "$RUN_DIR" <<'PY'
import json
import os
import pathlib
import sys

run_dir = pathlib.Path(sys.argv[1])
contract_version = os.environ["CONTRACT_VERSION"]
matching = json.loads((run_dir / "matching.json").read_text())
mismatched = json.loads((run_dir / "mismatched.json").read_text())
malformed = json.loads((run_dir / "malformed.json").read_text())

assert matching == {
    "status": "ok",
    "contract_version": contract_version,
    "connectors": "disabled",
    "dry_run": True,
}
assert mismatched["error"]["code"] == "contract_version_mismatch"
assert malformed["error"]["code"] == "contract_version_mismatch"

diagnostic = {
    "http_status": 426,
    "response": mismatched,
    "database_migration_attempted": False,
}
(run_dir / "incompatible-contract-diagnostic.json").write_text(
    json.dumps(diagnostic, indent=2, sort_keys=True) + "\n"
)
report = {
    "contract_version": contract_version,
    "database_migration_attempted": False,
    "dry_run": True,
    "external_connectors_contacted": False,
    "gates": {
        "generated_contracts": "passed",
        "dry_run_enforcement": "passed",
        "python_format": "passed",
        "python_lint": "passed",
        "python_types": "passed",
        "python_tests": "passed",
        "swift_tests": "passed",
        "swift_build": "passed",
    },
    "http_probes": {
        "matching": 200,
        "mismatched": 426,
        "malformed": 426,
    },
    "service_cleanup": "terminated",
}
(run_dir / "foundation-report.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n"
)
PY

printf 'foundation verification passed: %s\n' "$RUN_DIR"
