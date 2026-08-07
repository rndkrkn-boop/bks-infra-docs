#!/usr/bin/env bash
# daily-cycle-orchestrator.sh — Main orchestrator for daily audit→implement→verify cycle
# Coordinates timing, Telegram notifications, and approval workflow

set -euo pipefail

PROJECT_DIR="/home/admin/projects/nemohermes_bks"
SCRIPTS_DIR="$PROJECT_DIR/scripts"
AUDIT_DIR="$PROJECT_DIR/audits"
CYCLE_LOG="$AUDIT_DIR/cycle.log"

REPORT_DATE=$(date +%Y-%m-%d)
REPORT_TIME=$(date +%H:%M:%S)

mkdir -p "$AUDIT_DIR"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$CYCLE_LOG"
}

send_telegram() {
    local MSG="$1"
    
    # Проверяем наличие необходимых переменных окружения
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        # Если переменные не установлены, логируем как warning, но не падаем
        log "⚠️  Telegram notification skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set): $MSG"
        return 0
    fi
    
    # Используем Telegram Bot API для отправки сообщения
    # API endpoint: https://api.telegram.org/bot<TOKEN>/sendMessage
    local API_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
    local PAYLOAD
    PAYLOAD=$(cat <<EOF
{
    "chat_id": ${TELEGRAM_CHAT_ID},
    "text": "${MSG}",
    "parse_mode": "HTML"
}
EOF
)
    
    # Отправляем сообщение через curl
    if curl -s -X POST "$API_URL" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" \
        -m 10 \
        > /dev/null 2>&1; then
        log "📤 Telegram notification sent: $MSG"
        return 0
    else
        # Логируем ошибку, но не прерываем цикл
        log "⚠️  Failed to send Telegram notification: $MSG"
        return 1
    fi
}

# ============ PHASE 1: AUDIT (10:00) ============
audit_phase() {
    log "🔍 PHASE 1: AUDIT STARTED"
    
    if bash "$SCRIPTS_DIR/daily-audit.sh"; then
        log "✅ AUDIT PHASE COMPLETE"
        send_telegram "🔍 Daily audit complete. Review report and approve improvements."
        return 0
    else
        log "❌ AUDIT PHASE FAILED"
        return 1
    fi
}

# ============ PHASE 2: WAIT FOR APPROVAL (10:00 - 17:00) ============
wait_approval_phase() {
    log "⏳ PHASE 2: WAITING FOR APPROVAL"
    
    # Тот же путь, что daily-audit.sh печатает пользователю и daily-implement-now.sh
    # проверяет (AUDIT-005) — раньше здесь была независимая копия старого,
    # in-repo пути, и approval, положенный туда, куда просил daily-audit.sh,
    # эта проверка никогда бы не увидела.
    APPROVAL_DIR="${APPROVAL_DIR:-/home/admin/approvals}"
    APPROVAL_FILE="$APPROVAL_DIR/$REPORT_DATE-approval.json"
    MAX_WAIT=25200  # 7 hours
    ELAPSED=0
    INTERVAL=300    # Check every 5 minutes
    
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if [ -f "$APPROVAL_FILE" ]; then
            log "✅ APPROVAL RECEIVED"
            send_telegram "✅ Improvements approved. Starting implementation at 18:00."
            return 0
        fi
        
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))
    done
    
    log "⚠️  NO APPROVAL RECEIVED (timeout)"
    return 1
}

# ============ PHASE 3: IMPLEMENT (18:00) ============
implement_phase() {
    log "🚀 PHASE 3: IMPLEMENTATION STARTED"
    
    if bash "$SCRIPTS_DIR/daily-implement.sh"; then
        log "✅ IMPLEMENTATION PHASE COMPLETE"
        send_telegram "🚀 Improvements implemented. Running final verification..."
        return 0
    else
        log "⚠️  IMPLEMENTATION PHASE PARTIAL"
        return 0  # Continue to verification anyway
    fi
}

# ============ PHASE 4: VERIFY & COMMIT (20:00) ============
verify_phase() {
    log "🧪 PHASE 4: VERIFICATION & COMMIT STARTED"
    
    if bash "$SCRIPTS_DIR/daily-verify.sh"; then
        log "✅ VERIFY PHASE COMPLETE"
        # Конкретный счётчик тестов и хеш прогона — в git commit message и
        # $CYCLE_LOG (daily-verify.sh пишет их туда же); здесь только факт
        # результата, без повторения статичной фразы "All tests passed".
        send_telegram "✅ Verification gate passed (tests + ruff). Changes committed — details in $CYCLE_LOG."
        return 0
    else
        log "⚠️  VERIFY PHASE FAILED"
        send_telegram "❌ Verification failed. Review logs at $CYCLE_LOG"
        return 1
    fi
}

# ============ MAIN ORCHESTRATION ============
main() {
    log "════════════════════════════════════════════════════════════"
    log "🔄 DAILY AUDIT CYCLE STARTED"
    log "Date: $REPORT_DATE Time: $REPORT_TIME"
    log "════════════════════════════════════════════════════════════"
    
    # Phase 1: Audit
    if ! audit_phase; then
        log "❌ CYCLE FAILED AT AUDIT PHASE"
        exit 1
    fi
    
    # Phase 2: Wait for approval
    if ! wait_approval_phase; then
        log "⚠️  CYCLE PAUSED: AWAITING USER APPROVAL"
        exit 0
    fi
    
    # Phase 3: Implement
    implement_phase
    
    # Phase 4: Verify & Commit
    verify_phase
    
    log "════════════════════════════════════════════════════════════"
    log "✅ DAILY AUDIT CYCLE COMPLETE"
    log "════════════════════════════════════════════════════════════"
}

# Run main
main
