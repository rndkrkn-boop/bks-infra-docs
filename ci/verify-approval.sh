#!/usr/bin/env bash
# verify-approval.sh — тонкая обёртка над scripts/verify-approval.py (тот же
# паттерн, что ci/compliance-report.sh → scripts/compliance-audit.py).
#
# Гейт стоит ДО первой правки рабочего дерева в цикле daily-implement*.sh:
# несовпадение схемы, HMAC-подписи или сверки approved_issue_ids с отчётом
# аудита → exit 1, реализация не стартует.
#
# Переменные окружения:
#   APPROVAL_HMAC_KEY   обязателен — ключ из GitLab CI Variables. Без него
#                       скрипт падает (fail closed), а не пропускает проверку.
#
# Использование:
#   bash ci/verify-approval.sh <approval.json> <audit-report.json>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <approval_file> <audit_report_file>" >&2
    exit 2
fi

exec python3 "$REPO_DIR/scripts/verify-approval.py" verify "$1" "$2"
