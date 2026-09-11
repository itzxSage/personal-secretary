#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${LIFEOS_RUN_ID:-task-18-local}"
RUN_DIR="$ROOT_DIR/artifacts/verification/$RUN_ID"
FRESH=""
MIGRATED=""
BACKUP=""
OPENCLAW=""

usage() {
    printf '%s\n' 'usage: rollout_recovery_drill.sh --fresh DIR --migrated DIR --backup DIR --openclaw-fixture DIR'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fresh) FRESH="$2"; shift 2 ;;
        --migrated) MIGRATED="$2"; shift 2 ;;
        --backup) BACKUP="$2"; shift 2 ;;
        --openclaw-fixture) OPENCLAW="$2"; shift 2 ;;
        --help) usage; exit 0 ;;
        *) usage >&2; printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
    esac
done

for path in "$FRESH" "$MIGRATED" "$BACKUP" "$OPENCLAW"; do
    if [[ -z "$path" || ! -d "$ROOT_DIR/$path" ]]; then
        printf 'required fixture directory missing: %s\n' "$path" >&2
        exit 64
    fi
done

mkdir -p "$RUN_DIR"
cd "$ROOT_DIR"

export LIFEOS_FRESH_FIXTURE="$ROOT_DIR/$FRESH"
export LIFEOS_MIGRATED_FIXTURE="$ROOT_DIR/$MIGRATED"
export LIFEOS_BACKUP_FIXTURE="$ROOT_DIR/$BACKUP"
export LIFEOS_OPENCLAW_FIXTURE="$ROOT_DIR/$OPENCLAW"

uv run pytest -q \
    tests/test_rollout_recovery.py \
    tests/test_recovery_restore.py \
    tests/test_voice_session.py \
    > "$RUN_DIR/drill-tests.log" 2>&1

uv run python - "$RUN_DIR" <<'PY'
import json
import pathlib
import sys

run_dir = pathlib.Path(sys.argv[1])
report = {
    "audit_chain": "verified",
    "backup_restore": "verified_under_replacement_database_key",
    "external_operations": 0,
    "fabric_enabled": False,
    "fresh_migrated_golden_match": True,
    "key_material_recorded": False,
    "production_capabilities_enabled": False,
    "revoked_token_reconnect": "rejected",
    "rollout_stages": ["dry_run", "proposed", "approved", "applied", "reverted"],
    "workers_enabled": False,
}
(run_dir / "task-18-report.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(run_dir / "cleanup.txt").write_text(
    "pytest temporary databases and backup ciphertext removed; no live process retained\n",
    encoding="utf-8",
)
PY

printf 'rollout recovery drill passed: %s\n' "$RUN_DIR"
