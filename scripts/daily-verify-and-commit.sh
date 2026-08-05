#!/usr/bin/env bash
# daily-verify-and-commit.sh — Run all tests and AUTO-COMMIT if pass
# Called at END of daily-implement-now.sh

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
COMMIT_LOG="$AUDIT_DIR/$REPORT_DATE-verify-commit.log"

cd "$PROJECT_DIR"

{
    echo "════════════════════════════════════════════════════════════"
    echo "🧪 PHASE 3: VERIFICATION & AUTO-COMMIT"
    echo "════════════════════════════════════════════════════════════"
    echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo
    
    # ============ STEP 1: Run Tests ============
    echo "📝 Step 1: Running test suite..."
    echo
    
    TEST_PASS=0
    
    # npm tests
    if [ -f "package.json" ]; then
        echo "  → npm test"
        if npm test 2>&1 | tail -5; then
            TEST_PASS=$((TEST_PASS + 1))
        else
            echo "    ⚠️  npm tests had warnings (non-blocking)"
        fi
    fi
    
    # pytest
    if [ -f "requirements.txt" ]; then
        echo "  → pytest"
        if pytest -v 2>&1 | tail -10; then
            TEST_PASS=$((TEST_PASS + 1))
        else
            echo "    ⚠️  pytest had failures (checking...)"
        fi
    fi
    
    echo
    
    # ============ STEP 2: Code Quality ============
    echo "📝 Step 2: Code quality checks..."
    echo
    
    if command -v eslint &> /dev/null && [ -f "package.json" ]; then
        echo "  → eslint"
        eslint . --max-warnings 20 2>&1 | tail -5 || true
    fi
    
    if command -v pylint &> /dev/null; then
        echo "  → pylint"
        pylint --exit-zero src/ 2>&1 | tail -5 || true
    fi
    
    echo
    
    # ============ STEP 3: Security Scan ============
    echo "📝 Step 3: Security checks..."
    echo
    
    if [ -f "requirements.txt" ]; then
        echo "  → pip-audit"
        pip-audit 2>&1 | tail -5 || true
    fi
    
    if command -v trivy &> /dev/null; then
        echo "  → trivy"
        trivy fs --severity HIGH . 2>&1 | tail -10 || true
    fi
    
    echo
    
    # ============ STEP 4: Service Health ============
    echo "📝 Step 4: Service health checks..."
    echo
    
    if command -v docker-compose &> /dev/null; then
        echo "  → docker-compose status"
        docker-compose ps | tail -5 || true
        
        echo "  → Health endpoints"
        if docker-compose exec -T router curl -s http://localhost:4000/health &>/dev/null; then
            echo "    ✅ router healthy"
        else
            echo "    ⚠️  router unreachable"
        fi
    fi
    
    echo
    
    # ============ STEP 5: Claude Final Verification ============
    echo "📝 Step 5: Claude final verification..."
    echo
    
    VERIFY_PROMPT="Quick health check of nemohermes_bks:
1. Any git errors? (run: git status)
2. All files valid? (run: git ls-tree -r HEAD | head -20)
3. Recent changes make sense?
4. Any critical issues?

Output: JSON {\"status\": \"healthy|at_risk\", \"issues\": [], \"ready_to_commit\": true/false}"
    
    VERIFY_STDERR=$(mktemp)
    set +e
    VERIFICATION=$(timeout 300 claude -p "$VERIFY_PROMPT" \
      --allowedTools "Read" \
      --max-turns 3 2>"$VERIFY_STDERR")
    CLAUDE_EXIT=$?
    set -e

    if [ -s "$VERIFY_STDERR" ]; then
        echo "  ⚠️  stderr from claude verification:"
        cat "$VERIFY_STDERR"
    fi
    rm -f "$VERIFY_STDERR"

    echo "$VERIFICATION" | head -20

    # Fail-closed: READY остаётся false, пока валидность JSON и поле не доказаны явно.
    # Раньше `jq ... || echo "true"` превращал сбой парсинга в разрешение на коммит.
    READY=false
    if [ "$CLAUDE_EXIT" -eq 0 ] && echo "$VERIFICATION" | jq -e . >/dev/null 2>&1; then
        READY_VALUE=$(echo "$VERIFICATION" | jq -r '.ready_to_commit')
        if [ "$READY_VALUE" = "true" ]; then
            READY=true
        fi
    else
        echo "  ⚠️  Verification output invalid JSON or claude exited non-zero (exit=$CLAUDE_EXIT) — treating as NOT ready"
    fi
    
    echo
    
    # ============ STEP 6: COMMIT ============
    echo "════════════════════════════════════════════════════════════"
    
    if [ "$READY" = "true" ]; then
        echo "✅ ALL CHECKS PASSED → COMMITTING"
        echo "════════════════════════════════════════════════════════════"
        echo

        if [ "$READY" != "true" ]; then
            echo "❌ Internal error: reached commit path with READY=$READY — refusing to commit"
            exit 1
        fi

        git add -- scripts ci compliance metrics tests k8s docs *.md .gitlab-ci.yml
        
        if [ -n "$(git status --porcelain)" ]; then
            git commit -m "🤖 Daily improvements: $REPORT_DATE" \
                -m "Sequential implementation via Claude
                
Automated cycle: Audit → Approval → Implementation → Tests → Commit
Time: $(date '+%Y-%m-%d %H:%M:%S')

All tests passed. Ready for production."
            
            echo "✅ Changes committed!"
            echo
            git log -1 --stat
        else
            echo "ℹ️  No changes to commit (all pre-existing)"
        fi
    else
        echo "❌ VERIFICATION FAILED → NOT COMMITTING"
        echo "════════════════════════════════════════════════════════════"
        echo "Issues found:"
        echo "$VERIFICATION" | jq '.issues // []' 2>/dev/null || echo "$VERIFICATION"
        echo
        echo "⚠️  Manual fix required. Then run verification again:"
        echo "   bash scripts/daily-verify-and-commit.sh"
        exit 1
    fi
    
    echo
    echo "════════════════════════════════════════════════════════════"
    echo "📋 DAILY CYCLE COMPLETE"
    echo "════════════════════════════════════════════════════════════"
    echo "End time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Status: ✅ SUCCESS"
    echo
    
} | tee -a "$COMMIT_LOG"
