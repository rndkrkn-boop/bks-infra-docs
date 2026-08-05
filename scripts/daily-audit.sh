#!/usr/bin/env bash
# daily-audit.sh — Run comprehensive project audit via Claude, produce a
# schema-valid JSON report for user approval.
#
# Single canonical implementation (AUDIT task 6): daily-audit.sh and
# daily-audit-and-wait.sh used to carry two independently drifting copies of
# this prompt with different --max-turns limits. Now there is one script with
# a --wait flag; daily-audit-and-wait.sh is a thin wrapper around it.
#
# Usage: daily-audit.sh [--wait] [--max-wait=SECONDS]
#   (no flags)  Generate the report and exit. Used by daily-cycle-orchestrator.sh,
#               which does its own separate approval wait.
#   --wait      Generate the report, then block until an approval file appears
#               and hand off to daily-implement-now.sh. Used for the "approve
#               and go immediately" flow.

set -euo pipefail

# ~/.bashrc экспортирует ANTHROPIC_API_KEY, который иначе перебивает claude.ai-логин
# у claude -p ниже (платный API вместо аккаунта) — unset влияет только на этот процесс.
# Тот же класс бага, что чинился 2026-08-05 в daily-implement-now.sh/daily-verify*.sh,
# но этот скрипт тогда остался незамеченным — вне области того аудита.
unset ANTHROPIC_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/claude-rate-limit-lib.sh
. "$SCRIPT_DIR/claude-rate-limit-lib.sh"

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
AUDIT_DIR="$PROJECT_DIR/audits"
REPORT_DATE=$(date +%Y-%m-%d)
REPORT_TIME=$(date +%H:%M:%S)
REPORT_FILE="$AUDIT_DIR/$REPORT_DATE-audit-report.json"
RAW_FILE="$AUDIT_DIR/$REPORT_DATE-audit-report.raw.txt"
STDERR_FILE="$AUDIT_DIR/$REPORT_DATE-audit.stderr"
# Вне репозитория намеренно (AUDIT-005) — approval-файл не должен попадать в git.
APPROVAL_DIR="/home/admin/approvals"
APPROVAL_FILE="$APPROVAL_DIR/$REPORT_DATE-approval.json"

WAIT_FOR_APPROVAL=0
MAX_WAIT=28800  # 8 часов; используется только с --wait

for ARG in "$@"; do
    case "$ARG" in
        --wait)
            WAIT_FOR_APPROVAL=1
            ;;
        --max-wait=*)
            MAX_WAIT="${ARG#--max-wait=}"
            ;;
        *)
            echo "Unknown argument: $ARG" >&2
            echo "Usage: $0 [--wait] [--max-wait=SECONDS]" >&2
            exit 2
            ;;
    esac
done

mkdir -p "$AUDIT_DIR"

echo "🔍 Starting Daily Project Audit"
echo "Date: $REPORT_DATE $REPORT_TIME"
echo

# Вложенные каталоги — самостоятельные git-репозитории (см. .gitignore), не
# принадлежат этому аудиту. Без явного запрета обойдут их на диске (gitignore
# на файловый доступ инструментов не влияет), тратя ходы --max-turns и
# подмешивая чужую историю/секреты в отчёт.
#
# Только Read(path) — движок разрешений Claude Code проверяет file-permission
# паттерны исключительно по правилам Read(...); Grep(path)/Glob(path) в
# --disallowedTools не матчатся вообще и заваливают запуск CLI-валидацией
# ("is not matched by file permission checks") ещё до первого хода модели.
# Правило Read(...) уже покрывает Read/Grep/Glob разом.
NESTED_REPOS=(host-infra matrix monitoring MemGraphRAG NemoClaw router sandbox-templates bksamotsvety)
DISALLOWED=""
for REPO in "${NESTED_REPOS[@]}"; do
    DISALLOWED="${DISALLOWED}${DISALLOWED:+,}Read(./${REPO}/**)"
done

AUDIT_CONTEXT=$(cat << CONTEXT_END
Project: nemohermes_bks
Location: $PROJECT_DIR

INSTRUCTIONS FOR CLAUDE:
1. Use Read, Grep and Glob to analyze the project structure comprehensively.
   Do NOT descend into: ${NESTED_REPOS[*]} — these are independent git
   repositories, out of scope for this audit.
2. Check for:
   - Code quality issues (lint, complexity, duplication)
   - Security vulnerabilities (dependencies, secrets, RBAC)
   - Architecture problems (coupling, SPOF, scaling issues)
   - Documentation gaps (missing/outdated docs)
   - Test coverage (missing unit/integration/e2e tests)
   - Performance issues (bottlenecks, inefficiencies)
   - Deployment readiness (CI/CD, monitoring, logging)
   - Team processes (documentation, runbooks, SLOs)

3. For each issue found, produce an object with exactly these fields:
   - id: "AUDIT-XXX" (sequential)
   - priority: one of CRITICAL | HIGH | MEDIUM | LOW
   - title: short title, max 50 characters
   - description: detailed explanation (2-3 sentences)
   - impact: what breaks if not fixed
   - effort: one of QUICK (< 1 hour) | MEDIUM (1-4 hours) | COMPLEX (> 4 hours)
   - implementation: how to fix (specific commands/files to create)

   The report MUST validate against schemas/audit-report.schema.json
   (id/priority/title/effort/implementation are required; priority and
   effort must use the exact enum values above).

4. Output JSON with this structure:
   {
     "audit_date": "$REPORT_DATE",
     "issues": [ { "id": "AUDIT-001", "priority": "CRITICAL", "title": "...",
                   "description": "...", "impact": "...", "effort": "MEDIUM",
                   "implementation": "..." } ],
     "metrics": {
       "total_issues": N,
       "by_priority": {"CRITICAL": X, "HIGH": Y, "MEDIUM": Z, "LOW": W},
       "by_effort": {"QUICK": X, "MEDIUM": Y, "COMPLEX": Z}
     }
   }

5. Output ONLY valid JSON (no other text, no markdown fences).
CONTEXT_END
)

echo "Running Claude audit analysis..."
cd "$PROJECT_DIR"

# stdout и stderr идут в разные потоки: stdout — кандидат в отчёт, stderr —
# диагностика в отдельный файл. Код возврата проверяется явно ($CLAUDE_EXIT),
# без '||' — иначе таймаут/ошибка Claude маскируются под валидный пустой отчёт.
#
# Ретрай на лимите подписки: без ANTHROPIC_API_KEY единственный источник
# инференса — claude.ai-подписка, и её лимит — не ошибка, а повод подождать
# и повторить тот же запрос, а не проваливать аудит на весь день.
set +e
ATTEMPT=1
while :; do
    AUDIT_REPORT=$(timeout 1800 claude -p "$AUDIT_CONTEXT" \
        --add-dir "$PROJECT_DIR" \
        --allowedTools "Read,Grep,Glob" \
        --disallowedTools "$DISALLOWED" \
        --max-turns 40 2>"$STDERR_FILE")
    CLAUDE_EXIT=$?
    if [ "$CLAUDE_EXIT" -eq 0 ]; then
        break
    fi
    if claude_should_retry "$ATTEMPT" "$(cat "$STDERR_FILE" 2>/dev/null)"; then
        echo "⏳ Похоже на исчерпанный лимит подписки claude.ai (попытка $ATTEMPT/$CLAUDE_RATE_LIMIT_MAX_ATTEMPTS) — жду ${CLAUDE_RATE_LIMIT_WAIT_SECONDS}s и повторяю тот же запрос" >&2
        sleep "$CLAUDE_RATE_LIMIT_WAIT_SECONDS"
        ATTEMPT=$((ATTEMPT + 1))
        continue
    fi
    break
done
set -e

if [ -s "$STDERR_FILE" ]; then
    echo "⚠️  Claude stderr (см. $STDERR_FILE):"
    cat "$STDERR_FILE" >&2
fi

if [ "$CLAUDE_EXIT" -ne 0 ]; then
    echo "❌ Claude завершился с кодом $CLAUDE_EXIT — аудит провален" >&2
    printf '%s' "$AUDIT_REPORT" > "$RAW_FILE"
    echo "Сырой вывод сохранён: $RAW_FILE" >&2
    exit 1
fi

# Валидация до записи: в REPORT_FILE попадает только валидный JSON с полем
# .issues. Невалидный вывод (обрезанный JSON, markdown-обёртка, текст ошибки)
# уходит в *.raw.txt для разбора, а не молча становится "отчётом".
if ! printf '%s' "$AUDIT_REPORT" | jq -e '.issues' >/dev/null 2>&1; then
    echo "невалидный отчёт: не JSON или отсутствует поле .issues" >&2
    printf '%s' "$AUDIT_REPORT" > "$RAW_FILE"
    echo "Сырой вывод сохранён: $RAW_FILE" >&2
    exit 1
fi

printf '%s' "$AUDIT_REPORT" > "$REPORT_FILE"

echo "✅ Audit complete"
echo "📄 Report: $REPORT_FILE"
echo
echo "📊 Summary:"
jq '.metrics // (.issues | length)' "$REPORT_FILE" 2>/dev/null || true
echo

if [ "$WAIT_FOR_APPROVAL" -eq 0 ]; then
    echo "⏳ Waiting for your approval..."
    echo "📝 Review at: $REPORT_FILE"
    echo "✅ Approve at: $APPROVAL_FILE (format: JSON with selected issue IDs)"
    exit 0
fi

mkdir -p "$APPROVAL_DIR"
chmod 700 "$APPROVAL_DIR"

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
echo "⏳ Waiting for approval file: $APPROVAL_FILE"
echo

ELAPSED=0
INTERVAL=30
while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    if [ -f "$APPROVAL_FILE" ]; then
        echo
        echo "✅ APPROVAL RECEIVED!"
        echo "🔍 Validating approval file..."

        if ! jq -e '.approved_issue_ids' "$APPROVAL_FILE" >/dev/null 2>&1; then
            echo "❌ Invalid approval JSON or missing approved_issue_ids" >&2
            exit 1
        fi

        INVALID_ISSUES=0
        while IFS= read -r ISSUE_ID; do
            if ! jq -e ".issues[] | select(.id == \"$ISSUE_ID\")" "$REPORT_FILE" >/dev/null 2>&1; then
                echo "❌ Issue $ISSUE_ID not found in audit report" >&2
                INVALID_ISSUES=$((INVALID_ISSUES + 1))
            fi
        done < <(jq -r '.approved_issue_ids[]' "$APPROVAL_FILE")

        if [ "$INVALID_ISSUES" -gt 0 ]; then
            echo "❌ $INVALID_ISSUES invalid issues in approval file" >&2
            exit 1
        fi

        echo "✅ Approval file valid"
        echo "🚀 STARTING IMMEDIATE IMPLEMENTATION..."
        echo
        bash "$PROJECT_DIR/scripts/daily-implement-now.sh"
        exit $?
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))

    if [ $((ELAPSED % 300)) -eq 0 ]; then
        echo "⏳ Still waiting... ($((MAX_WAIT - ELAPSED)) seconds remaining)"
    fi
done

echo "⚠️  TIMEOUT: No approval received after $MAX_WAIT seconds" >&2
exit 1
