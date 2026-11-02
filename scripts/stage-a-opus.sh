#!/usr/bin/env bash
# Stage A: design sessions on Opus. Pass extra flags through, for example: --effort high
set -euo pipefail
claude --version
exec claude --model opus --effort medium "$@"
