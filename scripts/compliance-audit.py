#!/usr/bin/env python3
"""Автоматический комплаенс-аудит репозитория bks/infra.

Движок для каталога правил `compliance/rules.toml`. Проверки детерминированные
и дешёвые: ни одна не спрашивает LLM и не ходит в сеть — комплаенс, результат
которого зависит от температуры модели, доказательством не является.

Zero-dep by design: только stdlib (tomllib доступен с Python 3.11), потому что
CI-джобы репо стартуют на python:3.11-slim без `pip install`. Тем же свойством
обладает соседний check-docs-consistency.py.

Область проверки — файлы под контролем версий (`git ls-files`), а не рабочее
дерево: аудит обязан давать один и тот же вердикт на хосте и в CI.

Использование:
    python3 scripts/compliance-audit.py
    python3 scripts/compliance-audit.py --json-out out.json --md-out out.md
    python3 scripts/compliance-audit.py --metrics-out bks_compliance.prom

Коды возврата:
    0 — нарушений уровня гейта нет
    1 — есть блокирующее нарушение (severity >= [gate].block_at)
    2 — ошибка самого инструмента (нет правил, битый TOML, не git-репозиторий)
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import pathlib
import re
import subprocess
import sys
import tomllib

SEVERITIES = ("critical", "high", "medium", "low")
# Веса для итогового балла. Провал critical-правила должен быть виден в цифре,
# иначе дашборд покажет «97% соответствия» при утёкшем ключе.
SEVERITY_WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 1}

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WAIVED = "waived"
STATUS_ERROR = "error"


class ToolError(RuntimeError):
    """Ошибка конфигурации или окружения, а не нарушение комплаенса."""


# ── Работа с git ─────────────────────────────────────────────────────────────


def git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def tracked_files(root: pathlib.Path) -> list:
    """Все файлы под контролем версий — единственная область аудита."""
    result = git(root, "ls-files", "-z")
    if result.returncode != 0:
        raise ToolError(f"{root} не git-репозиторий: {result.stderr.strip()}")
    return sorted(item for item in result.stdout.split("\0") if item)


def is_ignored(root: pathlib.Path, path: str) -> bool:
    return git(root, "check-ignore", "-q", "--", path).returncode == 0


def last_commit_date(root: pathlib.Path, path: str):
    """Дата последнего коммита файла; None, если история недоступна."""
    result = git(root, "log", "-1", "--format=%cI", "--", path)
    stamp = result.stdout.strip()
    if result.returncode != 0 or not stamp:
        return None
    return dt.datetime.fromisoformat(stamp).date()


# ── Вспомогательное ──────────────────────────────────────────────────────────


def matches_any(path: str, globs: list) -> bool:
    """Сопоставление пути с шаблонами; `*` покрывает и разделитель каталогов."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def mask(text: str) -> str:
    """Замаскировать найденное совпадение.

    Отчёт с секретом внутри — ещё одна копия секрета, теперь и в артефактах
    CI. Сканер обязан показывать место находки, а не её значение.
    """
    flat = " ".join(text.split())
    if len(flat) <= 8:
        return "***"
    return flat[:3] + "***" + flat[-2:] + " (len=" + str(len(flat)) + ")"


def read_text(path: pathlib.Path):
    """Прочитать файл как UTF-8; бинарь молча пропускаем."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def finding(file: str, line: int, detail: str) -> dict:
    return {"file": file, "line": line, "detail": detail}


# ── Реализация типов проверок ────────────────────────────────────────────────
# Каждая возвращает список находок; пустой список означает «правило выполнено».


def check_forbidden_pattern(ctx, check: dict) -> list:
    pattern = re.compile(check["pattern"])
    include = check.get("include", ["*"])
    exclude = list(ctx.scan_exclude) + list(check.get("exclude", []))
    found = []
    for rel in ctx.files:
        if not matches_any(rel, include) or matches_any(rel, exclude):
            continue
        text = read_text(ctx.root / rel)
        if text is None:
            continue
        for match in pattern.finditer(text):
            found.append(
                finding(rel, line_of(text, match.start()), "совпадение: " + mask(match.group(0)))
            )
    return found


def check_required_pattern(ctx, check: dict) -> list:
    rel = check["path"]
    target = ctx.root / rel
    if not target.is_file():
        return [finding(rel, 0, "файл отсутствует")]
    text = read_text(target)
    if text is None:
        return [finding(rel, 0, "файл не читается как UTF-8")]
    if re.search(check["pattern"], text) is None:
        return [finding(rel, 0, "не найден обязательный маркер: " + check["pattern"])]
    return []


def check_paths_tracked(ctx, check: dict) -> list:
    tracked = set(ctx.files)
    return [
        finding(rel, 0, "путь не под контролем версий")
        for rel in check["paths"]
        if rel not in tracked
    ]


def check_paths_not_tracked(ctx, check: dict) -> list:
    globs = check["globs"]
    allow = check.get("allow", [])
    return [
        finding(rel, 0, "файл не должен быть под git")
        for rel in ctx.files
        if matches_any(rel, globs) and not matches_any(rel, allow)
    ]


def check_paths_ignored(ctx, check: dict) -> list:
    """Каталог обязан быть проигнорирован — и точно не отслеживаться."""
    tracked = set(ctx.files)
    found = []
    for rel in check["paths"]:
        if any(item == rel or item.startswith(rel + "/") for item in tracked):
            found.append(finding(rel, 0, "путь отслеживается git"))
        elif not is_ignored(ctx.root, rel):
            found.append(finding(rel, 0, "путь отсутствует в .gitignore"))
    return found


def check_max_age_days(ctx, check: dict) -> list:
    globs = check["glob"]
    globs = globs if isinstance(globs, list) else [globs]
    limit = int(check["max_age_days"])
    candidates = [rel for rel in ctx.files if matches_any(rel, globs)]
    if not candidates:
        return [finding(str(globs), 0, "нет ни одного файла по шаблону")]
    dated = [(rel, last_commit_date(ctx.root, rel)) for rel in candidates]
    dated = [(rel, date) for rel, date in dated if date is not None]
    if not dated:
        return [finding(str(globs), 0, "нет дат коммитов для файлов")]
    newest_file, newest_date = max(dated, key=lambda item: item[1])
    age = (ctx.now - newest_date).days
    if age > limit:
        return [
            finding(
                newest_file,
                0,
                "свежайший документ старше лимита: " + str(age) + " д. > " + str(limit) + " д.",
            )
        ]
    return []


CHECKS = {
    "forbidden_pattern": check_forbidden_pattern,
    "required_pattern": check_required_pattern,
    "paths_tracked": check_paths_tracked,
    "paths_not_tracked": check_paths_not_tracked,
    "paths_ignored": check_paths_ignored,
    "max_age_days": check_max_age_days,
}


# ── Загрузка каталога правил ─────────────────────────────────────────────────


class Context:
    """Всё, что проверки знают о репозитории; собирается один раз за прогон."""

    def __init__(self, root, files, scan_exclude, now):
        self.root = root
        self.files = files
        self.scan_exclude = scan_exclude
        self.now = now


def load_rules(path: pathlib.Path) -> dict:
    """Прочитать и провалидировать каталог.

    Валидация строгая и падает с кодом 2: опечатка в severity или waiver на
    несуществующее правило обязана быть ошибкой инструмента, а не тихо
    пропущенным требованием.
    """
    if not path.is_file():
        raise ToolError("каталог правил не найден: " + str(path))
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if not config.get("rules"):
        raise ToolError("в " + str(path) + " нет ни одного правила")

    seen = set()
    for rule in config["rules"]:
        rule_id = rule.get("id")
        if not rule_id:
            raise ToolError("правило без обязательного поля id")
        if rule_id in seen:
            raise ToolError("дублирующийся id правила: " + rule_id)
        seen.add(rule_id)
        for field in ("title", "severity", "check"):
            if not rule.get(field):
                raise ToolError(rule_id + ": не заполнено поле " + field)
        if rule["severity"] not in SEVERITY_WEIGHT:
            raise ToolError(rule_id + ": недопустимая severity " + str(rule["severity"]))
        check_type = rule["check"].get("type")
        if check_type not in CHECKS:
            raise ToolError(rule_id + ": неизвестный тип проверки " + str(check_type))

    for waiver in config.get("waivers", []):
        if waiver.get("rule") not in seen:
            raise ToolError("waiver ссылается на несуществующее правило: " + str(waiver.get("rule")))
        for field in ("reason", "owner", "expires"):
            if not waiver.get(field):
                raise ToolError("waiver " + str(waiver.get("rule")) + ": не заполнено поле " + field)

    block_at = config.get("gate", {}).get("block_at", "high")
    if block_at not in SEVERITIES:
        raise ToolError("недопустимый [gate].block_at: " + str(block_at))
    return config


# ── Прогон ───────────────────────────────────────────────────────────────────


def evaluate(config: dict, root: pathlib.Path, now: dt.date) -> dict:
    ctx = Context(
        root=root,
        files=tracked_files(root),
        scan_exclude=config.get("scan", {}).get("exclude", []),
        now=now,
    )
    waivers = {item["rule"]: item for item in config.get("waivers", [])}
    results = []

    for rule in config["rules"]:
        rule_id = rule["id"]
        try:
            found = CHECKS[rule["check"]["type"]](ctx, rule["check"])
            status = STATUS_PASS if not found else STATUS_FAIL
        except Exception as exc:  # одно битое правило не должно ронять аудит
            found = [finding("-", 0, "ошибка проверки: " + str(exc))]
            status = STATUS_ERROR

        waiver_info = None
        waiver = waivers.get(rule_id)
        if waiver:
            expires = dt.date.fromisoformat(str(waiver["expires"]))
            expired = expires < now
            waiver_info = {
                "reason": " ".join(waiver["reason"].split()),
                "owner": waiver["owner"],
                "ticket": waiver.get("ticket", ""),
                "expires": expires.isoformat(),
                "expired": expired,
            }
            if status == STATUS_FAIL and not expired:
                status = STATUS_WAIVED
            elif status == STATUS_FAIL and expired:
                # Просроченное исключение не защищает и само становится находкой.
                found = found + [
                    finding(
                        "compliance/rules.toml",
                        0,
                        "waiver истёк " + expires.isoformat() + " — исключение не действует",
                    )
                ]

        results.append(
            {
                "id": rule_id,
                "title": rule["title"],
                "framework": rule.get("framework", "-"),
                "reference": rule.get("reference", ""),
                "severity": rule["severity"],
                "rationale": " ".join(rule.get("rationale", "").split()),
                "remediation": " ".join(rule.get("remediation", "").split()),
                "check_type": rule["check"]["type"],
                "status": status,
                "findings": found if status != STATUS_PASS else [],
                "waiver": waiver_info,
            }
        )

    return summarize(results, config, ctx, now)


def summarize(results: list, config: dict, ctx, now: dt.date) -> dict:
    """Свести результаты в отчёт: балл, разрезы, состав гейта."""
    block_at = config.get("gate", {}).get("block_at", "high")
    block_index = SEVERITIES.index(block_at)
    blocking = [
        item
        for item in results
        if item["status"] in (STATUS_FAIL, STATUS_ERROR)
        and SEVERITIES.index(item["severity"]) <= block_index
    ]

    total_weight = sum(SEVERITY_WEIGHT[item["severity"]] for item in results)
    passed_weight = sum(
        SEVERITY_WEIGHT[item["severity"]] for item in results if item["status"] == STATUS_PASS
    )
    counts = dict.fromkeys((STATUS_PASS, STATUS_FAIL, STATUS_WAIVED, STATUS_ERROR), 0)
    for item in results:
        counts[item["status"]] += 1

    by_severity = {}
    for severity in SEVERITIES:
        subset = [item for item in results if item["severity"] == severity]
        by_severity[severity] = {
            "total": len(subset),
            "failed": sum(
                1 for item in subset if item["status"] in (STATUS_FAIL, STATUS_ERROR)
            ),
            "waived": sum(1 for item in subset if item["status"] == STATUS_WAIVED),
        }

    by_framework = {}
    for item in results:
        bucket = by_framework.setdefault(
            item["framework"], {"total": 0, "passed": 0, "waived": 0, "failed": 0}
        )
        bucket["total"] += 1
        if item["status"] == STATUS_PASS:
            bucket["passed"] += 1
        elif item["status"] == STATUS_WAIVED:
            bucket["waived"] += 1
        else:
            bucket["failed"] += 1

    return {
        "generated_at": now.isoformat(),
        "repository": ctx.root.name,
        "rules_version": config.get("meta", {}).get("version", "0"),
        "gate": {"block_at": block_at, "blocking_rules": [item["id"] for item in blocking]},
        "summary": {
            "total_rules": len(results),
            "scanned_files": len(ctx.files),
            # Waived не засчитывается как pass: балл показывает реальное
            # покрытие, а принятые риски видны отдельной цифрой.
            "score_percent": round(100 * passed_weight / total_weight, 1) if total_weight else 0.0,
            "by_status": counts,
            "by_severity": by_severity,
            "by_framework": by_framework,
        },
        "results": results,
    }


# ── Рендеринг ────────────────────────────────────────────────────────────────

STATUS_MARK = {
    STATUS_PASS: "✅ PASS",
    STATUS_FAIL: "❌ FAIL",
    STATUS_WAIVED: "🟡 WAIVED",
    STATUS_ERROR: "💥 ERROR",
}


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    status = summary["by_status"]
    lines = [
        "# Комплаенс-отчёт bks/infra",
        "",
        "- Дата прогона: **" + report["generated_at"] + "**",
        "- Каталог правил: **v" + str(report["rules_version"]) + "**",
        "- Правил: **" + str(summary["total_rules"]) + "**, файлов под git: **"
        + str(summary["scanned_files"]) + "**",
        "- Балл соответствия (взвешенный по severity): **"
        + str(summary["score_percent"]) + "%**",
        "- Статусы: PASS " + str(status["pass"]) + " · FAIL " + str(status["fail"])
        + " · WAIVED " + str(status["waived"]) + " · ERROR " + str(status["error"]),
        "- Гейт: блокировка при severity ≥ **" + report["gate"]["block_at"]
        + "**; блокирующих правил: **" + str(len(report["gate"]["blocking_rules"])) + "**",
        "",
        "## Сводка по правилам",
        "",
        "| Правило | Требование | Framework | Severity | Статус |",
        "|---------|------------|-----------|----------|--------|",
    ]
    for item in report["results"]:
        lines.append(
            "| `" + item["id"] + "` | " + item["title"] + " | " + item["framework"]
            + " | " + item["severity"] + " | " + STATUS_MARK[item["status"]] + " |"
        )

    lines += ["", "## Разрез по требованиям регуляторов", "",
              "| Framework | Всего | PASS | WAIVED | FAIL |",
              "|-----------|-------|------|--------|------|"]
    for name, bucket in sorted(report["summary"]["by_framework"].items()):
        lines.append(
            "| " + name + " | " + str(bucket["total"]) + " | " + str(bucket["passed"])
            + " | " + str(bucket["waived"]) + " | " + str(bucket["failed"]) + " |"
        )

    problems = [item for item in report["results"] if item["status"] != STATUS_PASS]
    lines += ["", "## Детали по невыполненным правилам", ""]
    if not problems:
        lines.append("Невыполненных правил нет.")
    for item in problems:
        lines += [
            "### " + STATUS_MARK[item["status"]] + " `" + item["id"] + "` — " + item["title"],
            "",
            "- Основание: " + item["reference"],
            "- Severity: **" + item["severity"] + "** (" + item["framework"] + ")",
        ]
        if item["rationale"]:
            lines.append("- Почему важно: " + item["rationale"])
        if item["remediation"]:
            lines.append("- Что сделать: " + item["remediation"])
        if item["waiver"]:
            waiver = item["waiver"]
            state = "ИСТЁК" if waiver["expired"] else "действует до " + waiver["expires"]
            lines.append(
                "- Исключение (" + state + "), владелец " + waiver["owner"] + ", "
                + waiver["ticket"] + ": " + waiver["reason"]
            )
        lines.append("")
        for found in item["findings"]:
            location = found["file"] + (":" + str(found["line"]) if found["line"] else "")
            lines.append("  - `" + location + "` — " + found["detail"])
        lines.append("")

    lines += [
        "---",
        "",
        "Отчёт сгенерирован `scripts/compliance-audit.py` из `compliance/rules.toml`.",
        "Совпадения секрет-правил маскируются: в отчёте только место находки.",
        "",
    ]
    return "\n".join(lines)


def labels(**pairs) -> str:
    """Собрать блок меток Prometheus: {rule="SEC-001",severity="critical"}."""
    inner = ",".join(key + '="' + str(value) + '"' for key, value in pairs.items())
    return "{" + inner + "}"


def render_metrics(report: dict, now: dt.datetime) -> str:
    """Prometheus textfile-экспозиция для node_exporter.

    Дашборд читает эти метрики из уже развёрнутого Prometheus: заводить под
    комплаенс отдельный источник данных незачем, а история и алертинг
    появляются бесплатно.
    """
    status_code = {STATUS_PASS: 1, STATUS_FAIL: 0, STATUS_WAIVED: 2, STATUS_ERROR: 3}
    lines = [
        "# HELP bks_compliance_score_percent Взвешенный балл соответствия (0-100).",
        "# TYPE bks_compliance_score_percent gauge",
        "bks_compliance_score_percent " + str(report["summary"]["score_percent"]),
        "# HELP bks_compliance_rule_status Статус правила: 1=pass, 0=fail, 2=waived, 3=error.",
        "# TYPE bks_compliance_rule_status gauge",
    ]
    for item in report["results"]:
        lines.append(
            "bks_compliance_rule_status"
            + labels(
                rule=item["id"],
                severity=item["severity"],
                framework=item["framework"],
                check=item["check_type"],
            )
            + " "
            + str(status_code[item["status"]])
        )

    lines += [
        "# HELP bks_compliance_rules_total Число правил в разрезе статуса.",
        "# TYPE bks_compliance_rules_total gauge",
    ]
    for status, count in report["summary"]["by_status"].items():
        lines.append("bks_compliance_rules_total" + labels(status=status) + " " + str(count))

    lines += [
        "# HELP bks_compliance_failed_rules Проваленные правила в разрезе severity.",
        "# TYPE bks_compliance_failed_rules gauge",
    ]
    for severity, bucket in report["summary"]["by_severity"].items():
        lines.append(
            "bks_compliance_failed_rules" + labels(severity=severity) + " " + str(bucket["failed"])
        )

    lines += [
        "# HELP bks_compliance_blocking_rules Число нарушений уровня гейта.",
        "# TYPE bks_compliance_blocking_rules gauge",
        "bks_compliance_blocking_rules " + str(len(report["gate"]["blocking_rules"])),
        "# HELP bks_compliance_waiver_expires_timestamp_seconds Срок действия исключения.",
        "# TYPE bks_compliance_waiver_expires_timestamp_seconds gauge",
    ]
    for item in report["results"]:
        if not item["waiver"]:
            continue
        expires = dt.datetime.fromisoformat(item["waiver"]["expires"]).replace(
            tzinfo=dt.timezone.utc
        )
        lines.append(
            "bks_compliance_waiver_expires_timestamp_seconds"
            + labels(rule=item["id"])
            + " "
            + str(int(expires.timestamp()))
        )

    lines += [
        "# HELP bks_compliance_last_run_timestamp_seconds Время последнего прогона аудита.",
        "# TYPE bks_compliance_last_run_timestamp_seconds gauge",
        "bks_compliance_last_run_timestamp_seconds " + str(int(now.timestamp())),
        "",
    ]
    return "\n".join(lines)


def print_console(report: dict) -> None:
    summary = report["summary"]
    print(
        "Комплаенс-аудит: " + str(summary["by_status"]["pass"]) + "/"
        + str(summary["total_rules"]) + " PASS, балл " + str(summary["score_percent"])
        + "%, файлов проверено " + str(summary["scanned_files"])
    )
    for item in report["results"]:
        if item["status"] == STATUS_PASS:
            continue
        print(
            "  " + STATUS_MARK[item["status"]] + " " + item["id"]
            + " [" + item["severity"] + "] " + item["title"]
        )
        for found in item["findings"][:10]:
            location = found["file"] + (":" + str(found["line"]) if found["line"] else "")
            print("      " + location + " — " + found["detail"])
        if len(item["findings"]) > 10:
            print("      … ещё " + str(len(item["findings"]) - 10) + " находок (см. JSON-отчёт)")
    blocking = report["gate"]["blocking_rules"]
    if blocking:
        print(
            "\nБлокирующие нарушения (severity >= " + report["gate"]["block_at"] + "): "
            + ", ".join(blocking)
        )
    else:
        print("\nБлокирующих нарушений нет.")


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Комплаенс-аудит репозитория bks/infra")
    default_root = pathlib.Path(__file__).resolve().parent.parent
    parser.add_argument("--root", type=pathlib.Path, default=default_root, help="корень репозитория")
    parser.add_argument("--rules", type=pathlib.Path, default=None, help="путь к rules.toml")
    parser.add_argument("--json-out", type=pathlib.Path, default=None, help="куда писать JSON-отчёт")
    parser.add_argument("--md-out", type=pathlib.Path, default=None, help="куда писать Markdown-отчёт")
    parser.add_argument(
        "--metrics-out", type=pathlib.Path, default=None, help="куда писать Prometheus-метрики"
    )
    parser.add_argument(
        "--now", default=None, help="дата прогона YYYY-MM-DD (для воспроизводимых тестов)"
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="всегда возвращать 0: режим наблюдения без блокировки пайплайна",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    rules_path = args.rules or root / "compliance" / "rules.toml"
    now_dt = (
        dt.datetime.fromisoformat(args.now).replace(tzinfo=dt.timezone.utc)
        if args.now
        else dt.datetime.now(dt.timezone.utc)
    )

    try:
        config = load_rules(rules_path)
        report = evaluate(config, root, now_dt.date())
    except ToolError as exc:
        print("ошибка комплаенс-аудита: " + str(exc), file=sys.stderr)
        return 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(report), encoding="utf-8")
    if args.metrics_out:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(render_metrics(report, now_dt), encoding="utf-8")

    print_console(report)
    if args.no_gate:
        return 0
    return 1 if report["gate"]["blocking_rules"] else 0


if __name__ == "__main__":
    sys.exit(main())
