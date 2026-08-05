#!/usr/bin/env bash
# daily-implement-now.sh — IMMEDIATE implementation after user approval
# Creates Kanban, then Sequential Loop with Claude testing each task
# Runs ALL DAY until complete

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
KANBAN_DIR="$PROJECT_DIR/kanban"
REPORT_DATE=$(date +%Y-%m-%d)
APPROVAL_FILE="$AUDIT_DIR/$REPORT_DATE-approval.json"
KANBAN_FILE="$KANBAN_DIR/$REPORT_DATE-kanban.json"
LOG_FILE="$AUDIT_DIR/$REPORT_DATE-implementation.log"

mkdir -p "$AUDIT_DIR" "$KANBAN_DIR"

{
    echo "════════════════════════════════════════════════════════════"
    echo "🚀 PHASE 2: IMMEDIATE IMPLEMENTATION (Sequential Loop)"
    echo "════════════════════════════════════════════════════════════"
    echo "Start time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Approval file: $APPROVAL_FILE"
    echo
    
    # ============ STEP 1: Parse Approval & Create Kanban ============
    echo "📋 Step 1: Creating Kanban board from approved issues..."
    echo
    
    # Build Kanban JSON
    cat > "$KANBAN_FILE" << 'KANBAN_INIT'
{
  "date": "REPLACE_DATE",
  "total_tasks": 0,
  "tasks": []
}
KANBAN_INIT
    
    # Parse approved issues and add to Kanban
    TASK_NUM=0
    TASKS_JSON='[]'
    
    # Extract approved issues from approval file
    jq -r '.approved_issues[]' "$APPROVAL_FILE" 2>/dev/null | while read -r ISSUE_JSON; do
        ((TASK_NUM++))
        
        ISSUE_ID=$(echo "$ISSUE_JSON" | jq -r '.id')
        PRIORITY=$(echo "$ISSUE_JSON" | jq -r '.priority')
        TITLE=$(echo "$ISSUE_JSON" | jq -r '.title')
        IMPL=$(echo "$ISSUE_JSON" | jq -r '.implementation')
        
        echo "  Task $TASK_NUM: $TITLE ($PRIORITY)"
    done
    
    echo "✅ Kanban created with $(jq '.approved_issues | length' "$APPROVAL_FILE") tasks"
    echo
    
    # ============ STEP 2: Sequential Loop with Claude ============
    echo "════════════════════════════════════════════════════════════"
    echo "🔄 SEQUENTIAL IMPLEMENTATION LOOP"
    echo "════════════════════════════════════════════════════════════"
    echo
    
    cd "$PROJECT_DIR"
    
    TASK_NUM=0
    COMPLETED=0
    FAILED=0
    
    # Read each approved issue and implement
    jq -r '.approved_issues[] | @json' "$APPROVAL_FILE" | while IFS= read -r ISSUE_LINE; do
        ISSUE=$(echo "$ISSUE_LINE" | sed 's/^"//' | sed 's/"$//')
        
        ((TASK_NUM++))
        
        ISSUE_ID=$(echo "$ISSUE" | jq -r '.id')
        PRIORITY=$(echo "$ISSUE" | jq -r '.priority')
        TITLE=$(echo "$ISSUE" | jq -r '.title')
        IMPL=$(echo "$ISSUE" | jq -r '.implementation')
        
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "📌 Task $TASK_NUM: $TITLE"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Priority: $PRIORITY | Issue ID: $ISSUE_ID"
        echo
        echo "Implementation plan:"
        echo "$IMPL"
        echo
        
        # ============ CLAUDE TASK: IMPLEMENT + TEST ============
        echo "🤖 Executing via Claude (with embedded testing)..."
        echo
        
        CLAUDE_PROMPT=$(cat << PROMPT_EOF
Task $TASK_NUM of $(jq '.approved_issues | length' "$APPROVAL_FILE"): $TITLE

Implementation requirements:
$IMPL

IMPORTANT: As you implement, include testing:
1. Create files/changes
2. For each file created, verify it exists: \`ls -la FILE\`
3. For code files, run basic syntax check
4. For scripts, run: \`bash -n SCRIPT.sh\` (syntax check)
5. For Python, run: \`python -m py_compile FILE.py\`
6. For YAML, run: \`yamllint FILE.yaml\` or \`yq . FILE.yaml\`
7. Run any existing unit tests: npm test, pytest
8. Output summary: what was created, what was tested, pass/fail

Keep implementation focused and concise. Stop after creating necessary files.
PROMPT_EOF
)
        
        # Run Claude with embedded testing
        if timeout 600 claude -p "$CLAUDE_PROMPT" \
            --allowedTools "Read,Edit" \
            --max-turns 8 >> "$LOG_FILE" 2>&1; then
            
            echo "✅ Task $TASK_NUM completed"
            ((COMPLETED++))
        else
            echo "⚠️  Task $TASK_NUM timeout or error"
            ((FAILED++))
        fi
        
        echo ""
        echo "⏱️  $(date '+%H:%M:%S') — Completed $COMPLETED/$TASK_NUM tasks"
        echo ""
    done
    
    # ============ STEP 3: Summary ============
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "📊 IMPLEMENTATION SUMMARY"
    echo "════════════════════════════════════════════════════════════"
    echo "Completed: $COMPLETED"
    echo "Failed: $FAILED"
    echo "Total: $TASK_NUM"
    echo "End time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo
    echo "Log file: $LOG_FILE"
    echo
    
    # ============ STEP 4: Auto-run verification ============
    echo "🧪 Running verification & commit..."
    echo
    
    bash "$PROJECT_DIR/scripts/daily-verify-and-commit.sh"
    
} | tee -a "$LOG_FILE"
