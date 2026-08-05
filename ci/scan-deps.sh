#!/usr/bin/env bash
# scan-deps.sh — Local dependency scanning (npm + pip)

set -euo pipefail

echo "🔍 Scanning dependencies for vulnerabilities"
echo "=============================================="
echo

FAILED=0

# Scan npm
if [ -f "package.json" ]; then
    echo "📦 Scanning npm dependencies..."
    if npm audit --audit-level=moderate 2>&1; then
        echo "✅ npm audit passed"
    else
        echo "❌ npm audit found vulnerabilities"
        FAILED=$((FAILED + 1))
    fi
fi

echo ""

# Scan pip
if [ -f "requirements.txt" ]; then
    echo "🐍 Scanning pip dependencies..."
    if pip-audit 2>&1; then
        echo "✅ pip audit passed"
    else
        echo "❌ pip audit found vulnerabilities"
        FAILED=$((FAILED + 1))
    fi
fi

echo ""
echo "=============================================="
if [ $FAILED -eq 0 ]; then
    echo "✅ All scans passed"
    exit 0
else
    echo "❌ $FAILED scan(s) failed"
    exit 1
fi
