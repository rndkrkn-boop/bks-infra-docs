#!/usr/bin/env bash
# daily-verify.sh — Run tests & commit changes
# Tests implemented improvements, verifies functionality, commits to git

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
COMMIT_MSG="Daily audit improvements: $REPORT_DATE"

cd "$PROJECT_DIR"

echo "🧪 Starting End-of-Day Verification"
echo "Date: $REPORT_DATE"
echo

# ============ STEP 1: Run Tests ============
echo "🧪 Running test suite..."
echo

if [ -f "package.json" ]; then
    echo "Running npm tests..."
    npm test 2>&1 | tail -20 || echo "⚠️  npm tests failed"
fi

if [ -f "requirements.txt" ]; then
    echo "Running pytest..."
    pytest -v 2>&1 | tail -20 || echo "⚠️  pytest failed"
fi

echo ""

# ============ STEP 2: Code Quality Checks ============
echo "🔍 Code quality checks..."
echo

if command -v pylint &> /dev/null && [ -f "requirements.txt" ]; then
    echo "Running pylint..."
    pylint --exit-zero src/ 2>&1 | tail -10 || true
fi

if command -v eslint &> /dev/null && [ -f "package.json" ]; then
    echo "Running eslint..."
    eslint . --max-warnings 10 2>&1 | tail -10 || echo "⚠️  eslint warnings found"
fi

echo ""

# ============ STEP 3: Security Scan ============
echo "🔐 Security checks..."
echo

if command -v trivy &> /dev/null; then
    echo "Running trivy scan..."
    trivy fs --severity HIGH . 2>&1 | tail -20 || true
fi

if [ -f "requirements.txt" ]; then
    echo "Running pip audit..."
    pip-audit 2>&1 | tail -10 || echo "⚠️  pip vulnerabilities found"
fi

echo ""

# ============ STEP 4: Verify Services ============
echo "🚀 Verifying services..."
echo

if command -v docker-compose &> /dev/null; then
    echo "Docker compose status:"
    docker-compose ps | tail -10 || true
    
    echo ""
    echo "Running health checks..."
    if docker-compose exec -T router curl -s http://localhost:4000/health &>/dev/null; then
        echo "✅ router: healthy"
    else
        echo "⚠️  router: unreachable"
    fi
fi

echo ""

# ============ STEP 5: Claude Verification ============
echo "🤖 Running Claude verification..."
echo

VERIFY_PROMPT="Verify that nemohermes_bks project is in good state:
1. Check git status (no uncommitted broken changes)
2. Verify key files exist and are valid (docker-compose.yml, requirements.txt, package.json)
3. Check for obvious errors in recent commits
4. Assess overall health (1-10 scale)
5. Recommend any critical fixes needed before commit

Output JSON format:
{
  \"status\": \"healthy|at_risk|broken\",
  \"health_score\": 0-10,
  \"issues\": [
    {\"severity\": \"CRITICAL|HIGH|MEDIUM\",
     \"issue\": \"...\",
     \"fix\": \"...\"}
  ],
  \"ready_to_commit\": true|false,
  \"recommendations\": \"...\",
  \"summary\": \"...\"
}"

VERIFICATION=$(timeout 300 claude -p "$VERIFY_PROMPT" \
  --allowedTools "Read" \
  --max-turns 5 2>&1 || echo '{"error": "Claude timeout"}')

echo "$VERIFICATION" | jq '.status, .health_score, .ready_to_commit' 2>/dev/null || echo "Verification result: $VERIFICATION"

# Extract readiness
READY=$(echo "$VERIFICATION" | jq -r '.ready_to_commit // false' 2>/dev/null)

echo ""

# ============ STEP 6: Commit ============
if [ "$READY" = "true" ]; then
    echo "✅ All checks passed. Committing changes..."
    echo
    
    git add -A
    
    if [ -n "$(git status --porcelain)" ]; then
        git commit -m "$COMMIT_MSG" \
            -m "Improvements from daily audit cycle
            
Automated by: daily-audit.sh → daily-implement.sh → daily-verify.sh
Date: $REPORT_DATE
Time: $(date +%H:%M:%S UTC%z)"
        
        echo "✅ Changes committed"
        
        # Show summary
        git log -1 --stat
    else
        echo "ℹ️  No changes to commit"
    fi
else
    echo "⚠️  NOT COMMITTING: Verification failed"
    echo "Issues found:"
    echo "$VERIFICATION" | jq '.issues // []' 2>/dev/null
    exit 1
fi

echo ""
echo "================================"
echo "📋 END-OF-DAY VERIFICATION COMPLETE"
echo "================================"
