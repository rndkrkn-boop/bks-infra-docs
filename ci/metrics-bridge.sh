#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# metrics-bridge.sh — один вход для регулярного обновления textfile-метрик.
#
# Используется и host-таймером (bks-metrics-bridge.timer), и CI: одна и та же
# логика в обоих местах, чтобы «в CI зелено» и «на хосте зелено» означали одно и
# то же. Тот же принцип, что у ci/compliance-report.sh и ci/cosign-lib.sh.
#
# Что делает:
#   1. Готовит JSON-сводку бэкапа (её умеет только backup-manager.py — он
#      единственный знает, что такое полный снапшот и свежий restore-drill).
#   2. Запускает scripts/metrics-bridge.py, который раскладывает .prom-файлы.
#   3. Если доступен promtool — проверяет получившуюся экспозицию.
#
# Переменные окружения:
#   METRICS_OUT_DIR   куда писать .prom (по умолчанию /var/lib/node_exporter/textfile)
#   METRICS_SOURCES   путь к каталогу источников (по умолчанию metrics/sources.toml)
#   METRICS_STRICT    on — вернуть 1, если источник недоступен или протух
#   BACKUP_ROOT       корень хранилища бэкапов (по умолчанию из retention.toml)
#   PROMTOOL          путь к promtool; пусто — проверка экспозиции пропускается
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${METRICS_OUT_DIR:-/var/lib/node_exporter/textfile}"
SOURCES="${METRICS_SOURCES:-$ROOT/metrics/sources.toml}"
PROMTOOL_BIN="${PROMTOOL:-$(command -v promtool || true)}"

mkdir -p "$OUT_DIR"

# ── 1. Сводка бэкапа ────────────────────────────────────────────────────────
# Пишем во временный файл и переносим mv-ом: мост может читать каталог в любой
# момент, а полуфайл он справедливо сочтёт битым JSON.
#
# rc=2 у status означает «инструмент не смог» — чаще всего хранилище ещё в
# плоской раскладке v1 (однократная миграция не выполнена). Это НЕ повод ронять
# прогон: мост отдаст bks_metrics_bridge_source_ok=0 по источнику backup, и
# отсутствие данных станет видно в Prometheus, а не в чужом exit code.
backup_tmp="$OUT_DIR/.backup-status.json.$$"
backup_args=(--policy "$ROOT/backup/retention.toml" status --json)
[ -n "${BACKUP_ROOT:-}" ] && backup_args+=(--root "$BACKUP_ROOT")

set +e
python3 "$ROOT/scripts/backup-manager.py" "${backup_args[@]}" > "$backup_tmp" 2>/dev/null
backup_rc=$?
set -e

if [ "$backup_rc" -le 1 ] && [ -s "$backup_tmp" ]; then
  mv -f "$backup_tmp" "$OUT_DIR/backup-status.json"
  chmod 0644 "$OUT_DIR/backup-status.json"
  echo "metrics-bridge: сводка бэкапа обновлена (rc=$backup_rc)"
else
  rm -f "$backup_tmp"
  echo "metrics-bridge: сводка бэкапа недоступна (rc=$backup_rc) — источник backup останется без данных" >&2
fi

# ── 2. Мост ─────────────────────────────────────────────────────────────────
bridge_args=(--config "$SOURCES" --out-dir "$OUT_DIR")
[ "${METRICS_STRICT:-off}" = "on" ] && bridge_args+=(--strict)

set +e
python3 "$ROOT/scripts/metrics-bridge.py" "${bridge_args[@]}"
bridge_rc=$?
set -e

# rc=2 — сломан сам инструмент (нет каталога источников, битый TOML): файлов нет,
# проверять нечего.
if [ "$bridge_rc" -ge 2 ]; then
  echo "metrics-bridge: мост не отработал (rc=$bridge_rc)" >&2
  exit "$bridge_rc"
fi

# ── 3. Проверка экспозиции ──────────────────────────────────────────────────
# Одна кривая строка поднимает node_textfile_scrape_error=1, и node_exporter
# перестаёт отдавать файл ЦЕЛИКОМ — то есть гасит все метрики источника сразу.
# Дешевле проверить здесь, чем искать потом пустую панель.
if [ -n "$PROMTOOL_BIN" ]; then
  for f in "$OUT_DIR"/*.prom; do
    [ -e "$f" ] || continue
    if "$PROMTOOL_BIN" check metrics < "$f" > /dev/null; then
      echo "metrics-bridge: экспозиция OK — $(basename "$f")"
    else
      echo "metrics-bridge: экспозиция НЕВАЛИДНА — $f" >&2
      exit 1
    fi
  done
else
  echo "metrics-bridge: promtool недоступен, проверка экспозиции пропущена" >&2
fi

echo "metrics-bridge: готово, файлы в $OUT_DIR (rc=$bridge_rc)"
exit "$bridge_rc"
