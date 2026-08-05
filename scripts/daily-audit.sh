#!/usr/bin/env bash
# daily-audit.sh — Run comprehensive project audit via Claude
# Generates report with improvement proposals
# Output: JSON report for user approval

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
REPORT_TIME=$(date +%H:%M:%S)
REPORT_FILE="$AUDIT_DIR/$REPORT_DATE-audit-report.json"
APPROVAL_FILE="$AUDIT_DIR/$REPORT_DATE-approval.json"

mkdir -p "$AUDIT_DIR"

echo "🔍 Starting Daily Project Audit"
echo "Date: $REPORT_DATE $REPORT_TIME"
echo

# Prepare audit context (project structure, issues, metrics)
AUDIT_CONTEXT=$(cat << 'CONTEXT_END'
Project: nemohermes_bks
Location: /home/admin/projects/nemohermes_bks

INSTRUCTIONS FOR CLAUDE:
1. Analyze the project structure comprehensively
2. Check for:
   - Code quality issues (lint, complexity, duplication)
   - Security vulnerabilities (dependencies, secrets, RBAC)
   - Architecture problems (coupling, SPOF, scaling issues)
   - Documentation gaps (missing/outdated docs)
   - Test coverage (missing unit/integration/e2e tests)
   - Performance issues (bottlenecks, inefficiencies)
   - Deployment readiness (CI/CD, monitoring, logging)
   - Team processes (documentation, runbooks, SLOs)

3. For each issue found:
   - Priority: CRITICAL | HIGH | MEDIUM | LOW
   - Title: Brief title (max 50 chars)
   - Description: Detailed explanation (2-3 sentences)
   - Impact: What breaks if not fixed
   - Effort: QUICK (< 1 hour) | MEDIUM (1-4 hours) | COMPLEX (> 4 hours)
   - Implementation: How to fix (specific commands/files to create)

4. Output JSON with this structure:
   {
     "audit_date": "2026-08-XX",
     "issues": [
       {
         "id": "AUDIT-001",
         "priority": "CRITICAL",
         "title": "Missing test coverage",
         "description": "...",
         "impact": "...",
         "effort": "MEDIUM",
         "implementation": "..."
       }
     ],
     "metrics": {
       "total_issues": N,
       "by_priority": {"CRITICAL": X, "HIGH": Y, ...},
       "by_effort": {"QUICK": X, "MEDIUM": Y, ...}
     }
   }

5. Output ONLY valid JSON (no other text).
CONTEXT_END
)

# Run Claude audit
echo "Running Claude audit analysis..."
cd "$PROJECT_DIR"

AUDIT_REPORT=$(timeout 600 claude -p "$AUDIT_CONTEXT" \
  --allowedTools "Read" \
  --max-turns 8 2>&1 || echo '{"error": "Claude timeout or error"}')

# Save raw report
echo "$AUDIT_REPORT" > "$REPORT_FILE"

echo "✅ Audit complete"
echo "📄 Report: $REPORT_FILE"
echo
echo "📊 Summary:"
echo "$AUDIT_REPORT" | jq '.metrics // .error' 2>/dev/null || echo "(Report parsing failed)"
echo
echo "⏳ Waiting for your approval..."
echo "📝 Review at: $REPORT_FILE"
echo "✅ Approve at: $APPROVAL_FILE (format: JSON with selected issue IDs)"
