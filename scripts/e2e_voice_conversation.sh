#!/bin/bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IOS_SIMULATOR=""
AUDIO_ARG="fixtures/voice/rest-of-day.pcm"
RELAY_ARG="fixtures/realtime"
ADAPTER_ARG="fixtures/openclaw"
RUN_ID="task-13-local"

while [ $# -gt 0 ]; do
    case "$1" in
        --ios-simulator)
            IOS_SIMULATOR="$2"
            shift 2
            ;;
        --audio-fixture)
            AUDIO_ARG="$2"
            shift 2
            ;;
        --relay-fixture)
            RELAY_ARG="$2"
            shift 2
            ;;
        --adapter-fixture)
            ADAPTER_ARG="$2"
            shift 2
            ;;
        *)
            RUN_ID="$1"
            shift
            ;;
    esac
done

case "$AUDIO_ARG" in
    /*) AUDIO="$AUDIO_ARG" ;;
    *) AUDIO="$ROOT/$AUDIO_ARG" ;;
esac
case "$RELAY_ARG" in
    /*) RELAY_DIR="$RELAY_ARG" ;;
    *) RELAY_DIR="$ROOT/$RELAY_ARG" ;;
esac
case "$ADAPTER_ARG" in
    /*) ADAPTER_DIR="$ADAPTER_ARG" ;;
    *) ADAPTER_DIR="$ROOT/$ADAPTER_ARG" ;;
esac

RUN_DIR="$ROOT/artifacts/verification/$RUN_ID"
mkdir -p "$RUN_DIR"
test -f "$AUDIO"
test -f "$RELAY_DIR/session.json"
test -f "$ADAPTER_DIR/voice-channel.json"

cd "$ROOT"
uv run python -m secretary_service.voice.e2e \
    "$AUDIO" \
    "$RELAY_DIR/session.json" \
    "$ADAPTER_DIR/voice-channel.json" \
    "$RUN_DIR/voice-conversation-report.json"

if [ -n "$IOS_SIMULATOR" ]; then
    if xcrun simctl list devices >/dev/null 2>&1; then
        printf 'simulator requested=%s; native run requires the SecretaryApp Xcode scheme\n' \
            "$IOS_SIMULATOR" > "$RUN_DIR/ios-simulator.txt"
    else
        printf 'simulator requested=%s; unavailable on Command Line Tools host\n' \
            "$IOS_SIMULATOR" > "$RUN_DIR/ios-simulator.txt"
    fi
fi

uv run python - "$RUN_DIR/voice-conversation-report.json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["status"] == "passed"
assert report["event_ids"] == list(range(1, 8))
assert report["tts"][-1] == "Workout moved to 6 PM."
assert report["approved_replans"] == 1
assert report["execution_leases"] == 1
assert report["cancelled_approval_execution_leases"] == 0
assert report["resume_event_ids"] == [5, 6, 7]
assert report["channel_bridge_sequences"] == [5, 6, 7]
PY

printf 'voice-conversation E2E passed: %s\n' "$RUN_DIR"
if [ -n "$IOS_SIMULATOR" ]; then
    printf 'UNVERIFIED: native iOS UI/voice test harness is not implemented; fixture checks passed only.\n' >&2
    exit 2
fi
