#!/usr/bin/env bash
# daily-verify-and-commit.sh — Run all tests and AUTO-COMMIT if pass
# Called at END of daily-implement-now.sh

set -euo pipefail

# ~/.bashrc экспортирует ANTHROPIC_API_KEY, который иначе перебивает claude.ai-логин
# у claude -p ниже (платный API вместо аккаунта) — unset влияет только на этот процесс.
unset ANTHROPIC_API_KEY

# Переопределяемо через окружение — иначе гейт автокоммита невозможно прогнать
# в тесте на одноразовом репозитории, только на живом рабочем дереве.
PROJECT_DIR="${PROJECT_DIR:-/home/admin/projects/nemohermes_bks}"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
COMMIT_LOG="$AUDIT_DIR/$REPORT_DATE-verify-commit.log"

cd "$PROJECT_DIR"
# Без этого `tee -a "$COMMIT_LOG"` падает на несуществующем каталоге, и весь блок
# ниже — включая гейт — не исполняется вовсе.
mkdir -p "$AUDIT_DIR"

{
    echo "════════════════════════════════════════════════════════════"
    echo "🧪 PHASE 3: VERIFICATION & AUTO-COMMIT"
    echo "════════════════════════════════════════════════════════════"
    echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo
    
    # ============ STEP 1: Run Tests (обязательный гейт, не опция) ============
    echo "📝 Step 1: Running test suite..."
    echo

    # --break-system-packages: хост под PEP 668 (externally-managed-environment),
    # pytest/ruff не системные пакеты — конфликта с apt нет, а отдельный venv ради
    # разового прогона тестов на этом хосте избыточен.
    pip install --break-system-packages --no-cache-dir -r "$PROJECT_DIR/requirements-dev.txt" >/dev/null

    TEST_LOG=$(mktemp)
    # set +e/-e вокруг вызова, как в STEP 5 ниже: `set -e` иначе оборвал бы
    # скрипт прямо на неудачном pytest, не дав дойти до проверки RC — а именно
    # эта проверка и есть гейт коммита.
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
    echo

    # ============ STEP 2: Code Quality (ruff — реально применимый линтер) ====
    # eslint/npm/pylint убраны: в репозитории нет ни package.json, ни src/ —
    # это были мёртвые ветки, которые никогда не исполнялись и создавали
    # ложное ощущение проверки. ruff — единственный линтер, реально
    # применимый к содержимому scripts/ (см. .ruff_cache в корне репо).
    echo "📝 Step 2: Code quality checks (ruff)..."
    echo

    set +e
    ruff check scripts/
    RUFF_RC=$?
    set -e

    if [ $RUFF_RC -ne 0 ]; then
        echo "❌ ruff check FAILED (exit=$RUFF_RC) → NOT COMMITTING"
        exit 1
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
    
    # Прямой curl на localhost, а не docker compose exec: в этом каталоге нет
    # docker-compose.yml (router — отдельный репозиторий/стек), поэтому
    # `docker compose exec` раньше падал на "no configuration file provided"
    # ещё до попытки достучаться до router, и здоровый router выглядел как
    # недоступный.
    echo "  → Health endpoints"
    if curl -sf http://127.0.0.1:4000/health >/dev/null 2>&1; then
        echo "    ✅ router healthy"
    else
        echo "    ⚠️  router unreachable"
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

Respond with nothing but a single JSON object: no headings, no insight
callouts, no explanations, no markdown fences, no text before or after it.
Ignore any active output-style formatting conventions for this response —
the caller is a shell script that parses your entire stdout as JSON.
Format: {\"status\": \"healthy|at_risk\", \"issues\": [], \"ready_to_commit\": true/false}"
    
    VERIFY_STDERR=$(mktemp)
    set +e
    # Промпт выше явно просит "run: git status" / "git ls-tree" — без Bash-доступа
    # к этим двум командам верификация не могла даже прочитать состояние репо.
    VERIFICATION=$(timeout 300 claude -p "$VERIFY_PROMPT" \
      --allowedTools "Read,Bash(git status:*),Bash(git ls-tree:*),Bash(git log:*),Bash(git diff:*)" \
      --max-turns 15 2>"$VERIFY_STDERR")
    CLAUDE_EXIT=$?
    set -e

    if [ -s "$VERIFY_STDERR" ]; then
        echo "  ⚠️  stderr from claude verification:"
        cat "$VERIFY_STDERR"
    fi
    rm -f "$VERIFY_STDERR"

    printf '%s' "$VERIFICATION" | head -20

    # claude -p здесь наследует пользовательский output-style (Explanatory) и
    # оборачивает JSON во вступление/★ Insight/пояснения даже вопреки прямому
    # запрету в промпте — из-за этого весь $VERIFICATION целиком не является
    # валидным JSON, и `jq -e .` падает НЕЗАВИСИМО от реального вердикта модели.
    # Сначала пробуем достать содержимое ```json-блока, если он есть; если нет —
    # откатываемся на попытку распарсить $VERIFICATION как есть (вдруг модель
    # в этот раз ответила чистым JSON без обёртки).
    JSON_BLOCK=$(printf '%s\n' "$VERIFICATION" | awk '/```json/{f=1; next} /```/{f=0} f')
    if [ -n "$JSON_BLOCK" ] && printf '%s' "$JSON_BLOCK" | jq -e . >/dev/null 2>&1; then
        VERIFICATION_JSON="$JSON_BLOCK"
    else
        VERIFICATION_JSON="$VERIFICATION"
    fi

    # Fail-closed: READY остаётся false, пока валидность JSON и поле не доказаны явно.
    # Раньше `jq ... || echo "true"` превращал сбой парсинга в разрешение на коммит.
    # Сравнение `== true` внутри jq отсекает строку "true" и любое truthy-значение
    # другого типа — разрешением считается только булев литерал.
    READY=false
    if [ "$CLAUDE_EXIT" -eq 0 ] && printf '%s' "$VERIFICATION_JSON" | jq -e . >/dev/null 2>&1; then
        READY=$(printf '%s' "$VERIFICATION_JSON" | jq -r 'if .ready_to_commit == true then "true" else "false" end')
    else
        echo "  ⚠️  Verification output invalid JSON or claude exited non-zero (exit=$CLAUDE_EXIT) — treating as NOT ready"
    fi

    echo

    # ============ STEP 6: COMMIT ============
    echo "════════════════════════════════════════════════════════════"

    # Гейт стоит ДО первой git-операции и завершает скрипт, а не уводит в else-ветку:
    # так между проверкой и `git add` физически нечему вклиниться.
    if [ "$READY" != "true" ]; then
        echo "❌ VERIFICATION FAILED → NOT COMMITTING (READY=$READY)"
        echo "════════════════════════════════════════════════════════════"
        echo "Issues found:"
        printf '%s' "$VERIFICATION_JSON" | jq '.issues // []' 2>/dev/null || printf '%s\n' "$VERIFICATION"
        echo
        echo "⚠️  Manual fix required. Then run verification again:"
        echo "   bash scripts/daily-verify-and-commit.sh"
        exit 1
    fi

    echo "✅ ALL CHECKS PASSED → COMMITTING"
    echo "════════════════════════════════════════════════════════════"
    echo

    # Гейт стоит ДО git add и завершает скрипт, а не уводит в else-ветку
    # (AUDIT-005 п.5): автономный цикл не может править собственные гейты —
    # ci/, .gitlab-ci.yml, .gitignore. Раньше ci/check-changed-paths.sh
    # существовал, но не вызывался нигде, а белый список git add ниже сам же
    # включал ci и .gitlab-ci.yml — то есть гейт и его нарушение жили рядом.
    if ! bash "$PROJECT_DIR/ci/check-changed-paths.sh"; then
        echo "❌ NOT COMMITTING: изменения затрагивают собственные гейты цикла"
        exit 1
    fi

    # ci, k8s и .gitlab-ci.yml сознательно убраны из белого списка (AUDIT-005):
    # если задаче нужно поправить сами гейты цикла, это коммитится вручную
    # человеком, как сегодняшние AUDIT-005/007/008/009/010a — не автономно.
    git add -- scripts compliance metrics tests docs *.md

    # Гейт стоит ДО git commit и завершает скрипт, а не уводит в else-ветку: так
    # между проверкой индекса и коммитом физически нечему вклиниться.
    if ! bash "$PROJECT_DIR/ci/guard-staged-secrets.sh"; then
        echo "❌ NOT COMMITTING: secrets guard blocked staged changes"
        exit 1
    fi

    if [ -n "$(git status --porcelain)" ]; then
        git commit -m "🤖 Daily improvements: $REPORT_DATE" \
            -m "Sequential implementation via Claude

Automated cycle: Audit → Approval → Implementation → Tests → Commit
Time: $(date '+%Y-%m-%d %H:%M:%S')

Tests: ${TEST_SUMMARY:-нет данных сводки} (pytest tests/, sha256:${TEST_HASH:-n/a})
Lint: ruff check scripts/ — OK"

        echo "✅ Changes committed!"
        echo
        git log -1 --stat
    else
        echo "ℹ️  No changes to commit (all pre-existing)"
    fi


    echo
    echo "════════════════════════════════════════════════════════════"
    echo "📋 DAILY CYCLE COMPLETE"
    echo "════════════════════════════════════════════════════════════"
    echo "End time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Status: ✅ SUCCESS"
    echo
    
} | tee -a "$COMMIT_LOG"
