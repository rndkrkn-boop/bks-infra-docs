#!/usr/bin/env bash
# verify-ready-lib.sh — функция для проверки READY-статуса верификации.
#
# Содержит `verify_ready()` — детерминированную bash-логику, которая проверяет
# готовность к коммиту на основе кода возврата claude и JSON-ответа верификации.
#
# Используется:
#   - В scripts/daily-verify.sh, scripts/daily-verify-and-commit.sh (bash)
#   - В tests/test_ready_logic.py через subprocess (Python)
#
# Логика: READY=true ТОЛЬКО если И claude завершился с кодом 0, И stdout —
# валидный JSON, И .ready_to_commit СТРОГО булево true (не строка "true" и не
# другое truthy-значение). Это fail-closed поведение: любой сбой → не коммитить.

# verify_ready CLAUDE_EXIT VERIFICATION_JSON
# Единственный источник истины о готовности к коммиту.
# Аргументы:
#   $1 — код возврата claude (int)
#   $2 — stdout верификации в виде JSON-строки
#
# Печатает в stdout:
#   "true"  — все проверки прошли, можно коммитить
#   "false" — любая из проверок не прошла, коммитить нельзя
#
# Возвращает успешный код (0) всегда — логика в stdout, не в exit-коде.
verify_ready() {
    local claude_exit="$1"
    local verification_json="$2"

    # Fail-closed: READY=false до тех пор, пока не доказаны И нулевой код возврата,
    # И валидный JSON, И буквальное `true` в .ready_to_commit.
    if [ "$claude_exit" -ne 0 ]; then
        echo "false"
        return 0
    fi

    # Проверяем валидность JSON
    if ! printf '%s' "$verification_json" | jq -e . >/dev/null 2>&1; then
        echo "false"
        return 0
    fi

    # Сравнение внутри jq (`== true`) отсекает строку "true" и любое
    # truthy-значение другого типа — разрешением считается только булев литерал.
    local result
    result=$(printf '%s' "$verification_json" | jq -r 'if .ready_to_commit == true then "true" else "false" end')
    echo "$result"
    return 0
}

# Экспортируем, если скрипт был sourced (вместо прямого запуска)
# shellcheck disable=SC2034
export -f verify_ready 2>/dev/null || true
