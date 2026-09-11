#!/bin/sh
set -eu

LABEL="com.personal-secretary.service"
RUNTIME_USER="_secretary"
TARGET="/Library/LaunchDaemons/${LABEL}.plist"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' "install.sh must run as root" >&2
    exit 77
fi
if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
    printf '%s\n' "create the dedicated _secretary runtime user first; see docs/operations.md" >&2
    exit 67
fi

sed "s|__REPOSITORY_ROOT__|${ROOT}|g" \
    "$ROOT/infra/launchd/${LABEL}.plist" > "$TARGET"
chown root:wheel "$TARGET"
chmod 0644 "$TARGET"
launchctl bootstrap system "$TARGET"
