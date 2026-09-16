#!/bin/sh
# iPhone-friendly fallback; explicit free provider, no external-plugin routing.
set -eu
cd /Users/jrsgagne/Development/personal-secretary
export OPENCODE_CONFIG_CONTENT='{"enabled_providers":["opencode"],"model":"opencode/nemotron-3-ultra-free","small_model":"opencode/mimo-v2.5-free","share":"disabled"}'
exec opencode run --pure --agent build --model opencode/nemotron-3-ultra-free "$(cat tools/dev_harness/OPENCODE_CONTINUE.txt)"
