#!/usr/bin/env bash
# claude-rate-limit-lib.sh — общая логика ожидания лимита подписки claude.ai
# для всех claude -p вызовов ежедневного цикла (daily-audit.sh,
# daily-implement-now.sh, daily-verify.sh, daily-verify-and-commit.sh).
#
# ANTHROPIC_API_KEY нигде в этом конвейере не используется (см. unset в
# каждом daily-*.sh) — единственный источник инференса это claude.ai-подписка.
# Когда её лимит исчерпан, правильная реакция — подождать и повторить ТОТ ЖЕ
# вызов, а не считать задачу проваленной и не переключаться на платный API
# (которым 2026-08-05 конвейер уже упирался в "Credit balance is too low").
#
# Источником в каждый вызывающий скрипт:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   . "$SCRIPT_DIR/claude-rate-limit-lib.sh"

CLAUDE_RATE_LIMIT_WAIT_SECONDS="${CLAUDE_RATE_LIMIT_WAIT_SECONDS:-1800}"   # 30 минут между попытками
CLAUDE_RATE_LIMIT_MAX_ATTEMPTS="${CLAUDE_RATE_LIMIT_MAX_ATTEMPTS:-48}"     # до ~24 часов суммарного ожидания

# Признаки того, что claude -p упал именно из-за исчерпанного лимита
# подписки, а не из-за реальной ошибки (невалидный промпт, сбой сети и т.п.).
# Best-effort набор фраз: сформулирован по документированным паттернам
# сообщений об usage-лимитах, но ещё не проверен на живом срабатывании лимита
# в этом конвейере — уточнить формулировку по факту первого реального case.
claude_is_rate_limited() {
    printf '%s' "$1" | grep -qiE \
        'usage limit|rate limit|try again (later|in)|resets? (at|in)|quota exceeded|too many requests|limit will reset'
}

# claude_should_retry ATTEMPT OUTPUT_TEXT
# Возвращает 0 (можно повторить), если попытка не последняя И вывод похож на
# лимит подписки. Логирует решение в stderr вызывающего.
claude_should_retry() {
    local attempt="$1" output="$2"
    if [ "$attempt" -ge "$CLAUDE_RATE_LIMIT_MAX_ATTEMPTS" ]; then
        return 1
    fi
    claude_is_rate_limited "$output"
}
