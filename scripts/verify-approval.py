#!/usr/bin/env python3
"""verify-approval.py — гейт перед стартом автономной реализации (AUDIT task 8).

Файл одобрения (APPROVAL_DIR/<дата>-approval.json) — единственный вход,
которым человек включает автономную запись в репозиторий. Раньше этот вход
был неаутентифицированным файлом-триггером: достаточно было, чтобы файл
существовал по нужному пути. Этот скрипт закрывает три независимых дыры:

  1. Файл должен пройти schemas/approval.schema.json (структура, типы, паттерны id).
  2. Файл должен нести действительную HMAC-SHA256-подпись под ключом
     APPROVAL_HMAC_KEY (GitLab CI Variables) — без ключа никто не может
     сгенерировать валидный approval, даже имея запись в APPROVAL_DIR.
  3. Каждый approved_issue_id должен присутствовать в отчёте аудита ТОГО ЖЕ
     дня, и сам отчёт обязан быть валиден по schemas/audit-report.schema.json
     (AUDIT-006) — иначе можно было бы одобрить issue, которого аудит не
     находил.

Любая проверка не пройдена → exit 1, реализация не стартует (fail closed).

Использование:
    python3 scripts/verify-approval.py verify <approval.json> <audit-report.json>
    python3 scripts/verify-approval.py sign <approval.json>   # дополняет/пересчитывает signature

Подпись при `sign` считается ключом из той же переменной APPROVAL_HMAC_KEY —
это удобство для человека, который одобряет issue на своей машине с тем же
ключом, синхронизированным из GitLab CI Variables (см. .env.example).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - окружение без зависимости
    print("verify-approval: модуль jsonschema не установлен (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)

SCHEMA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "schemas"


def fail(msg: str) -> None:
    print(f"❌ verify-approval: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path: Path, label: str) -> dict:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        fail(f"{label} не найден: {path}")
        raise  # unreachable, для mypy/читаемости
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{label} не валидный JSON ({path}): {exc}")
        raise


def canonical_payload(approval: dict) -> bytes:
    """Каноническая сериализация approval-объекта без поля signature.

    sort_keys + компактные разделители — детерминированная сериализация,
    одинаковая у подписывающего и у проверяющего независимо от того, в каком
    порядке поля были в исходном файле.
    """
    payload_obj = {k: v for k, v in approval.items() if k != "signature"}
    return json.dumps(payload_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_signature(approval: dict, key: str) -> str:
    payload = canonical_payload(approval)
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def require_hmac_key() -> str:
    key = os.environ.get("APPROVAL_HMAC_KEY", "")
    if not key:
        fail("APPROVAL_HMAC_KEY не задан (ключ из GitLab CI Variables) — fail closed")
    return key


def cmd_verify(args: argparse.Namespace) -> None:
    approval = load_json(args.approval_file, "файл одобрения")
    audit_report = load_json(args.audit_report_file, "отчёт аудита")

    approval_schema = json.loads((args.schema_dir / "approval.schema.json").read_text())
    audit_schema = json.loads((args.schema_dir / "audit-report.schema.json").read_text())

    try:
        jsonschema.validate(approval, approval_schema)
    except jsonschema.ValidationError as exc:
        fail(f"файл одобрения не проходит schemas/approval.schema.json: {exc.message}")

    try:
        jsonschema.validate(audit_report, audit_schema)
    except jsonschema.ValidationError as exc:
        fail(f"отчёт аудита не проходит schemas/audit-report.schema.json: {exc.message}")

    key = require_hmac_key()
    signature = approval.get("signature", "")
    expected = compute_signature(approval, key)
    if not hmac.compare_digest(signature, expected):
        fail("HMAC-подпись не совпадает — файл одобрения отклонён")

    approval_date = approval.get("approval_date")
    audit_date = audit_report.get("audit_date")
    if approval_date and audit_date and approval_date != audit_date:
        fail(f"approval_date ({approval_date}) не совпадает с audit_date отчёта ({audit_date})")

    report_ids = {issue.get("id") for issue in audit_report.get("issues", [])}
    approved_ids = approval.get("approved_issue_ids", [])
    missing = [i for i in approved_ids if i not in report_ids]
    if missing:
        fail("approved_issue_ids отсутствуют в отчёте аудита того же дня: " + ", ".join(missing))

    print(
        "✅ verify-approval: файл одобрения валиден "
        f"(схема + HMAC + {len(approved_ids)} issue сверены с отчётом аудита)"
    )


def cmd_sign(args: argparse.Namespace) -> None:
    approval = load_json(args.approval_file, "файл одобрения")
    key = require_hmac_key()
    approval["signature"] = compute_signature(approval, key)
    args.approval_file.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
    print(f"✅ verify-approval: подпись записана в {args.approval_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema-dir", type=Path, default=SCHEMA_DIR_DEFAULT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="проверить approval-файл перед стартом реализации")
    p_verify.add_argument("approval_file", type=Path)
    p_verify.add_argument("audit_report_file", type=Path)
    p_verify.set_defaults(func=cmd_verify)

    p_sign = sub.add_parser("sign", help="пересчитать и записать signature (для одобряющего человека)")
    p_sign.add_argument("approval_file", type=Path)
    p_sign.set_defaults(func=cmd_sign)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
