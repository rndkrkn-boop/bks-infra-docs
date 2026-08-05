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
    # TODO: Integrate Telegram notification
    echo "📤 [TODO] Send to Telegram: $MSG"
}

# ============ PHASE 1: AUDIT (10:00) ============
audit_phase() {
    log "🔍 PHASE 1: AUDIT STARTED"
    
    bash "$SCRIPTS_DIR/daily-audit.sh"
    
    if [ $? -eq 0 ]; then
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
    
    APPROVAL_FILE="$AUDIT_DIR/$REPORT_DATE-approval.json"
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
    
    bash "$SCRIPTS_DIR/daily-implement.sh"
    
    if [ $? -eq 0 ]; then
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
    
    bash "$SCRIPTS_DIR/daily-verify.sh"
    
    if [ $? -eq 0 ]; then
        log "✅ VERIFY PHASE COMPLETE"
        send_telegram "✅ All tests passed. Changes committed to git."
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
