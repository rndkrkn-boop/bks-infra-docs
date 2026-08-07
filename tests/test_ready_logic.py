"""Тесты fail-open гейта верификации (scripts/daily-verify.sh, scripts/daily-verify-and-commit.sh).

Раньше этот гейт проверялся только вручную через scripts/_test-ready-logic.sh —
bash-скрипт, который печатал четыре кейса на stdout и не имел ни одного assert,
поэтому регрессия (например, случайно ослабленное условие) не завалила бы CI.

2026-08-07: переписано на subprocess-вызов реальной bash-функции verify_ready()
из scripts/verify-ready-lib.sh. Теперь тест проверяет РЕАЛЬНУЮ логику в bash,
а не отдельную Python-копию — регрессия в daily-verify*.sh БУДЕТ поймана CI.

Логика: READY=true ТОЛЬКО если И claude завершился с кодом 0, И stdout — валидный JSON,
И .ready_to_commit СТРОГО булево true (не строка "true" и не другое truthy-значение).
"""

import json
import pathlib
import subprocess


LIB_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "verify-ready-lib.sh"


def ready(claude_exit: int, verification_json: str) -> str:
    """Вызывает verify_ready() из bash через subprocess.
    
    Аргументы:
        claude_exit: код возврата claude (int)
        verification_json: JSON-строка с результатом верификации
    
    Возвращает:
        "true" или "false" в зависимости от проверок
    """
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB_PATH}"; verify_ready "$1" "$2"',
         "_", str(claude_exit), verification_json],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_invalid_json_is_not_ready():
    """Невалидный JSON → не готово."""
    assert ready(0, "this is not json at all {broken") == "false"


def test_ready_to_commit_false_is_not_ready():
    """ready_to_commit=false → не готово."""
    verification = json.dumps({"status": "at_risk", "issues": ["x"], "ready_to_commit": False})
    assert ready(0, verification) == "false"


def test_nonzero_exit_with_valid_true_is_not_ready():
    """Ненулевой код возврата claude → не готово, даже если JSON валиден и ready_to_commit=true."""
    verification = json.dumps({"status": "healthy", "ready_to_commit": True})
    assert ready(1, verification) == "false"


def test_valid_json_with_ready_to_commit_true_is_ready():
    """Валидный JSON + код 0 + ready_to_commit=true → готово."""
    verification = json.dumps({"status": "healthy", "issues": [], "ready_to_commit": True})
    assert ready(0, verification) == "true"


def test_ready_to_commit_as_string_true_is_not_ready():
    """Строка "true" — не булево true; проверка отсекает это."""
    verification = json.dumps({"status": "healthy", "ready_to_commit": "true"})
    assert ready(0, verification) == "false"
