#!/usr/bin/env bash
# scan-deps.sh — Local dependency scanning (npm + pip)

set -euo pipefail

echo "🔍 Scanning dependencies for vulnerabilities"
echo "=============================================="
echo

FAILED=0
SCANNED=0

# Scan npm
if [ -f "package.json" ]; then
    SCANNED=$((SCANNED + 1))
    echo "📦 Scanning npm dependencies..."
    if npm audit --audit-level=moderate 2>&1; then
        echo "✅ npm audit passed"
    else
        echo "❌ npm audit found vulnerabilities"
        FAILED=$((FAILED + 1))
    fi
fi

echo ""

# Scan pip: requirements.txt (прод) и/или requirements-dev.txt (тестовые
# зависимости хост-cron прогона, см. AUDIT-004) — в этом репозитории сейчас
# есть только второй.
for REQ_FILE in requirements.txt requirements-dev.txt; do
    if [ -f "$REQ_FILE" ]; then
        SCANNED=$((SCANNED + 1))
        echo "🐍 Scanning pip dependencies ($REQ_FILE)..."
        if pip-audit -r "$REQ_FILE" 2>&1; then
            echo "✅ pip audit passed ($REQ_FILE)"
        else
            echo "❌ pip audit found vulnerabilities ($REQ_FILE)"
            FAILED=$((FAILED + 1))
        fi
        echo ""
    fi
done

echo "=============================================="

# Отсутствие манифеста — ошибка конфигурации, а не тихий успех: раньше
# "нечего сканировать" и "всё чисто" выглядели в логе одинаково (AUDIT-009).
if [ "$SCANNED" -eq 0 ]; then
    echo "❌ Нечего сканировать: не найдено ни package.json, ни requirements.txt/requirements-dev.txt" >&2
    exit 1
fi

if [ $FAILED -eq 0 ]; then
    echo "✅ All scans passed ($SCANNED manifest(s))"
    exit 0
else
    echo "❌ $FAILED scan(s) failed"
    exit 1
fi
