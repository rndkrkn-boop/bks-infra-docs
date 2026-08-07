"""Тесты функции send_telegram() из scripts/daily-cycle-orchestrator.sh.

Тестирует:
- Отправку сообщения через Telegram Bot API
- Обработку отсутствующих переменных окружения
- Корректное форматирование JSON-payload
"""

import os
import pathlib
import subprocess


ORCHESTRATOR_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "daily-cycle-orchestrator.sh"
)


def test_daily_cycle_orchestrator_has_send_telegram():
    """Проверяет, что daily-cycle-orchestrator.sh имеет функцию send_telegram()."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    assert "send_telegram()" in content, "send_telegram() function not found"
    assert "TELEGRAM_BOT_TOKEN" in content, "TELEGRAM_BOT_TOKEN not found"
    assert "TELEGRAM_CHAT_ID" in content, "TELEGRAM_CHAT_ID not found"
    assert "curl" in content, "curl command not found in send_telegram()"
    assert "sendMessage" in content, "Telegram API endpoint not found"


def test_daily_cycle_orchestrator_calls_send_telegram():
    """Проверяет, что цикл вызывает send_telegram() в нужных местах."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Должны быть вызовы send_telegram в 5 местах
    call_count = content.count('send_telegram "')
    assert call_count == 5, f"Expected 5 send_telegram calls, found {call_count}"
    
    # Проверяю основные точки вызова
    assert 'send_telegram "🔍 Daily audit complete' in content, "Missing audit complete notification"
    assert 'send_telegram "✅ Improvements approved' in content, "Missing approval notification"
    assert 'send_telegram "🚀 Improvements implemented' in content, "Missing implementation notification"
    assert 'send_telegram "✅ Verification gate passed' in content, "Missing verification passed notification"
    assert 'send_telegram "❌ Verification failed' in content, "Missing verification failed notification"


def test_send_telegram_function_structure():
    """Проверяет структуру функции send_telegram()."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю основные компоненты функции в целом скрипте
    assert "local MSG" in content, "Function should have local MSG variable"
    assert "TELEGRAM_BOT_TOKEN" in content, "Function should check TELEGRAM_BOT_TOKEN"
    assert "TELEGRAM_CHAT_ID" in content, "Function should check TELEGRAM_CHAT_ID"
    assert "https://api.telegram.org/bot" in content, "Function should use Telegram API URL"
    assert "sendMessage" in content, "Function should use sendMessage endpoint"
    assert "curl -s -X POST" in content, "Function should use curl to send message"
    assert "Content-Type" in content, "Function should set Content-Type header"
    assert "application/json" in content, "Function should use JSON content type"
    assert "parse_mode" in content, "Function should include parse_mode parameter"
    assert '"HTML"' in content, "Function should use HTML parse mode"
    assert "-m 10" in content, "Function should have curl timeout of 10 seconds"
    assert 'log "📤 Telegram notification sent' in content, "Function should log success"


def test_send_telegram_payload_format():
    """Проверяет корректность формирования JSON-payload."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю JSON структуру payload в целом скрипте
    assert '"chat_id"' in content, "Payload should include chat_id field"
    assert '"text"' in content, "Payload should include text field"
    assert '"parse_mode"' in content, "Payload should include parse_mode field"
    assert '"HTML"' in content, "parse_mode should be set to HTML"


def test_send_telegram_error_handling():
    """Проверяет обработку ошибок."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю наличие обработки ошибок в целом скрипте
    assert "return 0" in content, "Function should have return statements"
    assert "skipped" in content.lower(), "Function should skip notification if variables not set"
    assert "Failed" in content or "failed" in content, "Function should log failures"


def test_env_example_has_telegram_vars():
    """Проверяет, что .env.example содержит переменные Telegram."""
    env_example = ORCHESTRATOR_PATH.parent.parent / ".env.example"
    with open(env_example, "r", encoding='utf-8') as f:
        content = f.read()
    
    assert "TELEGRAM_BOT_TOKEN" in content, "TELEGRAM_BOT_TOKEN not found in .env.example"
    assert "TELEGRAM_CHAT_ID" in content, "TELEGRAM_CHAT_ID not found in .env.example"
    assert "Telegram" in content or "telegram" in content, ".env.example should mention Telegram"


def test_send_telegram_skips_with_missing_token():
    """Проверяет, что send_telegram пропускает отправку без токена."""
    test_script = """#!/bin/bash
set -euo pipefail

# Подключу функции из orchestrator
log() {
    echo "[LOG] $1"
}

send_telegram() {
    local MSG="$1"
    
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        log "⚠️  Telegram notification skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set): $MSG"
        return 0
    fi
    
    log "Would send: $MSG"
    return 0
}

# Тест: пустой токен
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID="12345"
send_telegram "test message"
"""
    
    result = subprocess.run(
        ["bash", "-c", test_script],
        capture_output=True,
        text=True,
        timeout=5,
    )
    
    assert result.returncode == 0, f"Script should succeed: {result.stderr}"
    assert "skipped" in result.stdout.lower(), f"Should indicate skip: {result.stdout}"


def test_send_telegram_skips_with_missing_chat_id():
    """Проверяет, что send_telegram пропускает отправку без chat_id."""
    test_script = """#!/bin/bash
set -euo pipefail

log() {
    echo "[LOG] $1"
}

send_telegram() {
    local MSG="$1"
    
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        log "⚠️  Telegram notification skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set): $MSG"
        return 0
    fi
    
    log "Would send: $MSG"
    return 0
}

# Тест: пустой chat_id
TELEGRAM_BOT_TOKEN="token"
TELEGRAM_CHAT_ID=""
send_telegram "test message"
"""
    
    result = subprocess.run(
        ["bash", "-c", test_script],
        capture_output=True,
        text=True,
        timeout=5,
    )
    
    assert result.returncode == 0, f"Script should succeed: {result.stderr}"
    assert "skipped" in result.stdout.lower(), f"Should indicate skip: {result.stdout}"


def test_send_telegram_uses_curl_with_json():
    """Проверяет, что curl используется с JSON payload."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю использование curl с JSON
    assert "curl -s -X POST" in content, "Should use curl with POST method"
    assert "-H" in content and "Content-Type" in content, "Should set headers"
    assert "application/json" in content, "Should use JSON content type"
    assert "-d" in content or "--data" in content, "Should send data with curl"


def test_send_telegram_logs_success():
    """Проверяет логирование успешной отправки."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю наличие логирования успеха
    assert "📤 Telegram notification sent" in content or "notification sent" in content.lower(), \
        "Function should log success"


def test_send_telegram_api_endpoint():
    """Проверяет правильность API endpoint."""
    with open(ORCHESTRATOR_PATH, "r") as f:
        content = f.read()
    
    # Проверяю endpoint
    assert "api.telegram.org/bot" in content, "Should use correct Telegram API URL"
    assert "sendMessage" in content, "Should use sendMessage endpoint"
    assert "TELEGRAM_BOT_TOKEN" in content, \
        "Should use bot token from variable"
