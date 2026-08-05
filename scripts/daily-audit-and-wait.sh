#!/usr/bin/env bash
# daily-audit-and-wait.sh — Run audit, wait for approval, then IMMEDIATELY start implementation
# No waiting until 18:00 — as soon as user approves, we go!

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
APPROVAL_DIR="/home/admin/approvals"  # External to repo, user-only access
REPORT_DATE=$(date +%Y-%m-%d)
REPORT_FILE="$AUDIT_DIR/$REPORT_DATE-audit-report.json"
APPROVAL_FILE="$APPROVAL_DIR/$REPORT_DATE-approval.json"

mkdir -p "$AUDIT_DIR" "$APPROVAL_DIR"
chmod 700 "$APPROVAL_DIR"

echo "════════════════════════════════════════════════════════"
echo "🔍 PHASE 1: DAILY AUDIT"
echo "════════════════════════════════════════════════════════"
echo "Date: $REPORT_DATE"
echo

# Run audit
cd "$PROJECT_DIR"

AUDIT_CONTEXT=$(cat << 'CONTEXT_END'
Project: nemohermes_bks
Location: /home/admin/projects/nemohermes_bks

Analyze project and output JSON with issues (CRITICAL, HIGH, MEDIUM, LOW).
For each issue include:
- id: "AUDIT-XXX"
- priority: level
- title: short title
- description: detailed
- effort: "QUICK|MEDIUM|COMPLEX"
- implementation: specific steps/files to create

Output ONLY valid JSON.
CONTEXT_END
)

echo "Running Claude audit..."

# Separate stderr from stdout for proper error handling
AUDIT_STDERR=$(mktemp)
set +e
AUDIT_REPORT=$(timeout 1800 claude -p "$AUDIT_CONTEXT" \
  --allowedTools "Read" \
  --max-turns 16 2>"$AUDIT_STDERR")
CLAUDE_EXIT=$?
set -e

# Check for stderr and report it
if [ -s "$AUDIT_STDERR" ]; then
    echo "⚠️  Claude stderr:"
    cat "$AUDIT_STDERR" >&2
fi
rm -f "$AUDIT_STDERR"

# Fail-closed: validate JSON structure before saving
if [ $CLAUDE_EXIT -ne 0 ]; then
    echo "❌ Claude exited with code $CLAUDE_EXIT — audit failed"
    exit 1
fi

if ! echo "$AUDIT_REPORT" | jq -e '.issues' >/dev/null 2>&1; then
    echo "❌ Invalid JSON or missing .issues field — audit report corrupted"
    echo "Raw output:"
    echo "$AUDIT_REPORT" | head -50
    exit 1
fi

echo "$AUDIT_REPORT" > "$REPORT_FILE"

echo "✅ Audit complete"
echo "📄 Report saved: $REPORT_FILE"
echo
echo "════════════════════════════════════════════════════════"
echo "⏳ AWAITING YOUR APPROVAL"
echo "════════════════════════════════════════════════════════"
echo
echo "📋 Next step:"
echo "   1. Review: cat $REPORT_FILE | jq '.issues'"
echo "   2. Create approval file with selected issues"
echo "   3. Approval file path: $APPROVAL_FILE"
echo
echo "Example approval file format:"
cat << 'EXAMPLE'
{
  "approved_issue_ids": ["AUDIT-001", "AUDIT-003"],
  "approved_issues": [
    {
      "id": "AUDIT-001",
      "priority": "CRITICAL",
      "title": "Add connection pooling",
      "implementation": "Setup pgbouncer..."
    }
  ]
}
EXAMPLE

echo
echo "⏳ Waiting for approval file..."
echo "   File: $APPROVAL_FILE"
echo

# Wait for approval file (max 8 hours)
MAX_WAIT=28800
ELAPSED=0
INTERVAL=30

while [ $ELAPSED -lt $MAX_WAIT ]; do
    if [ -f "$APPROVAL_FILE" ]; then
        echo ""
        echo "✅ APPROVAL RECEIVED!"
        
        # Validate approval file structure and content
        echo "🔍 Validating approval file..."
        
        # Check JSON validity
        if ! jq -e '.approved_issue_ids' "$APPROVAL_FILE" >/dev/null 2>&1; then
            echo "❌ Invalid approval JSON or missing approved_issue_ids"
            exit 1
        fi
        
        # Validate each issue ID exists in audit report
        INVALID_ISSUES=0
        while IFS= read -r ISSUE_ID; do
            if ! jq -e ".issues[] | select(.id == \"$ISSUE_ID\")" "$REPORT_FILE" >/dev/null 2>&1; then
                echo "❌ Issue $ISSUE_ID not found in audit report"
                INVALID_ISSUES=$((INVALID_ISSUES + 1))
            fi
        done < <(jq -r '.approved_issue_ids[]' "$APPROVAL_FILE")
        
        if [ $INVALID_ISSUES -gt 0 ]; then
            echo "❌ $INVALID_ISSUES invalid issues in approval file"
            exit 1
        fi
        
        echo "✅ Approval file valid"
        echo "🚀 STARTING IMMEDIATE IMPLEMENTATION..."
        echo
        
        # Hand off to implementation script
        bash "$PROJECT_DIR/scripts/daily-implement-now.sh"
        exit $?
    fi
    
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
    
    # Progress indicator
    if [ $((ELAPSED % 300)) -eq 0 ]; then
        echo "⏳ Still waiting... ($(($MAX_WAIT - $ELAPSED)) seconds remaining)"
    fi
done

echo "⚠️  TIMEOUT: No approval received after 8 hours"
exit 1
