#!/usr/bin/env bash
# daily-implement.sh — Implement approved audit improvements via Sequential Claude Loop
# Reads approval file, creates Kanban tasks, executes via Claude

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
APPROVAL_FILE="$AUDIT_DIR/$REPORT_DATE-approval.json"
KANBAN_FILE="$PROJECT_DIR/kanban/daily-improvements.json"
LOG_FILE="$AUDIT_DIR/$REPORT_DATE-implementation.log"

mkdir -p "$AUDIT_DIR" "$(dirname "$KANBAN_FILE")"

echo "🚀 Starting Daily Implementation"
echo "Report date: $REPORT_DATE"
echo "Approval file: $APPROVAL_FILE"
echo

# Check if approval file exists
if [ ! -f "$APPROVAL_FILE" ]; then
    echo "❌ Approval file not found: $APPROVAL_FILE"
    echo "⏳ Waiting for user approval..."
    exit 1
fi

# Parse approved issues
echo "📋 Loading approved issues..."
APPROVED=$(jq -r '.approved_issue_ids[]' "$APPROVAL_FILE" 2>/dev/null || echo "")

if [ -z "$APPROVED" ]; then
    echo "⚠️  No issues approved. Exiting."
    exit 0
fi

# Create Kanban board for today
cat > "$KANBAN_FILE" << KANBAN_EOF
{
  "date": "$REPORT_DATE",
  "source": "daily-audit",
  "tasks": [
KANBAN_EOF

# Counter for tasks
TASK_NUM=0

# Read approval file and create tasks
jq -r '.approved_issues[] | @base64d' "$APPROVAL_FILE" 2>/dev/null | while read -r ISSUE; do
    ISSUE_ID=$(echo "$ISSUE" | jq -r '.id')
    TITLE=$(echo "$ISSUE" | jq -r '.title')
    IMPL=$(echo "$ISSUE" | jq -r '.implementation')
    
    TASK_NUM=$((TASK_NUM + 1))
    
    cat >> "$KANBAN_FILE" << TASK_EOF
    {
      "id": "$TASK_NUM",
      "issue_id": "$ISSUE_ID",
      "title": "$TITLE",
      "status": "READY",
      "implementation": $IMPL
    },
TASK_EOF
done

# Close JSON (remove trailing comma)
sed -i '$ s/,$//' "$KANBAN_FILE"

cat >> "$KANBAN_FILE" << KANBAN_EOF
  ]
}
KANBAN_EOF

echo "✅ Kanban created: $KANBAN_FILE"
echo

# Sequential Loop: Execute each task via Claude
echo "🔄 SEQUENTIAL EXECUTION LOOP"
echo "================================"
echo

cd "$PROJECT_DIR"

TASKS=$(jq -r '.tasks[].id' "$KANBAN_FILE")
TOTAL_TASKS=$(echo "$TASKS" | wc -l)
COMPLETED=0
FAILED=0

for TASK_ID in $TASKS; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Task $TASK_ID / $TOTAL_TASKS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Get task details
    TITLE=$(jq -r ".tasks[] | select(.id == $TASK_ID) | .title" "$KANBAN_FILE")
    IMPL=$(jq -r ".tasks[] | select(.id == $TASK_ID) | .implementation" "$KANBAN_FILE")
    
    echo "Title: $TITLE"
    echo "Implementation:"
    echo "$IMPL"
    echo
    
    # Run Claude to implement
    echo "🚀 Executing via Claude..."
    
    PROMPT="Implement this improvement for nemohermes_bks project: $TITLE

Details: $IMPL

Create necessary files and make changes. Output summary of what was done."
    
    if timeout 300 claude -p "$PROMPT" \
        --allowedTools "Read,Edit" \
        --max-turns 6 >> "$LOG_FILE" 2>&1; then
        
        echo "✅ Task $TASK_ID completed"
        COMPLETED=$((COMPLETED + 1))
    else
        echo "❌ Task $TASK_ID failed"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "================================"
echo "📊 IMPLEMENTATION SUMMARY"
echo "================================"
echo "Completed: $COMPLETED / $TOTAL_TASKS"
echo "Failed: $FAILED"
echo "Log: $LOG_FILE"
echo
echo "📝 Next: Run daily-verify.sh for testing & commit"
