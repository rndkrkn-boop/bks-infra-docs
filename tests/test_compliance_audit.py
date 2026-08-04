"""Тесты движка комплаенс-аудита (scripts/compliance-audit.py).

Проверяем сам инструмент, а не текущее состояние репозитория: правила меняются
каждый спринт, а движок обязан одинаково честно считать pass/fail, гасить
находки действующим waiver-ом и не гасить — просроченным.

Каждый тест собирает синтетический git-репозиторий во временном каталоге:
область аудита определяется `git ls-files`, поэтому без индекса проверять
нечего.
"""

import importlib.util
import pathlib
import subprocess

import pytest

ENGINE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "compliance-audit.py"


def load_engine():
    """Импортировать модуль из файла с дефисом в имени."""
    spec = importlib.util.spec_from_file_location("compliance_audit", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_engine()

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def make_repo(tmp_path, files, commit_date="2026-08-01T12:00:00+00:00"):
    """Собрать git-репозиторий с заданными файлами и датой коммита."""
    import os

    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = dict(os.environ)
    env.update(GIT_ENV)
    env["GIT_AUTHOR_DATE"] = commit_date
    env["GIT_COMMITTER_DATE"] = commit_date
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "fixture"], check=True, env=env
    )
    return tmp_path


def write_rules(tmp_path, body):
    path = tmp_path / "rules.toml"
    path.write_text(body, encoding="utf-8")
    return path


def run(tmp_path, rules_body, now="2026-08-04"):
    import datetime as dt

    config = engine.load_rules(write_rules(tmp_path, rules_body))
    return engine.evaluate(config, tmp_path, dt.date.fromisoformat(now))


def result_for(report, rule_id):
    return next(item for item in report["results"] if item["id"] == rule_id)


# Токеноподобные строки собираны из частей намеренно: каталог тестов НЕ
# исключён из секрет-скана (слепое место любого сканера), поэтому в исходнике
# не должно быть литерала, который сканер примет за настоящий ключ.
FAKE_PREFIX = "glp" + "at-"
FAKE_TAIL = "abcdefghijklmnopqrstuvwx"
FAKE_SECRET = FAKE_PREFIX + FAKE_TAIL
TOKEN_LINE = "TOKEN=" + FAKE_SECRET + "\n"
FAKE_SECRET_QUOTED = "pattern = '" + FAKE_SECRET + "'\n"

SECRET_RULES = """
[gate]
block_at = "high"

[[rules]]
id = "SEC-TEST"
title = "Секрет не в git"
framework = "NFR"
severity = "critical"
[rules.check]
type = "forbidden_pattern"
pattern = 'PREFIX[a-z]{20,}'
include = ["*"]
""".replace("PREFIX", FAKE_PREFIX)


def test_forbidden_pattern_ловит_секрет_и_маскирует_его(tmp_path):
    make_repo(tmp_path, {"deploy.sh": TOKEN_LINE})
    report = run(tmp_path, SECRET_RULES)
    rule = result_for(report, "SEC-TEST")

    assert rule["status"] == "fail"
    assert rule["findings"][0]["file"] == "deploy.sh"
    assert rule["findings"][0]["line"] == 1
    # Главное свойство сканера: значение секрета не утекает в отчёт.
    assert FAKE_TAIL not in str(report)
    assert FAKE_TAIL not in engine.render_markdown(report)
    assert report["gate"]["blocking_rules"] == ["SEC-TEST"]


def test_чистый_репозиторий_проходит(tmp_path):
    make_repo(tmp_path, {"deploy.sh": "TOKEN=openshell:resolve:env:TOKEN\n"})
    report = run(tmp_path, SECRET_RULES)

    assert result_for(report, "SEC-TEST")["status"] == "pass"
    assert report["gate"]["blocking_rules"] == []
    assert report["summary"]["score_percent"] == 100.0


def test_исключения_из_скана_не_дают_сканеру_ловить_сам_себя(tmp_path):
    make_repo(tmp_path, {"rules-copy.toml": FAKE_SECRET_QUOTED})
    body = SECRET_RULES + '\n[scan]\nexclude = ["rules-copy.toml"]\n'
    assert result_for(run(tmp_path, body), "SEC-TEST")["status"] == "pass"


WAIVER_RULES = SECRET_RULES + """
[[waivers]]
rule = "SEC-TEST"
reason = "тестовое исключение"
owner = "bks/infra"
expires = "EXPIRES"
"""


def test_действующий_waiver_снимает_блокировку_но_не_повышает_балл(tmp_path):
    make_repo(tmp_path, {"deploy.sh": TOKEN_LINE})
    report = run(tmp_path, WAIVER_RULES.replace("EXPIRES", "2026-12-31"))
    rule = result_for(report, "SEC-TEST")

    assert rule["status"] == "waived"
    assert rule["waiver"]["expired"] is False
    assert report["gate"]["blocking_rules"] == []
    # Waived не засчитывается как pass: принятый риск остаётся риском.
    assert report["summary"]["score_percent"] == 0.0


def test_просроченный_waiver_снова_блокирует_и_попадает_в_находки(tmp_path):
    make_repo(tmp_path, {"deploy.sh": TOKEN_LINE})
    report = run(tmp_path, WAIVER_RULES.replace("EXPIRES", "2026-07-01"))
    rule = result_for(report, "SEC-TEST")

    assert rule["status"] == "fail"
    assert rule["waiver"]["expired"] is True
    assert any("waiver истёк" in item["detail"] for item in rule["findings"])
    assert report["gate"]["blocking_rules"] == ["SEC-TEST"]


def test_paths_not_tracked_отличает_шаблон_от_настоящего_env(tmp_path):
    make_repo(tmp_path, {"deploy/.env.example": "TOKEN=\n", "deploy/prod.env": "TOKEN=real\n"})
    body = """
[[rules]]
id = "ENV-TEST"
title = "env не в git"
framework = "NFR"
severity = "critical"
[rules.check]
type = "paths_not_tracked"
globs = ["*.env", ".env"]
allow = ["*.env.example"]
"""
    rule = result_for(run(tmp_path, body), "ENV-TEST")

    assert rule["status"] == "fail"
    assert [item["file"] for item in rule["findings"]] == ["deploy/prod.env"]


def test_paths_ignored_ловит_отслеживаемый_каталог(tmp_path):
    make_repo(tmp_path, {"component/deploy.env": "TOKEN=real\n"})
    body = """
[[rules]]
id = "IGN-TEST"
title = "компонент вне монорепо"
framework = "NFR"
severity = "critical"
[rules.check]
type = "paths_ignored"
paths = ["component"]
"""
    rule = result_for(run(tmp_path, body), "IGN-TEST")

    assert rule["status"] == "fail"
    assert rule["findings"][0]["detail"] == "путь отслеживается git"


def test_max_age_days_считает_по_дате_коммита(tmp_path):
    make_repo(tmp_path, {"docs/audit/old.md": "старый отчёт\n"}, commit_date="2026-01-01T10:00:00+00:00")
    body = """
[[rules]]
id = "AGE-TEST"
title = "аудит свежий"
framework = "NFR"
severity = "medium"
[rules.check]
type = "max_age_days"
glob = "docs/audit/*.md"
max_age_days = 90
"""
    rule = result_for(run(tmp_path, body), "AGE-TEST")

    assert rule["status"] == "fail"
    assert "старше лимита" in rule["findings"][0]["detail"]
    # Тот же файл в пределах лимита — проверка проходит.
    assert result_for(run(tmp_path, body, now="2026-02-01"), "AGE-TEST")["status"] == "pass"


def test_required_pattern_сообщает_об_отсутствующем_файле(tmp_path):
    make_repo(tmp_path, {"README.md": "нет нужного маркера\n"})
    body = """
[[rules]]
id = "REQ-TEST"
title = "маркер на месте"
framework = "NFR"
severity = "medium"
[rules.check]
type = "required_pattern"
path = "ARCHITECTURE.md"
pattern = "Namespace read/write ACL"
"""
    rule = result_for(run(tmp_path, body), "REQ-TEST")

    assert rule["status"] == "fail"
    assert rule["findings"][0]["detail"] == "файл отсутствует"


@pytest.mark.parametrize(
    "broken,ожидаемый_фрагмент",
    [
        ('[[rules]]\nid = "A"\ntitle = "t"\nseverity = "urgent"\n[rules.check]\ntype = "paths_tracked"\npaths = []\n', "недопустимая severity"),
        ('[[rules]]\nid = "A"\ntitle = "t"\nseverity = "low"\n[rules.check]\ntype = "ask_llm"\n', "неизвестный тип проверки"),
        ('[[rules]]\nid = "A"\ntitle = "t"\nseverity = "low"\n[rules.check]\ntype = "paths_tracked"\npaths = []\n\n[[waivers]]\nrule = "B"\nreason = "r"\nowner = "o"\nexpires = "2026-12-31"\n', "несуществующее правило"),
        ('[gate]\nblock_at = "urgent"\n\n[[rules]]\nid = "A"\ntitle = "t"\nseverity = "low"\n[rules.check]\ntype = "paths_tracked"\npaths = []\n', "block_at"),
    ],
)
def test_битый_каталог_правил_это_ошибка_инструмента(tmp_path, broken, ожидаемый_фрагмент):
    """Опечатка в каталоге обязана падать, а не тихо пропускать требование."""
    with pytest.raises(engine.ToolError) as exc:
        engine.load_rules(write_rules(tmp_path, broken))
    assert ожидаемый_фрагмент in str(exc.value)


def test_метрики_экспортируются_в_формате_prometheus(tmp_path):
    import datetime as dt

    make_repo(tmp_path, {"deploy.sh": TOKEN_LINE})
    report = run(tmp_path, WAIVER_RULES.replace("EXPIRES", "2026-12-31"))
    text = engine.render_metrics(report, dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc))

    assert 'bks_compliance_rule_status' in text
    assert 'rule="SEC-TEST"' in text
    assert "bks_compliance_score_percent 0.0" in text
    assert "bks_compliance_blocking_rules 0" in text
    # Срок waiver-а экспортируется, чтобы алерт срабатывал до его истечения.
    assert "bks_compliance_waiver_expires_timestamp_seconds" in text
    # Значение секрета не должно попасть даже в метрики.
    assert FAKE_TAIL not in text


def test_код_возврата_и_режим_наблюдения(tmp_path, capsys):
    make_repo(tmp_path, {"deploy.sh": TOKEN_LINE})
    rules = str(write_rules(tmp_path, SECRET_RULES))
    args = ["--root", str(tmp_path), "--rules", rules, "--now", "2026-08-04"]

    assert engine.main(args) == 1
    assert engine.main(args + ["--no-gate"]) == 0
    assert "SEC-TEST" in capsys.readouterr().out


def test_отсутствующий_каталог_правил_это_код_2(tmp_path, capsys):
    make_repo(tmp_path, {"README.md": "пусто\n"})
    code = engine.main(["--root", str(tmp_path), "--rules", str(tmp_path / "нет.toml")])

    assert code == 2
    assert "каталог правил не найден" in capsys.readouterr().err


def test_боевой_каталог_правил_валиден_и_прогоняется(tmp_path):
    """Смоук по реальному репозиторию: правила и движок не разъехались."""
    import datetime as dt

    repo = ENGINE_PATH.parent.parent
    config = engine.load_rules(repo / "compliance" / "rules.toml")
    report = engine.evaluate(config, repo, dt.date.fromisoformat("2026-08-04"))

    assert report["summary"]["total_rules"] == len(config["rules"])
    assert report["summary"]["scanned_files"] > 0
    # Каждое правило обязано отработать: ERROR означает битую проверку.
    assert [item["id"] for item in report["results"] if item["status"] == "error"] == []
    # Гейт на реальном репозитории должен быть чист — иначе комплаенс нарушен.
    assert report["gate"]["blocking_rules"] == []
