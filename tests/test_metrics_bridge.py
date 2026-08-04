"""Тесты моста метрик (scripts/metrics-bridge.py).

Проверяем инструмент, а не текущее состояние хоста: источники приходят и уходят,
а мост обязан одинаково честно отдавать экспозицию, изолировать сбойный источник
и НЕ выдавать отсутствие данных за нуль.

Отдельный класс тестов — контракт имён метрик. Правила записи в bks/monitoring
ссылаются на конкретные имена; переименование поля здесь обнулило бы панели и
алерты молча, без единой ошибки в логах. Такой рассинхрон уже был найден при
разработке (правило ждало bks_watchdog_run_timestamp_seconds, мост отдавал
bks_watchdog_last_run_timestamp_seconds), поэтому он зафиксирован тестом.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_PATH = ROOT / "scripts" / "metrics-bridge.py"
Q = chr(34)


def load_engine():
    """Импортировать модуль из файла с дефисом в имени."""
    spec = importlib.util.spec_from_file_location("metrics_bridge", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_engine()


def labelled(name, labels, value):
    """Строка экспозиции с лейблами — собирается, а не пишется литералом."""
    return "%s{%s} %s" % (name, labels, value)


def write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def toml_source(path, out_dir, extra=""):
    """Минимальный каталог с одним jsonl-источником."""
    return (
        "[defaults]\n"
        + 'out_dir = "%s"\n\n' % out_dir
        + "[[source]]\n"
        + 'name = "wd"\n'
        + 'kind = "jsonl_last_by_key"\n'
        + 'path = "%s"\n' % path
        + 'out = "bks_wd.prom"\n'
        + 'metric_prefix = "bks_watchdog"\n'
        + extra
    )


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(ENGINE_PATH)] + args,
        capture_output=True, text=True, cwd=str(cwd or ROOT),
    )


# ── Формат экспозиции ────────────────────────────────────────────────────────


def test_label_value_escaped():
    """Кавычка, слеш и перевод строки в значении лейбла ломают весь файл."""
    raw = "a" + Q + "b" + chr(92) + "c" + chr(10) + "d"
    escaped = engine.escape_label_value(raw)
    assert Q not in escaped.replace(chr(92) + Q, "")
    assert chr(10) not in escaped
    assert escaped == "a" + chr(92) + Q + "b" + chr(92) * 2 + "c" + chr(92) + "nd"


def test_metric_and_label_names_sanitized():
    assert engine.sanitize_name("check-name.v2") == "check_name_v2"
    assert engine.sanitize_name("9lives").startswith("_")


def test_values_rendered_without_float_noise():
    assert engine.render_value(3) == "3"
    assert engine.render_value(3.0) == "3"
    assert engine.render_value(True) == "1"
    assert engine.render_value(False) == "0"
    assert engine.render_value(0.5) == "0.5"


def test_samples_sorted_deterministically():
    """Порядок серий не должен зависеть от порядка обхода словарей."""
    first = engine.Family("m", "help")
    first.add([("check", "zebra")], 1)
    first.add([("check", "alpha")], 0)
    second = engine.Family("m", "help")
    second.add([("check", "alpha")], 0)
    second.add([("check", "zebra")], 1)
    assert first.render() == second.render()
    assert first.render()[2] == labelled("m", "check=" + Q + "alpha" + Q, "0")


def test_family_without_labels_has_no_braces():
    family = engine.Family("m", "help")
    family.add([], 7)
    assert family.render()[-1] == "m 7"


def test_exposition_ends_with_newline():
    """Без завершающего перевода строки последняя метрика считается обрезанной."""
    family = engine.Family("m", "help")
    family.add([], 1)
    assert engine.render_exposition([family]).endswith(chr(10))


def test_empty_families_are_skipped():
    """Семейство без сэмплов не должно давать HELP/TYPE без значений."""
    assert engine.render_exposition([engine.Family("m", "help")]).strip() == ""


def test_write_atomic_leaves_no_temp_and_sets_mode(tmp_path):
    target = tmp_path / "x.prom"
    engine.write_atomic(target, "m 1" + chr(10))
    assert target.read_text() == "m 1" + chr(10)
    assert oct(target.stat().st_mode)[-3:] == "644"
    assert [f.name for f in tmp_path.iterdir()] == ["x.prom"]


def test_write_atomic_replaces_previous_content(tmp_path):
    target = tmp_path / "x.prom"
    engine.write_atomic(target, "old 1" + chr(10))
    engine.write_atomic(target, "new 2" + chr(10))
    assert target.read_text() == "new 2" + chr(10)


# ── Разбор времени ───────────────────────────────────────────────────────────


def test_timestamp_formats():
    assert engine.parse_timestamp(1785858371351, "ms") == 1785858371.351
    assert engine.parse_timestamp(1785858371, "s") == 1785858371
    assert engine.parse_timestamp("2026-08-05T00:10:00Z") == 1785888600.0
    assert engine.parse_timestamp(None) is None
    assert engine.parse_timestamp("не время") is None
    assert engine.parse_timestamp("") is None


# ── Адаптер JSONL ────────────────────────────────────────────────────────────


def test_jsonl_last_record_per_key_wins(tmp_path):
    """Watchdog дописывает историю; Prometheus интересует текущее состояние."""
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [
        dict(ts_ms=1000, check="disk_space", status="fail"),
        dict(ts_ms=2000, check="disk_space", status="ok"),
        dict(ts_ms=2000, check="router", status="fail"),
    ])
    families, meta = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog"), now=3.0
    )
    text = engine.render_exposition(families)
    assert labelled("bks_watchdog_check_status", "check=" + Q + "disk_space" + Q, "1") in text
    assert labelled("bks_watchdog_check_status", "check=" + Q + "router" + Q, "0") in text
    assert meta["series"] == 2


def test_jsonl_run_timestamp_is_newest_record(tmp_path):
    """Время прогона — из ДАННЫХ, а не mtime файла: одна живая проверка не должна
    маскировать девять умерших."""
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [
        dict(ts_ms=1000, check="a", status="ok"),
        dict(ts_ms=5000, check="b", status="ok"),
    ])
    families, meta = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog"), now=9.0
    )
    assert meta["newest_ts"] == 5.0
    assert "bks_watchdog_last_run_timestamp_seconds 5" in engine.render_exposition(families)


def test_jsonl_tail_drops_truncated_first_line(tmp_path):
    """Чтение хвоста обрезает первую строку посередине — она не должна попасть
    в счётчик нечитаемых строк как ложная ошибка разбора."""
    src = tmp_path / "metrics.jsonl"
    filler = [dict(ts_ms=1000 + i, check="c%d" % i, status="ok") for i in range(200)]
    write_jsonl(src, filler)
    families, meta = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog", tail_bytes=512),
        now=9.0,
    )
    assert meta["malformed"] == 0
    # Прочитан именно хвост, а не весь файл.
    assert 0 < meta["series"] < 200


def test_jsonl_malformed_lines_counted_not_fatal(tmp_path):
    src = tmp_path / "metrics.jsonl"
    src.write_text(
        json.dumps(dict(ts_ms=1000, check="a", status="ok")) + chr(10)
        + "{битый json" + chr(10)
        + "[1,2,3]" + chr(10),
        encoding="utf-8",
    )
    families, meta = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog"), now=9.0
    )
    assert meta["series"] == 1
    assert meta["malformed"] == 2
    assert "bks_watchdog_malformed_lines 2" in engine.render_exposition(families)


def test_jsonl_record_without_timestamp_still_reports_status(tmp_path):
    """Нечитаемая метка времени не должна лишать Prometheus статуса проверки."""
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(check="a", status="ok")])
    families, meta = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog"), now=9.0
    )
    text = engine.render_exposition(families)
    assert labelled("bks_watchdog_check_status", "check=" + Q + "a" + Q, "1") in text
    assert "bks_watchdog_last_run_timestamp_seconds" not in text
    assert meta["newest_ts"] is None


def test_jsonl_custom_ok_values(tmp_path):
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="a", status="PASS")])
    families, _ = engine.adapter_jsonl_last_by_key(
        dict(name="wd", path=str(src), metric_prefix="bks_watchdog",
             status_ok_values=["pass"]),
        now=9.0,
    )
    assert labelled("bks_watchdog_check_status", "check=" + Q + "a" + Q, "1") in engine.render_exposition(families)


# ── Адаптер JSON-сводок ──────────────────────────────────────────────────────


def json_source(path, fields):
    return dict(name="backup", path=str(path), metric_prefix="bks_backup",
                field=fields)


def test_json_gauges_null_is_skipped_not_zero(tmp_path):
    """null означает «drill никогда не проводился», а не «проводился только что».
    Подмена нулём — ложь дашборду."""
    src = tmp_path / "s.json"
    src.write_text(json.dumps(dict(last_drill_age_days=None, snapshots=3)), encoding="utf-8")
    families, meta = engine.adapter_json_gauges(
        json_source(src, [
            dict(json="last_drill_age_days", metric="last_drill_age_seconds", scale=86400),
            dict(json="snapshots", metric="snapshots"),
        ]),
        now=1.0,
    )
    text = engine.render_exposition(families)
    assert "bks_backup_last_drill_age_seconds" not in text
    assert "bks_backup_snapshots 3" in text
    assert meta["series"] == 1


def test_json_gauges_scale_to_base_units(tmp_path):
    src = tmp_path / "s.json"
    src.write_text(json.dumps(dict(age_days=2.5)), encoding="utf-8")
    families, _ = engine.adapter_json_gauges(
        json_source(src, [dict(json="age_days", metric="age_seconds", scale=86400)]),
        now=1.0,
    )
    assert "bks_backup_age_seconds 216000" in engine.render_exposition(families)


def test_json_gauges_bool_and_string(tmp_path):
    src = tmp_path / "s.json"
    src.write_text(json.dumps(dict(ok=True, broken=False, note="текст")), encoding="utf-8")
    families, _ = engine.adapter_json_gauges(
        json_source(src, [
            dict(json="ok", metric="recoverable"),
            dict(json="broken", metric="broken"),
            dict(json="note", metric="note"),
        ]),
        now=1.0,
    )
    text = engine.render_exposition(families)
    assert "bks_backup_recoverable 1" in text
    assert "bks_backup_broken 0" in text
    # Строку в число не приводим: иначе на дашборде появился бы 0 вместо текста ошибки.
    assert "bks_backup_note" not in text


def test_json_gauges_rejects_non_object(tmp_path):
    src = tmp_path / "s.json"
    src.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        engine.adapter_json_gauges(json_source(src, []), now=1.0)


def test_json_gauges_field_spec_must_be_complete(tmp_path):
    src = tmp_path / "s.json"
    src.write_text(json.dumps(dict(a=1)), encoding="utf-8")
    with pytest.raises(engine.ToolError):
        engine.adapter_json_gauges(json_source(src, [dict(json="a")]), now=1.0)


# ── Свежесть, изоляция и коды возврата ───────────────────────────────────────


def test_stale_source_is_marked_and_reported(tmp_path):
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="a", status="ok")])
    families, result = engine.process_source(
        dict(name="wd", kind="jsonl_last_by_key", path=str(src),
             metric_prefix="bks_watchdog", max_age_seconds=60),
        now=1000.0,
    )
    assert result["ok"] is True
    assert result["stale"] is True
    assert "старше" in result["error"]


def test_fresh_source_not_marked_stale(tmp_path):
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000000, check="a", status="ok")])
    _, result = engine.process_source(
        dict(name="wd", kind="jsonl_last_by_key", path=str(src),
             metric_prefix="bks_watchdog", max_age_seconds=600),
        now=1000.0,
    )
    assert result["stale"] is False
    assert result["error"] is None


def test_missing_file_is_not_an_exception(tmp_path):
    """Отсутствие файла — нормально до первого прогона задачи, но не «всё хорошо»."""
    families, result = engine.process_source(
        dict(name="backup", kind="json_gauges", path=str(tmp_path / "нет.json")),
        now=1.0,
    )
    assert families == []
    assert result["ok"] is False
    assert "файла нет" in result["error"]


def test_broken_source_does_not_kill_the_others(tmp_path):
    """Регрессия: битый JSON одного источника ронял весь прогон, включая запись
    собственных метрик моста — то есть тихо гасил ВСЁ."""
    good = tmp_path / "metrics.jsonl"
    write_jsonl(good, [dict(ts_ms=1000, check="a", status="ok")])
    bad = tmp_path / "s.json"
    bad.write_text("это не json", encoding="utf-8")
    out = tmp_path / "out"
    config = dict(source=[
        dict(name="wd", kind="jsonl_last_by_key", path=str(good),
             out="bks_wd.prom", metric_prefix="bks_watchdog"),
        dict(name="backup", kind="json_gauges", path=str(bad),
             out="bks_backup.prom", metric_prefix="bks_backup"),
    ])
    failed, results, _ = engine.run(config, out, [], False, now=2000.0)
    assert failed == 1
    assert (out / "bks_wd.prom").is_file()
    assert not (out / "bks_backup.prom").exists()
    # Главное: собственные метрики моста записаны, проблема источника видна.
    self_text = (out / engine.SELF_OUT).read_text()
    assert labelled("bks_metrics_bridge_source_ok", "source=" + Q + "backup" + Q, "0") in self_text
    assert labelled("bks_metrics_bridge_source_ok", "source=" + Q + "wd" + Q, "1") in self_text


def test_self_metrics_duration_never_negative(tmp_path):
    """--now подставляет логическое время; длительность обязана считаться по
    настенным часам, иначе она уходит в минус (найдено на живом прогоне)."""
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="a", status="ok")])
    out = tmp_path / "out"
    config = dict(source=[dict(name="wd", kind="jsonl_last_by_key", path=str(src),
                               out="bks_wd.prom", metric_prefix="bks_watchdog")])
    engine.run(config, out, [], False, now=1.0)
    for line in (out / engine.SELF_OUT).read_text().splitlines():
        if line.startswith("bks_metrics_bridge_duration_seconds "):
            assert float(line.split()[1]) >= 0


def test_unknown_source_selection_is_tool_error(tmp_path):
    config = dict(source=[dict(name="wd", kind="jsonl_last_by_key", path="/нет",
                               out="x.prom")])
    with pytest.raises(engine.ToolError):
        engine.run(config, tmp_path, ["опечатка"], False, now=1.0)


def test_unknown_kind_is_tool_error(tmp_path):
    with pytest.raises(engine.ToolError):
        engine.process_source(
            dict(name="x", kind="телепатия", path=str(tmp_path)), now=1.0
        )


def test_empty_catalog_is_tool_error(tmp_path):
    with pytest.raises(engine.ToolError):
        engine.run(dict(), tmp_path, [], False, now=1.0)


def test_cli_missing_config_returns_2(tmp_path):
    result = run_cli(["--config", str(tmp_path / "нет.toml")])
    assert result.returncode == 2
    assert "нет файла источников" in result.stderr


def test_cli_broken_toml_returns_2(tmp_path):
    cfg = tmp_path / "sources.toml"
    cfg.write_text("[[source]" + chr(10), encoding="utf-8")
    result = run_cli(["--config", str(cfg)])
    assert result.returncode == 2
    assert "битый TOML" in result.stderr


def test_cli_strict_fails_on_stale_default_does_not(tmp_path):
    """Протухший источник виден в метриках всегда, но роняет процесс только с
    --strict: красный systemd-unit был бы вторым каналом тревоги об одном и том
    же (в этом стеке правило: один инцидент — один канал)."""
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="a", status="ok")])
    out = tmp_path / "out"
    cfg = tmp_path / "sources.toml"
    cfg.write_text(
        toml_source(src, out, extra="max_age_seconds = 60" + chr(10)),
        encoding="utf-8",
    )
    assert run_cli(["--config", str(cfg)]).returncode == 0
    assert run_cli(["--config", str(cfg), "--strict"]).returncode == 1


def test_cli_stdout_writes_nothing_to_disk(tmp_path):
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="a", status="ok")])
    out = tmp_path / "out"
    cfg = tmp_path / "sources.toml"
    cfg.write_text(toml_source(src, out), encoding="utf-8")
    result = run_cli(["--config", str(cfg), "--stdout"])
    assert result.returncode == 0
    assert "bks_watchdog_check_status" in result.stdout
    assert not out.exists()


# ── Контракт имён с правилами записи ─────────────────────────────────────────
# Правила bks/monitoring (prometheus/rules/bks-aggregation.yml) ссылаются на
# имена ниже. Переименование поля в адаптере или в metrics/sources.toml обнулит
# соответствующие панели и алерты БЕЗ единой ошибки в логах — поэтому имена
# зафиксированы тестом, а не только комментарием.


def test_metric_names_expected_by_recording_rules(tmp_path):
    src = tmp_path / "metrics.jsonl"
    write_jsonl(src, [dict(ts_ms=1000, check="disk_space", status="ok")])
    out = tmp_path / "out"
    catalog = ROOT / "metrics" / "sources.toml"
    assert catalog.is_file(), "каталог источников обязателен"
    config = dict(source=[dict(name="watchdog", kind="jsonl_last_by_key",
                               path=str(src), out="bks_watchdog.prom",
                               metric_prefix="bks_watchdog")])
    engine.run(config, out, [], False, now=2000.0)
    watchdog_text = (out / "bks_watchdog.prom").read_text()
    self_text = (out / engine.SELF_OUT).read_text()
    for name in ("bks_watchdog_check_status", "bks_watchdog_last_run_timestamp_seconds"):
        assert name in watchdog_text, name
    for name in ("bks_metrics_bridge_last_run_timestamp_seconds",
                 "bks_metrics_bridge_source_ok",
                 "bks_metrics_bridge_source_stale"):
        assert name in self_text, name


def test_catalog_metric_prefixes_match_rules():
    """Префиксы в каталоге источников — часть контракта с правилами записи."""
    import tomllib

    catalog = tomllib.loads((ROOT / "metrics" / "sources.toml").read_text(encoding="utf-8"))
    prefixes = set(s.get("metric_prefix") for s in catalog["source"])
    assert "bks_watchdog" in prefixes
    assert "bks_backup" in prefixes
    backup = next(s for s in catalog["source"] if s["metric_prefix"] == "bks_backup")
    metrics = set(f["metric"] for f in backup["field"])
    # На эти два имени опираются bks:backup_recoverable и
    # bks:backup_snapshots_complete в слое агрегации.
    assert "recoverable" in metrics
    assert "snapshots_complete" in metrics
