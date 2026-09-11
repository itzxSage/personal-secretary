#!/usr/bin/env bash
set -euo pipefail
LIFEOS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LIFEOS_ROOT"
exec uv run python -m scripts.e2e_lifeos "$@"
