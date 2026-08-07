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
# shellcheck source=scripts/claude-task-orchestrator-lib.sh
. "$SCRIPT_DIR/claude-task-orchestrator-lib.sh"

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
        
        # ============ CLAUDE TASK: IMPLEMENT + TEST (многоходово, --resume) ============
        # 2026-08-06: заменено с одного claude -p --max-turns 20 на воркер-сессию
        # через claude-task-orchestrator-lib.sh — сложные многофайловые задачи
        # регулярно упирались в "Reached max turns" и терялись, хотя частичные
        # правки уже были на диске. См. план в памяти
        # claude-task-orchestrator-resume (--resume вместо фиксированного бюджета
        # ходов) и claude-code-allowedtools-not-isolated (почему permission-флаги
        # передаются на каждый вызов заново).
        echo "🤖 Executing via Claude (multi-turn session with embedded testing)..."
        echo

        if run_task_via_worker_session "$ISSUE_ID" "$PRIORITY" "$TITLE" "$IMPL" "$LOG_FILE"; then
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
