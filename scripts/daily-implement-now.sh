#!/usr/bin/env bash
# daily-implement-now.sh — IMMEDIATE implementation after user approval
# Creates Kanban, then Sequential Loop with Claude testing each task
# Runs ALL DAY until complete

set -euo pipefail

# ~/.bashrc экспортирует ANTHROPIC_API_KEY для интерактивных сессий, и claude -p
# наследует его здесь же — он берёт верх над claude.ai-логином (платный API вместо
# аккаунта), из-за чего цикл упирается в баланс ключа. unset — только в этом
# процессе, интерактивный шелл и остальные инструменты не затрагиваются.
unset ANTHROPIC_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/claude-rate-limit-lib.sh
. "$SCRIPT_DIR/claude-rate-limit-lib.sh"

# --no-auto-verify: для daily-cycle-orchestrator.sh, который сам вызывает
# daily-verify.sh отдельным шагом (Telegram-уведомления по фазам) — без флага
# оркестратор получил бы верификацию и коммит дважды подряд.
AUTO_VERIFY=1
for ARG in "$@"; do
    case "$ARG" in
        --no-auto-verify) AUTO_VERIFY=0 ;;
    esac
done

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
KANBAN_DIR="$PROJECT_DIR/kanban"
REPORT_DATE=$(date +%Y-%m-%d)
# Вне репозитория намеренно (AUDIT-005) — approval-файл не должен быть
# редактируемым тем же коммитом, который он одобряет. Тот же путь, что
# daily-audit.sh печатает пользователю и создаёт с правами 0700.
APPROVAL_DIR="${APPROVAL_DIR:-/home/admin/approvals}"
APPROVAL_FILE="$APPROVAL_DIR/$REPORT_DATE-approval.json"
REPORT_FILE="$AUDIT_DIR/$REPORT_DATE-audit-report.json"
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

    # ============ STEP 0: Verify Approval (AUDIT-005 gate) ============
    # Единственная проверка, что реализация вообще может начаться: схема +
    # HMAC-подпись + сверка approved_issue_ids с сегодняшним отчётом аудита.
    # Раньше этот файл читался напрямую безо всякой аутентификации — ровно та
    # дыра, которую AUDIT-005 был должен закрыть, но фактически закрыл только
    # со стороны генерации (daily-audit.sh), не здесь, в точке потребления.
    echo "🔐 Step 0: Verifying approval file (schema + HMAC + audit-report cross-check)..."
    if ! bash "$PROJECT_DIR/ci/verify-approval.sh" "$APPROVAL_FILE" "$REPORT_FILE"; then
        echo "❌ Approval verification failed — реализация не начнётся" >&2
        exit 1
    fi
    echo "✅ Approval verified"
    echo

    # ============ STEP 1: Parse Approval & Create Kanban ============
    echo "📋 Step 1: Creating Kanban board from approved issues..."
    echo

    # Build Kanban JSON directly with jq — no bash loop, no placeholder to forget substituting
    jq --arg date "$REPORT_DATE" '{
        date: $date,
        total_tasks: (.approved_issues | length),
        tasks: [.approved_issues[] | {id, priority, title, status: "pending"}]
    }' "$APPROVAL_FILE" > "$KANBAN_FILE"

    while IFS= read -r ISSUE; do
        echo "  Task: $(echo "$ISSUE" | jq -r '.title') ($(echo "$ISSUE" | jq -r '.priority'))"
    done < <(jq -c '.approved_issues[]' "$APPROVAL_FILE")

    echo "✅ Kanban created with $(jq '.total_tasks' "$KANBAN_FILE") tasks"
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
    
    # Read each approved issue and implement.
    # Process substitution (not a pipe) keeps TASK_NUM/COMPLETED/FAILED in this
    # shell instead of a subshell copy — otherwise they'd reset to 0 after the loop
    # and the summary below would always print zeros regardless of what happened.
    while IFS= read -r ISSUE; do
        TASK_NUM=$((TASK_NUM + 1))

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
        # Write — план задач часто требует создать новый файл (Edit умеет только
        # редактировать существующие). Bash ограничен ровно теми командами, которые
        # запрашивает промпт выше (шаги 2–7), а не выдан безусловно — это автономный
        # auto-commit контур, и остальные AUDIT-* задачи как раз про то, что ему
        # нельзя доверять больше необходимого.
        # </dev/null — без этого claude наследует stdin от process substitution
        # цикла (`done < <(jq ...)`), может частично вычитать из него, и внешний
        # `read -r ISSUE` получает EOF после первой же итерации: реально
        # обрабатывалась 1 задача из 10, хотя канбан создавался на все 10.
        TASK_ATTEMPT=1
        TASK_RC=1
        while :; do
            # permission-mode dontAsk + явный --disallowedTools: без этого
            # 2026-08-06 задача реально выполнила git commit, хотя её
            # allowedTools ниже git не упоминает вовсе — проектный
            # .claude/settings.local.json несёт накопленные за месяцы
            # интерактивных сессий allow-паттерны (Bash(git commit *),
            # Bash(curl *) без привязки к пути), которые складываются с
            # allowedTools вызова, а не заменяются им. git add/rm оставлены
            # разрешёнными — задаче реально нужно удалять файлы (git rm,
            # см. AUDIT-004 2026-08-06), но ни add, ни rm сами по себе не
            # создают коммит и не публикуют ничего наружу.
            if timeout 600 claude -p "$CLAUDE_PROMPT" \
                --permission-mode dontAsk \
                --allowedTools "Read,Edit,Write,Bash(ls:*),Bash(bash -n:*),Bash(python -m py_compile:*),Bash(pytest:*),Bash(npm test:*),Bash(yamllint:*),Bash(yq:*),Bash(shellcheck:*),Bash(git add:*),Bash(git rm:*),Bash(git status:*),Bash(git diff:*)" \
                --disallowedTools "Bash(git commit*),Bash(git push*),Bash(curl*),Bash(wget*),Bash(sudo*)" \
                --max-turns 20 < /dev/null >> "$LOG_FILE" 2>&1; then
                TASK_RC=0
                break
            fi
            # Проверяем только хвост лога (этот запуск), а не файл целиком —
            # иначе совпадение из давно прошедшей задачи запустило бы ретрай
            # для текущей.
            if claude_should_retry "$TASK_ATTEMPT" "$(tail -c 4000 "$LOG_FILE")"; then
                echo "⏳ Похоже на исчерпанный лимит подписки claude.ai (попытка $TASK_ATTEMPT/$CLAUDE_RATE_LIMIT_MAX_ATTEMPTS) — жду ${CLAUDE_RATE_LIMIT_WAIT_SECONDS}s и повторяю ту же задачу" >> "$LOG_FILE"
                sleep "$CLAUDE_RATE_LIMIT_WAIT_SECONDS"
                TASK_ATTEMPT=$((TASK_ATTEMPT + 1))
                continue
            fi
            break
        done

        if [ "$TASK_RC" -eq 0 ]; then
            echo "✅ Task $TASK_NUM completed"
            COMPLETED=$((COMPLETED + 1))
        else
            echo "⚠️  Task $TASK_NUM timeout or error"
            FAILED=$((FAILED + 1))
        fi

        echo ""
        echo "⏱️  $(date '+%H:%M:%S') — Completed $COMPLETED/$TASK_NUM tasks"
        echo ""
    done < <(jq -c '.approved_issues[]' "$APPROVAL_FILE")
    
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
    if [ "$AUTO_VERIFY" -eq 1 ]; then
        echo "🧪 Running verification & commit..."
        echo

        bash "$PROJECT_DIR/scripts/daily-verify-and-commit.sh"
    else
        echo "⏭️  --no-auto-verify: пропускаю verify-and-commit, вызовет вызывающий скрипт"
    fi
    
} | tee -a "$LOG_FILE"
