"""Тесты fail-open гейта верификации (scripts/daily-verify.sh, scripts/daily-verify-and-commit.sh).

Раньше этот гейт проверялся только вручную через scripts/_test-ready-logic.sh —
bash-скрипт, который печатал четыре кейса на stdout и не имел ни одного assert,
поэтому регрессия (например, случайно ослабленное условие) не завалила бы CI.
compute_ready() ниже — та же логика, что в обоих daily-verify*.sh: READY=true
только если И claude завершился с кодом 0, И stdout — валидный JSON, И
.ready_to_commit СТРОГО булево true (не строка "true" и не другое truthy-значение).
Если гейт в daily-verify*.sh изменится, эту копию нужно обновить вместе с ним.
"""

import json


def compute_ready(verification_stdout: str, claude_exit: int) -> bool:
    if claude_exit != 0:
        return False
    try:
        parsed = json.loads(verification_stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    return parsed.get("ready_to_commit") is True


def test_invalid_json_is_not_ready():
    assert compute_ready("this is not json at all {broken", 0) is False


def test_ready_to_commit_false_is_not_ready():
    verification = json.dumps({"status": "at_risk", "issues": ["x"], "ready_to_commit": False})
    assert compute_ready(verification, 0) is False


def test_nonzero_exit_with_valid_true_is_not_ready():
    verification = json.dumps({"status": "healthy", "ready_to_commit": True})
    assert compute_ready(verification, 1) is False


def test_valid_json_with_ready_to_commit_true_is_ready():
    verification = json.dumps({"status": "healthy", "issues": [], "ready_to_commit": True})
    assert compute_ready(verification, 0) is True


def test_ready_to_commit_as_string_true_is_not_ready():
    """Строка "true" — не булево true; jq `== true` в продакшен-скрипте это отсекает."""
    verification = json.dumps({"status": "healthy", "ready_to_commit": "true"})
    assert compute_ready(verification, 0) is False
