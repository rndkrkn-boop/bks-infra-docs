#!/usr/bin/env bash
# daily-audit-and-wait.sh — Run audit, wait for approval, then IMMEDIATELY
# start implementation. No waiting until 18:00 — as soon as user approves, we go!
#
# Thin wrapper: the actual prompt/limits/validation live in daily-audit.sh
# (single canonical implementation, AUDIT task 6). This just calls it with
# --wait so the two entrypoints can never drift apart again.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/daily-audit.sh" --wait "$@"
