#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# compliance-report.sh — один вход для регулярного комплаенс-отчёта.
#
# Используется и CI-джобом `compliance-audit`, и host-таймером: одна и та же
# логика в обоих местах, чтобы «в CI зелено» и «на хосте зелено» означали одно
# и то же (тот же принцип, что у ci/cosign-lib.sh).
#
# Собирает четыре артефакта:
#   report.json    — машиночитаемый отчёт (вход дашборда и внешних систем)
#   report.md      — человекочитаемый отчёт для ревью и приложения к аудиту
#   dashboard.html — самодостаточный дашборд, открывается без сети
#   *.prom         — метрики bks_compliance_* для Prometheus (опционально)
#
# Переменные окружения:
#   COMPLIANCE_OUT_DIR     куда складывать артефакты (по умолчанию ./.compliance-out)
#   COMPLIANCE_METRICS_DIR textfile-каталог node_exporter; пусто — метрики не пишем
#   COMPLIANCE_GATE        off — не блокировать вызывающего (режим наблюдения)
#   COMPLIANCE_NOW         дата прогона YYYY-MM-DD (для воспроизводимых прогонов)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${COMPLIANCE_OUT_DIR:-$ROOT/.compliance-out}"
METRICS_DIR="${COMPLIANCE_METRICS_DIR:-}"
mkdir -p "$OUT_DIR"

args=(
  --root "$ROOT"
  --json-out "$OUT_DIR/report.json"
  --md-out "$OUT_DIR/report.md"
)
[ -n "${COMPLIANCE_NOW:-}" ] && args+=(--now "$COMPLIANCE_NOW")
[ "${COMPLIANCE_GATE:-on}" = "off" ] && args+=(--no-gate)

# Метрики пишем во временный файл и переносим mv-ом: textfile collector читает
# каталог по своему таймеру и покажет полуфайл как битую экспозицию, если
# писать в целевой файл напрямую.
metrics_tmp=""
if [ -n "$METRICS_DIR" ]; then
  mkdir -p "$METRICS_DIR"
  metrics_tmp="$METRICS_DIR/.bks_compliance.prom.$$"
  args+=(--metrics-out "$metrics_tmp")
fi

set +e
python3 "$ROOT/scripts/compliance-audit.py" "${args[@]}"
audit_rc=$?
set -e

# Код 2 — сломан сам инструмент: отчёта нет, публиковать нечего.
if [ "$audit_rc" -ge 2 ]; then
  [ -n "$metrics_tmp" ] && rm -f "$metrics_tmp"
  echo "compliance-report: аудит не отработал (rc=$audit_rc)" >&2
  exit "$audit_rc"
fi

python3 "$ROOT/scripts/compliance-dashboard.py" \
  "$OUT_DIR/report.json" --out "$OUT_DIR/dashboard.html"

if [ -n "$metrics_tmp" ]; then
  mv -f "$metrics_tmp" "$METRICS_DIR/bks_compliance.prom"
  chmod 0644 "$METRICS_DIR/bks_compliance.prom"
  echo "compliance-report: метрики -> $METRICS_DIR/bks_compliance.prom"
fi

echo "compliance-report: артефакты в $OUT_DIR (rc=$audit_rc)"
exit "$audit_rc"
