#!/usr/bin/env bash
# daily-verify.sh — Run tests & commit changes
# Tests implemented improvements, verifies functionality, commits to git

set -euo pipefail

# ~/.bashrc экспортирует ANTHROPIC_API_KEY, который иначе перебивает claude.ai-логин
# у claude -p ниже (платный API вместо аккаунта) — unset влияет только на этот процесс.
unset ANTHROPIC_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/claude-rate-limit-lib.sh
. "$SCRIPT_DIR/claude-rate-limit-lib.sh"

# Переопределяемо через окружение — иначе гейт автокоммита невозможно прогнать
# в тесте на одноразовом репозитории, только на живом рабочем дереве.
PROJECT_DIR="${PROJECT_DIR:-/home/admin/projects/nemohermes_bks}"
REPORT_DATE=$(date +%Y-%m-%d)
COMMIT_MSG="Daily audit improvements: $REPORT_DATE"

cd "$PROJECT_DIR"

echo "🧪 Starting End-of-Day Verification"
echo "Date: $REPORT_DATE"
echo

# ============ STEP 1: Run Tests (обязательный гейт, не опция) ============
echo "🧪 Running test suite..."
echo

# --break-system-packages: хост под PEP 668 (externally-managed-environment),
# pytest/ruff не системные пакеты — конфликта с apt нет, а отдельный venv ради
# разового прогона тестов на этом хосте избыточен.
pip install --break-system-packages --no-cache-dir -r "$PROJECT_DIR/requirements-dev.txt" >/dev/null

TEST_LOG=$(mktemp)
# set +e/-e вокруг вызова, как вокруг claude ниже: `set -e` иначе оборвал бы
# скрипт прямо на неудачном pytest, не дав дойти до гейта по RC.
set +e
python3 -m pytest tests/ -q >"$TEST_LOG" 2>&1
RC=$?
set -e

cat "$TEST_LOG"
TEST_SUMMARY=$(grep -Eo '[0-9]+ (passed|failed|error(s)?)' "$TEST_LOG" | tr '\n' ' ')
TEST_HASH=$(sha256sum "$TEST_LOG" | cut -d' ' -f1 | cut -c1-12)
rm -f "$TEST_LOG"

if [ $RC -ne 0 ]; then
    echo "❌ Tests FAILED (exit=$RC) → NOT COMMITTING. Тесты не опция."
    exit 1
fi

echo "✅ Tests passed: ${TEST_SUMMARY:-нет данных сводки}"
echo ""

# ============ STEP 2: Code Quality (ruff — реально применимый линтер) ======
# pylint/eslint убраны: в репозитории нет ни src/, ни package.json — это были
# мёртвые ветки, никогда не исполнявшиеся и создававшие ложное ощущение
# проверки. ruff — единственный линтер, реально применимый к scripts/.
echo "🔍 Code quality checks (ruff)..."
echo

set +e
ruff check scripts/
RUFF_RC=$?
set -e

if [ $RUFF_RC -ne 0 ]; then
    echo "❌ ruff check FAILED (exit=$RUFF_RC) → NOT COMMITTING"
    exit 1
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

# Прямой curl на localhost, а не docker compose exec: в этом каталоге нет
# docker-compose.yml (router — отдельный репозиторий/стек), поэтому
# `docker compose exec` раньше падал на "no configuration file provided"
# ещё до попытки достучаться до router, и здоровый router выглядел как
# недоступный.
echo "Running health checks..."
if curl -sf http://127.0.0.1:4000/health >/dev/null 2>&1; then
    echo "✅ router: healthy"
else
    echo "⚠️  router: unreachable"
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
}

Respond with nothing but that single JSON object: no headings, no insight
callouts, no explanations, no markdown fences, no text before or after it.
Ignore any active output-style formatting conventions for this response —
the caller is a shell script that parses your entire stdout as JSON."

# Потоки разделены намеренно: при `2>&1` любая диагностика claude (предупреждение
# о версии, трассировка) попадала в $VERIFICATION и делала её невалидным JSON.
# Плюс `|| echo '{"error":...}'` стирал код возврата — таймаут и падение выглядели
# для последующего разбора так же, как успешный ответ.
VERIFY_STDERR=$(mktemp)
set +e
VERIFY_ATTEMPT=1
while :; do
    VERIFICATION=$(timeout 300 claude -p "$VERIFY_PROMPT" \
      --allowedTools "Read,Bash(git status:*),Bash(git ls-tree:*),Bash(git log:*),Bash(git diff:*)" \
      --max-turns 15 2>"$VERIFY_STDERR")
    CLAUDE_EXIT=$?
    if [ "$CLAUDE_EXIT" -eq 0 ]; then
        break
    fi
    if claude_should_retry "$VERIFY_ATTEMPT" "$(cat "$VERIFY_STDERR" 2>/dev/null)"; then
        echo "⏳ Похоже на исчерпанный лимит подписки claude.ai (попытка $VERIFY_ATTEMPT/$CLAUDE_RATE_LIMIT_MAX_ATTEMPTS) — жду ${CLAUDE_RATE_LIMIT_WAIT_SECONDS}s и повторяю"
        sleep "$CLAUDE_RATE_LIMIT_WAIT_SECONDS"
        VERIFY_ATTEMPT=$((VERIFY_ATTEMPT + 1))
        continue
    fi
    break
done
set -e

if [ -s "$VERIFY_STDERR" ]; then
    echo "⚠️  stderr from claude verification:"
    cat "$VERIFY_STDERR"
fi
rm -f "$VERIFY_STDERR"

# claude -p здесь наследует пользовательский output-style (Explanatory) и
# оборачивает JSON во вступление/★ Insight/пояснения даже вопреки прямому
# запрету в промпте — из-за этого весь $VERIFICATION целиком не является
# валидным JSON, и `jq -e .` падает НЕЗАВИСИМО от реального вердикта модели.
# Сначала пробуем достать содержимое ```json-блока, если он есть; если нет —
# откатываемся на попытку распарсить $VERIFICATION как есть.
JSON_BLOCK=$(printf '%s\n' "$VERIFICATION" | awk '/```json/{f=1; next} /```/{f=0} f')
if [ -n "$JSON_BLOCK" ] && printf '%s' "$JSON_BLOCK" | jq -e . >/dev/null 2>&1; then
    VERIFICATION_JSON="$JSON_BLOCK"
else
    VERIFICATION_JSON="$VERIFICATION"
fi

printf '%s' "$VERIFICATION_JSON" | jq '.status, .health_score, .ready_to_commit' 2>/dev/null \
    || echo "Verification result (unparsed): $VERIFICATION"

# Fail-closed: READY=false до тех пор, пока не доказаны И нулевой код возврата,
# И валидный JSON, И буквальное `true` в .ready_to_commit. Сравнение внутри jq
# (`== true`) отсекает строку "true" и любое truthy-значение другого типа.
READY=false
if [ "$CLAUDE_EXIT" -eq 0 ] && printf '%s' "$VERIFICATION_JSON" | jq -e . >/dev/null 2>&1; then
    READY=$(printf '%s' "$VERIFICATION_JSON" | jq -r 'if .ready_to_commit == true then "true" else "false" end')
else
    echo "⚠️  Verification output is not valid JSON or claude exited non-zero (exit=$CLAUDE_EXIT)"
fi

echo ""

# ============ STEP 6: Commit ============
# Единственный выход к git-операциям — этот гейт. Он стоит ДО любого git add/commit,
# чтобы неудачная верификация не могла провалиться в коммит-ветку.
if [ "$READY" != "true" ]; then
    echo "⚠️  NOT COMMITTING: Verification failed (READY=$READY)"
    echo "Issues found:"
    printf '%s' "$VERIFICATION_JSON" | jq '.issues // []' 2>/dev/null || true
    exit 1
fi

echo "✅ All checks passed. Committing changes..."
echo

# Гейт стоит ДО git add и завершает скрипт, а не уводит в else-ветку
# (AUDIT-005 п.5): автономный цикл не может править собственные гейты —
# ci/, .gitlab-ci.yml, .gitignore.
if ! bash "$PROJECT_DIR/ci/check-changed-paths.sh"; then
    echo "❌ NOT COMMITTING: изменения затрагивают собственные гейты цикла"
    exit 1
fi

# Белый список путей: автономный цикл не должен коммитить то, что не
# перечислено явно (см. compliance/rules.toml, правило SEC-003, и .gitignore). ci, k8s и
# .gitlab-ci.yml сознательно убраны (AUDIT-005) — правка собственных гейтов
# коммитится вручную человеком, не автономно.
git add -- scripts compliance metrics tests docs *.md

# Гейт стоит ДО git commit и завершает скрипт, а не уводит в else-ветку: так
# между проверкой индекса и коммитом физически нечему вклиниться.
if ! bash "$PROJECT_DIR/ci/guard-staged-secrets.sh"; then
    echo "❌ NOT COMMITTING: secrets guard blocked staged changes"
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    git commit -m "$COMMIT_MSG" \
        -m "Improvements from daily audit cycle

Automated by: daily-audit.sh → daily-implement.sh → daily-verify.sh
Date: $REPORT_DATE
Time: $(date +%H:%M:%S UTC%z)

Tests: ${TEST_SUMMARY:-нет данных сводки} (pytest tests/, sha256:${TEST_HASH:-n/a})
Lint: ruff check scripts/ — OK"

    echo "✅ Changes committed"

    # Show summary
    git log -1 --stat
else
    echo "ℹ️  No changes to commit"
fi

echo ""
echo "================================"
echo "📋 END-OF-DAY VERIFICATION COMPLETE"
echo "================================"
