"""Тесты гейта одобрения (scripts/verify-approval.py, AUDIT-005/AUDIT task 8).

Это единственная проверка, которая решает, может ли автономный цикл вообще
начать реализацию — до 2026-08-06 daily-implement-now.sh её не вызывал
вовсе, и approval-файл читался без аутентификации. Тестируем сам гейт через
его настоящий CLI-контракт (subprocess), а не импортом функций: то, что
реально вызывают daily-*.sh скрипты — это `python3 verify-approval.py verify
<approval> <report>` с кодом возврата, а не библиотечный API.
"""

import hashlib
import hmac
import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify-approval.py"
SCHEMA_DIR = REPO_ROOT / "schemas"

HMAC_KEY = "test-key-not-a-real-secret-0123456789abcdef"

VALID_ISSUE = {
    "id": "AUDIT-901",
    "priority": "LOW",
    "title": "test issue",
    "effort": "QUICK",
    "implementation": "noop",
}


def audit_report(tmp_path, audit_date="2026-08-06", issues=(VALID_ISSUE,)):
    path = tmp_path / "audit-report.json"
    path.write_text(json.dumps({"audit_date": audit_date, "issues": list(issues)}))
    return path


def sign(payload: dict, key: str = HMAC_KEY) -> str:
    body = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def approval_file(tmp_path, name="approval.json", *, signed=True, key=HMAC_KEY, **overrides):
    payload = {
        "approval_date": "2026-08-06",
        "approved_issue_ids": ["AUDIT-901"],
        "approved_issues": [VALID_ISSUE],
    }
    payload.update(overrides)
    if signed:
        payload["signature"] = sign(payload, key)
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def run_verify(approval_path, report_path, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--schema-dir", str(SCHEMA_DIR),
         "verify", str(approval_path), str(report_path)],
        capture_output=True, text=True, env=env,
    )


def test_valid_signed_approval_passes(tmp_path):
    approval = approval_file(tmp_path)
    report = audit_report(tmp_path)
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 0, result.stderr
    assert "валиден" in result.stdout


def test_missing_signature_field_fails_schema(tmp_path):
    approval = approval_file(tmp_path, signed=False)
    report = audit_report(tmp_path)
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 1
    assert "signature" in result.stderr


def test_tampered_payload_fails_hmac(tmp_path):
    # Подписываем валидный payload, затем меняем поле ПОСЛЕ подписи —
    # ровно то, что подделка approval-файла и означает на практике.
    approval = approval_file(tmp_path)
    data = json.loads(approval.read_text())
    data["approved_issues"][0]["implementation"] = "rm -rf /"
    approval.write_text(json.dumps(data))
    report = audit_report(tmp_path)
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 1
    assert "HMAC" in result.stderr


def test_missing_key_fails_closed(tmp_path):
    approval = approval_file(tmp_path)
    report = audit_report(tmp_path)
    result = run_verify(approval, report, env_extra=None)  # без APPROVAL_HMAC_KEY
    assert result.returncode == 1
    assert "APPROVAL_HMAC_KEY" in result.stderr


def test_wrong_key_fails_hmac(tmp_path):
    approval = approval_file(tmp_path, key=HMAC_KEY)
    report = audit_report(tmp_path)
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": "different-key"})
    assert result.returncode == 1
    assert "HMAC" in result.stderr


def test_issue_not_in_audit_report_fails(tmp_path):
    # Одобрен AUDIT-901, но отчёт аудита того дня про него не знает —
    # ровно дыра, которую сверка с отчётом должна ловить.
    approval = approval_file(tmp_path)
    report = audit_report(tmp_path, issues=[{**VALID_ISSUE, "id": "AUDIT-999"}])
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 1
    assert "AUDIT-901" in result.stderr


def test_approval_date_mismatch_fails(tmp_path):
    approval = approval_file(tmp_path, approval_date="2026-08-05")
    report = audit_report(tmp_path, audit_date="2026-08-06")
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 1
    assert "audit_date" in result.stderr


def test_invalid_issue_id_pattern_fails_schema(tmp_path):
    approval = approval_file(tmp_path, approved_issue_ids=["not-a-valid-id"])
    report = audit_report(tmp_path)
    result = run_verify(approval, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert result.returncode == 1


def test_sign_subcommand_round_trips(tmp_path):
    path = tmp_path / "to_sign.json"
    path.write_text(json.dumps({
        "approval_date": "2026-08-06",
        "approved_issue_ids": ["AUDIT-901"],
        "approved_issues": [VALID_ISSUE],
    }))
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "sign", str(path)],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "APPROVAL_HMAC_KEY": HMAC_KEY},
    )
    assert result.returncode == 0, result.stderr
    signed = json.loads(path.read_text())
    assert signed["signature"] == sign({k: v for k, v in signed.items() if k != "signature"})

    report = audit_report(tmp_path)
    verify_result = run_verify(path, report, {"APPROVAL_HMAC_KEY": HMAC_KEY})
    assert verify_result.returncode == 0, verify_result.stderr
