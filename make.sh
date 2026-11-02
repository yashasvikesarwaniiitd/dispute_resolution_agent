#!/usr/bin/env bash
# Shim so `./make.sh <target>` works where GNU Make is absent (card P0-02).
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python scripts/make.py "$@"
