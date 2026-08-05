#!/usr/bin/env bash
# daily-implement.sh — Legacy entrypoint used by daily-cycle-orchestrator.sh
# ("audit at 10:00 → approve → implement at 18:00" flow with Telegram
# notifications per phase).
#
# Thin wrapper: this used to be an independent, un-synced copy of the
# sequential implementation loop — same class of bugs (allowedTools without
# Write/Bash, --max-turns 6) that daily-implement-now.sh had before today's
# fix, plus it read the approval file from the pre-AUDIT-005 in-repo path
# with no schema/HMAC verification at all. Single canonical implementation
# now lives in daily-implement-now.sh; --no-auto-verify lets the orchestrator
# keep running its own daily-verify.sh step afterward instead of getting
# verify-and-commit twice.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/daily-implement-now.sh" --no-auto-verify "$@"
