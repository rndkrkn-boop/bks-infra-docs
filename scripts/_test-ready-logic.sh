#!/usr/bin/env bash
set -euo pipefail

run_case() {
    local desc="$1" fake_stdout="$2" fake_exit="$3"
    echo "=== CASE: $desc ==="
    VERIFICATION="$fake_stdout"
    CLAUDE_EXIT="$fake_exit"
    READY=false
    if [ "$CLAUDE_EXIT" -eq 0 ] && echo "$VERIFICATION" | jq -e . >/dev/null 2>&1; then
        READY_VALUE=$(echo "$VERIFICATION" | jq -r '.ready_to_commit')
        if [ "$READY_VALUE" = "true" ]; then
            READY=true
        fi
    else
        echo "  WARN: invalid JSON or claude exited non-zero (exit=$CLAUDE_EXIT) -> treating as NOT ready"
    fi
    echo "  -> READY=$READY"
    echo
}

run_case "invalid JSON, exit=0" "this is not json at all {broken" 0
run_case "valid JSON, ready_to_commit=false" "{\"status\":\"at_risk\",\"issues\":[\"x\"],\"ready_to_commit\":false}" 0
run_case "valid JSON, ready_to_commit=true" "{\"status\":\"healthy\",\"issues\":[],\"ready_to_commit\":true}" 0
run_case "claude exit!=0 but stdout looks like valid true" "{\"status\":\"healthy\",\"ready_to_commit\":true}" 1

echo "Expected: READY=false for cases 1,2,4; READY=true only for case 3."
