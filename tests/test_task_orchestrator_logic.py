"""Тесты детерминированной логики решений воркер-сессии
(scripts/claude-task-orchestrator-lib.sh, orchestrator_decide).

2026-08-06: daily-implement-now.sh перешёл с одного claude -p --max-turns 20
на сессию через --session-id/--resume — "кончились ходы" перестаёт быть
автоматическим провалом задачи. orchestrator_decide — единственное место,
решающее "продолжать / готово / спросить оркестратора / бросить", поэтому
тестируется как чистая функция через реальный bash (subprocess), а не
переписывается на Python: в отличие от compute_ready в test_ready_logic.py,
здесь нет инлайновой копии логики в другом скрипте — она живёт только тут,
дублировать нечего.
"""

import pathlib
import subprocess

LIB_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "claude-task-orchestrator-lib.sh"


def decide(status: str, diff_changed: str, stagnant_count: str, attempt: str, max_attempts: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB_PATH}"; orchestrator_decide "$1" "$2" "$3" "$4" "$5"',
         "_", status, diff_changed, stagnant_count, attempt, max_attempts],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_done_completes_regardless_of_diff():
    assert decide("done", "0", "0", "1", "5") == "complete"
    assert decide("done", "1", "0", "1", "5") == "complete"


def test_blocked_asks_orchestrator():
    assert decide("blocked", "1", "0", "1", "5") == "ask_orchestrator"
    assert decide("blocked", "0", "3", "2", "5") == "ask_orchestrator"


def test_in_progress_with_progress_nudges():
    assert decide("in_progress", "1", "0", "1", "5") == "nudge"


def test_in_progress_stagnant_below_threshold_nudges():
    # Порог по умолчанию — 2 подряд без изменений; на первой стагнации ещё nudge.
    assert decide("in_progress", "0", "1", "2", "5") == "nudge"


def test_in_progress_stagnant_at_threshold_abandons():
    assert decide("in_progress", "0", "2", "3", "5") == "abandon"


def test_call_failed_treated_like_in_progress_for_stagnation():
    assert decide("call_failed", "1", "0", "1", "5") == "nudge"
    assert decide("call_failed", "0", "2", "3", "5") == "abandon"


def test_done_on_the_last_allowed_attempt_still_completes():
    # Регресс-кейс: лимит попыток ограничивает число ХОДОВ, а не отменяет уже
    # случившийся результат — если работа реально завершилась на последней
    # разрешённой попытке, это успех, а не abandon.
    assert decide("done", "1", "0", "5", "5") == "complete"
    assert decide("done", "0", "0", "5", "5") == "complete"


def test_last_attempt_abandons_when_not_done():
    assert decide("in_progress", "1", "0", "5", "5") == "abandon"
    assert decide("blocked", "0", "0", "5", "5") == "abandon"


def test_unknown_status_abandons_fail_closed():
    assert decide("garbage", "1", "0", "1", "5") == "abandon"
