"""Тесты рендера HTML-дашборда (scripts/compliance-dashboard.py).

Вход скрипта — JSON-отчёт `compliance-audit.py` (структурно это тот же вид
входных данных, что rules.toml для движка в test_compliance_audit.py: строгий
формат, за который отвечает соседний инструмент). Проверяем happy-path
рендера и падение на синтаксически некорректном/пустом отчёте — дашборд не
обязан лечить чужой битый JSON молча.
"""

import importlib.util
import json
import pathlib

import pytest

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "compliance-dashboard.py"


def load_engine():
    """Импортировать модуль из файла с дефисом в имени."""
    spec = importlib.util.spec_from_file_location("compliance_dashboard", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_engine()


def make_report():
    return {
        "generated_at": "2026-08-06T00:00:00+00:00",
        "rules_version": "1.0",
        "gate": {"block_at": "high", "blocking_rules": ["SEC-TEST"]},
        "summary": {
            "total_rules": 2,
            "scanned_files": 10,
            "score_percent": 50.0,
            "by_status": {"pass": 1, "waived": 0, "fail": 1, "error": 0},
            "by_severity": {
                "critical": {"total": 1},
                "high": {"total": 1},
                "medium": {"total": 0},
                "low": {"total": 0},
            },
            "by_framework": {"NFR": {"total": 2}},
        },
        "results": [
            {
                "id": "OK-TEST",
                "title": "проверка проходит",
                "framework": "NFR",
                "reference": "NFR-1",
                "severity": "high",
                "status": "pass",
                "findings": [],
                "waiver": None,
            },
            {
                "id": "SEC-TEST",
                "title": "секрет не в git",
                "framework": "NFR",
                "reference": "NFR-2",
                "severity": "critical",
                "status": "fail",
                "findings": [{"file": "deploy.sh", "line": 1, "detail": "секрет найден"}],
                "waiver": None,
            },
        ],
    }


def test_render_html_рисует_обе_карточки_и_находку(tmp_path):
    html = engine.render_html(make_report())

    assert "OK-TEST" in html
    assert "SEC-TEST" in html
    assert "deploy.sh:1" in html
    assert "секрет найден" in html
    assert "50.0%" in html


def test_main_собирает_файл_из_json_отчёта(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(make_report()), encoding="utf-8")
    out_path = tmp_path / "dashboard.html"

    code = engine.main([str(report_path), "--out", str(out_path)])

    assert code == 0
    assert out_path.is_file()
    assert "SEC-TEST" in out_path.read_text(encoding="utf-8")


def test_отсутствующий_отчёт_это_код_2(tmp_path, capsys):
    code = engine.main([str(tmp_path / "нет.json")])

    assert code == 2
    assert "нет JSON-отчёта" in capsys.readouterr().err


def test_пустой_или_битый_json_валится_с_ошибкой(tmp_path):
    """Пустой файл — вырожденный случай синтаксически некорректного JSON."""
    bad = tmp_path / "broken.json"
    bad.write_text("", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        engine.main([str(bad), "--out", str(tmp_path / "out.html")])
