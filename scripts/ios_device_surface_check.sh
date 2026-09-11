#!/bin/bash
set -euo pipefail

DEVICE_ID="${LIFEOS_TEST_DEVICE:-}"
BUNDLE_ID="${LIFEOS_IOS_BUNDLE_ID:-com.lifeos.SecretaryApp}"
RUN_ID="${LIFEOS_RUN_ID:-device-check}"
REPORT_PATH="${LIFEOS_DEVICE_REPORT:-$HOME/.omo/evidence/lifeos/$RUN_ID/task-11/device-surface-report.json}"

usage() {
    printf '%s\n' "Usage: $0 [--device-id ID] [--bundle-id ID] [--output PATH]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device-id)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            DEVICE_ID="$2"
            shift 2
            ;;
        --bundle-id)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            BUNDLE_ID="$2"
            shift 2
            ;;
        --output)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            REPORT_PATH="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

mkdir -p "$(dirname "$REPORT_PATH")"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/lifeos-ios-device.XXXXXX")
trap 'rm -rf "$TEMP_DIR"' EXIT

DEVICE_REFERENCE=""
if [[ -n "$DEVICE_ID" ]]; then
    DEVICE_REFERENCE=$(printf '%s' "$DEVICE_ID" | shasum -a 256 | cut -c1-12)
fi

write_unverified_report() {
    local reason="$1"
    python3 - "$REPORT_PATH" "$DEVICE_REFERENCE" "$reason" <<'PY'
import json
import sys

path, device_reference, reason = sys.argv[1:]
surfaces = {
    "push_to_talk": "unverified",
    "app_shortcut": "unverified",
    "back_tap": "unverified",
    "action_button": "unverified",
    "control_center": "unverified",
    "lock_screen": "unverified",
    "notification_deep_link": "unverified",
}
report = {
    "schema_version": 1,
    "device_reference": device_reference,
    "device_found": False,
    "app_installed": False,
    "surfaces": surfaces,
    "result": "unverified",
    "reason": reason,
    "limitations": [
        "The harness does not synthesize hardware gestures, Back Tap, or Siri speech.",
        "Gesture support requires separately captured configuration evidence.",
    ],
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
    printf 'UNVERIFIED: %s\nReport: %s\n' "$reason" "$REPORT_PATH"
    exit 0
}

[[ -n "$DEVICE_ID" ]] || write_unverified_report "no physical device identifier was provided"
command -v xcrun >/dev/null 2>&1 || write_unverified_report "xcrun is unavailable"
xcrun --find devicectl >/dev/null 2>&1 || write_unverified_report "devicectl requires a full Xcode installation"

DETAILS_JSON="$TEMP_DIR/device-details.json"
if ! xcrun devicectl device info details --device "$DEVICE_ID" --json-output "$DETAILS_JSON" >/dev/null 2>&1; then
    write_unverified_report "the paired physical device is unavailable"
fi

APPS_JSON="$TEMP_DIR/apps.json"
APP_INSTALLED=false
if xcrun devicectl device info apps --device "$DEVICE_ID" --json-output "$APPS_JSON" >/dev/null 2>&1; then
    if python3 - "$APPS_JSON" "$BUNDLE_ID" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
raise SystemExit(0 if sys.argv[2] in json.dumps(payload, sort_keys=True) else 1)
PY
    then
        APP_INSTALLED=true
    fi
fi

RECEIPTS_JSON="$TEMP_DIR/lifeos-surface-receipts.json"
RECEIPTS_FOUND=false
if [[ "$APP_INSTALLED" == true ]]; then
    if xcrun devicectl device copy from \
        --device "$DEVICE_ID" \
        --domain-type appDataContainer \
        --domain-identifier "$BUNDLE_ID" \
        --source "Documents/lifeos-surface-receipts.json" \
        --destination "$RECEIPTS_JSON" >/dev/null 2>&1; then
        RECEIPTS_FOUND=true
    fi
fi

python3 - "$REPORT_PATH" "$DEVICE_REFERENCE" "$APP_INSTALLED" "$RECEIPTS_FOUND" "$RECEIPTS_JSON" <<'PY'
import json
import os
import sys

path, device_reference, installed_raw, receipts_raw, receipts_path = sys.argv[1:]
app_installed = installed_raw == "true"
receipts_found = receipts_raw == "true"
observed = set()
if receipts_found and os.path.isfile(receipts_path):
    try:
        with open(receipts_path, encoding="utf-8") as handle:
            observed = {
                item["surface"]
                for item in json.load(handle)
                if isinstance(item, dict) and isinstance(item.get("surface"), str)
            }
    except (OSError, ValueError, KeyError, TypeError):
        observed = set()

def receipt_state(surface):
    return "receipt_observed" if surface in observed else "unverified"

surfaces = {
    "push_to_talk": receipt_state("push_to_talk"),
    "app_shortcut": receipt_state("app_shortcut"),
    "back_tap": "manual_configuration_required",
    "action_button": "manual_configuration_required",
    "control_center": receipt_state("control_center"),
    "lock_screen": receipt_state("lock_screen"),
    "notification_deep_link": receipt_state("notification"),
}
report = {
    "schema_version": 1,
    "device_reference": device_reference,
    "device_found": True,
    "app_installed": app_installed,
    "surfaces": surfaces,
    "result": "recorded",
    "limitations": [
        "App installation is not proof that an App Intent, widget, or control is registered.",
        "A receipt proves app handler execution, not which hardware or accessibility gesture initiated it.",
        "The harness does not synthesize hardware gestures, Back Tap, or Siri speech.",
        "Gesture support requires separately captured configuration evidence.",
    ],
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

printf 'RECORDED: paired device probe completed without hardware-gesture claims\nReport: %s\n' "$REPORT_PATH"
