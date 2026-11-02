#!/usr/bin/env bash
# Stage B: build sessions. Opus plans in plan mode, Sonnet executes.
set -euo pipefail
claude --version
exec claude --model opusplan --effort medium "$@"
