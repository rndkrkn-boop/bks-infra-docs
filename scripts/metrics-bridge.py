#!/usr/bin/env python3
"""metrics-bridge.py — мост между артефактами задач-по-таймеру и Prometheus.

Проблема, которую он решает. Часть системы не скрейпится в принципе: аудит
комплаенса, бэкап и watchdog — это задачи, которые запускаются по таймеру,
пишут результат и умирают. В момент скрейпа процесса уже нет, /metrics
предъявить некому. Штатный путь Prometheus для таких данных — textfile
collector node_exporter: задача пишет .prom-файл, экспортёр отдаёт его как
обычную экспозицию.

Этот скрипт превращает уже существующие артефакты (JSONL watchdog, JSON-сводки)
в такие файлы, не требуя от авторов задач знать про Prometheus.

Почему конфигом, а не кодом на источник: добавление источника — это правка
metrics/sources.toml, а не новая функция. Тот же принцип, что у
compliance/rules.toml и backup/retention.toml: каталог данных отдельно, движок
отдельно.

Zero-dep by design: только stdlib (tomllib с Python 3.11). Скрипт запускается
и на хосте из systemd-таймера, и в CI на python:3.11-slim без pip install —
как compliance-audit.py и check-docs-consistency.py рядом.

Использование:
    python3 scripts/metrics-bridge.py                      # запись в out_dir
    python3 scripts/metrics-bridge.py --stdout             # в stdout, не пишет
    python3 scripts/metrics-bridge.py --source watchdog    # только один источник
    python3 scripts/metrics-bridge.py --strict             # rc=1 при сбое источника

Коды возврата:
    0 — файлы записаны (проблемы источников видны в bks_metrics_bridge_*)
    1 — только с --strict: источник недоступен или данные протухли
    2 — ошибка самого инструмента (нет конфига, битый TOML, каталог не пишется)

Про --strict и таймер. По умолчанию протухший источник НЕ роняет процесс:
цель конвейера — сделать проблему видимой в Prometheus, а красный systemd-unit
завёл бы второй канал тревоги о том же самом (в этом стеке сознательное
правило: один инцидент — один канал). --strict нужен CI, где чинить есть кому.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "metrics" / "sources.toml"
DEFAULT_OUT_DIR = "/var/lib/node_exporter/textfile"
SELF_PREFIX = "bks_metrics_bridge"
SELF_OUT = "bks_metrics_bridge.prom"

# Читаем хвост, а не файл: metrics.jsonl watchdog — 4+ МБ и растёт, а нужна
# только последняя запись каждой проверки. При цикле раз в 5 минут разбор
# нескольких мегабайт на каждый прогон — плата ни за что.
DEFAULT_TAIL_BYTES = 262144

LABEL_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")


class ToolError(RuntimeError):
    """Ошибка конфигурации или окружения, а не проблема данных."""


# ── Экспозиция Prometheus ────────────────────────────────────────────────────


def sanitize_name(name: str) -> str:
    """Имя метрики/лейбла по спецификации: [a-zA-Z_][a-zA-Z0-9_]*."""
    clean = LABEL_NAME_RE.sub("_", name)
    if clean and clean[0].isdigit():
        clean = "_" + clean
    return clean


def escape_label_value(value: str) -> str:
    r"""Экранирование по формату экспозиции: обратный слеш, кавычка, перевод строки.

    Не косметика: значения приходят из JSON внешних задач. Неэкранированная
    кавычка делает всю строку неразбираемой, node_exporter поднимает
    node_textfile_scrape_error=1 и ПЕРЕСТАЁТ отдавать файл целиком — то есть
    одна кривая строка гасит все метрики источника сразу.
    """
    out = value.replace("\\", "\\\\").replace(chr(34), "\\" + chr(34))
    return out.replace("\n", "\\n")


def render_value(value: float | int | bool) -> str:
    """Число в текст без экспоненты для целых и без хвоста .0."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


class Family:
    """Одно семейство метрик: HELP, TYPE и его сэмплы."""

    def __init__(self, name: str, help_text: str, metric_type: str = "gauge") -> None:
        self.name = name
        self.help_text = help_text
        self.metric_type = metric_type
        self.samples: list[tuple[list[tuple[str, str]], float]] = []

    def add(self, labels: list[tuple[str, str]], value: float) -> None:
        self.samples.append((labels, value))

    def render(self) -> list[str]:
        lines = [
            "# HELP %s %s" % (self.name, self.help_text),
            "# TYPE %s %s" % (self.name, self.metric_type),
        ]
        # Сортировка обязательна: без неё порядок серий зависит от порядка
        # обхода словарей, файл меняется без изменения данных, а diff в git и
        # golden-тесты становятся бесполезными.
        for labels, value in sorted(self.samples, key=lambda s: s[0]):
            rendered = render_value(value)
            if labels:
                pairs = ",".join(
                    "%s=%s%s%s" % (sanitize_name(k), chr(34), escape_label_value(v), chr(34))
                    for k, v in labels
                )
                lines.append("%s{%s} %s" % (self.name, pairs, rendered))
            else:
                lines.append("%s %s" % (self.name, rendered))
        return lines


def render_exposition(families: list[Family]) -> str:
    """Текст экспозиции. Обязательный перевод строки в конце — без него
    последняя метрика считается обрезанной."""
    lines: list[str] = []
    for family in families:
        if family.samples:
            lines.extend(family.render())
    return "\n".join(lines) + "\n"


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Запись через временный файл и os.replace.

    node_exporter читает каталог по своему расписанию и наткнётся на
    полузаписанный файл, если писать в целевой путь напрямую: получится битая
    экспозиция и node_textfile_scrape_error=1. Тот же приём (mv, а не прямая
    запись) уже используется в ci/compliance-report.sh.
    """
    tmp = path.with_name("." + path.name + ".tmp." + str(os.getpid()))
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# ── Разбор времени ───────────────────────────────────────────────────────────


def parse_timestamp(raw, unit: str = "s") -> float | None:
    """Метка времени из записи: unix-число (s/ms) или ISO-8601 с Z.

    Возвращает None вместо исключения: одна нечитаемая метка не должна лишать
    Prometheus остальных метрик источника.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) / 1000.0 if unit == "ms" else float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return float(text) / 1000.0 if unit == "ms" else float(text)
        try:
            # fromisoformat в 3.11 понимает Z, но не гарантированно во всех
            # входных вариантах — приводим к +00:00 сами.
            return dt.datetime.fromisoformat(
                text.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            return None
    return None


def read_tail_lines(path: pathlib.Path, tail_bytes: int) -> list[str]:
    """Последние строки файла без чтения его целиком.

    Первая строка отбрасывается, если чтение началось не с начала файла: она
    почти наверняка обрезана посередине, и json.loads на ней даст ложную
    ошибку разбора.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
            partial = True
        else:
            partial = False
        chunk = handle.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if partial and lines:
        lines = lines[1:]
    return lines


# ── Адаптеры источников ──────────────────────────────────────────────────────


def adapter_jsonl_last_by_key(source: dict, now: float) -> tuple[list[Family], dict]:
    """JSONL, где каждая строка — результат одной проверки.

    Берётся ПОСЛЕДНЯЯ запись на каждое значение ключа (по умолчанию check):
    watchdog дописывает историю, а Prometheus интересует текущее состояние.
    История при этом не теряется — она уезжает в Loki через alloy.
    """
    path = pathlib.Path(source["path"])
    prefix = source.get("metric_prefix", sanitize_name(source["name"]))
    key_field = source.get("key", "check")
    status_field = source.get("status_field", "status")
    ok_values = [str(v) for v in source.get("status_ok_values", ["ok"])]
    ts_field = source.get("timestamp_field", "ts_ms")
    ts_unit = source.get("timestamp_unit", "ms")
    tail_bytes = int(source.get("tail_bytes", DEFAULT_TAIL_BYTES))

    lines = read_tail_lines(path, tail_bytes)
    latest: dict[str, dict] = {}
    malformed = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        key = record.get(key_field)
        if key is None:
            continue
        latest[str(key)] = record

    status = Family(
        prefix + "_check_status",
        "Результат последней проверки: 1=ok, 0=fail.",
    )
    seen_at = Family(
        prefix + "_check_last_run_timestamp_seconds",
        "Время последнего результата проверки (unix seconds).",
    )
    newest_ts = None
    for key, record in latest.items():
        labels = [(key_field, str(key))]
        raw_status = str(record.get(status_field, "")).lower()
        status.add(labels, 1 if raw_status in ok_values else 0)
        ts = parse_timestamp(record.get(ts_field), ts_unit)
        if ts is not None:
            seen_at.add(labels, ts)
            newest_ts = ts if newest_ts is None else max(newest_ts, ts)

    run_ts = Family(
        prefix + "_last_run_timestamp_seconds",
        "Время последнего прогона источника (unix seconds).",
    )
    if newest_ts is not None:
        run_ts.add([], newest_ts)

    # Без суффикса _total: он зарезервирован за счётчиками, и promtool check
    # metrics справедливо ругается на gauge с таким именем. Мелочь, но именно
    # такие мелочи потом ломают чужие дашборды и rate() по «счётчику».
    checks_known = Family(prefix + "_checks", "Число известных проверок.")
    checks_known.add([], len(latest))

    families = [status, seen_at, run_ts, checks_known]
    if malformed:
        broken = Family(
            prefix + "_malformed_lines",
            "Строк, не разобравшихся как JSON, в прочитанном хвосте.",
        )
        broken.add([], malformed)
        families.append(broken)

    meta = dict(series=len(latest), newest_ts=newest_ts, malformed=malformed)
    return families, meta


def adapter_json_gauges(source: dict, now: float) -> tuple[list[Family], dict]:
    """JSON-объект (сводка инструмента) в набор gauge-метрик по описанию полей.

    Так подключается любой инструмент, умеющий --json, без правки его кода и
    без дублирования его логики здесь. Пример — backup-manager.py status:
    он один знает, что такое «полный снапшот» и «пригодность к восстановлению»,
    и остаётся единственным источником этой истины.

    Поля, отсутствующие в JSON или равные null, ПРОПУСКАЮТСЯ, а не пишутся
    нулём: 0 и «данных нет» — разные утверждения, и подменять второе первым
    значит врать дашборду (last_drill_age_days=null означает «drill никогда не
    проводился», а не «проводился только что»).
    """
    path = pathlib.Path(source["path"])
    prefix = source.get("metric_prefix", sanitize_name(source["name"]))
    # ValueError, а не ToolError: битый JSON одного источника не должен
    # останавливать весь прогон (см. process_source). Иначе пустой или
    # недописанный файл гасит метрики ВСЕХ источников сразу, включая
    # собственные метрики моста — то есть ровно тот тихий отказ, ради
    # обнаружения которого конвейер и строится.
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ожидался JSON-объект, получен %s" % type(payload).__name__)

    families: list[Family] = []
    exported = 0
    for spec in source.get("field", []):
        if "json" not in spec or "metric" not in spec:
            raise ToolError(
                "источник %s: у [[source.field]] обязательны json и metric"
                % source["name"]
            )
        json_key = spec["json"]
        if json_key not in payload:
            continue
        raw = payload[json_key]
        if raw is None:
            continue
        if isinstance(raw, bool):
            value: float = 1.0 if raw else 0.0
        elif isinstance(raw, (int, float)):
            value = float(raw)
        else:
            # Строку в число не превращаем: молчаливое приведение однажды
            # покажет 0 там, где в JSON лежал текст ошибки.
            continue
        # scale переводит значение в базовую единицу Prometheus. Инструменты
        # отдают сводки в удобных человеку единицах (возраст в сутках), а
        # соглашение экспозиции требует секунд и байт — promtool check metrics
        # это проверяет и справедливо ругается на _days.
        value = value * float(spec.get("scale", 1))
        family = Family(
            prefix + "_" + sanitize_name(spec["metric"]),
            spec.get("help", "Поле %s сводки %s." % (json_key, source["name"])),
        )
        family.add([], value)
        families.append(family)
        exported += 1

    newest_ts = parse_timestamp(
        payload.get(source.get("timestamp_field", "evaluated_at")),
        source.get("timestamp_unit", "s"),
    )
    run_ts = Family(
        prefix + "_last_run_timestamp_seconds",
        "Время последнего прогона источника (unix seconds).",
    )
    if newest_ts is not None:
        run_ts.add([], newest_ts)
        families.append(run_ts)

    meta = dict(series=exported, newest_ts=newest_ts, malformed=0)
    return families, meta


ADAPTERS = dict(
    jsonl_last_by_key=adapter_jsonl_last_by_key,
    json_gauges=adapter_json_gauges,
)


# ── Собственные метрики моста ────────────────────────────────────────────────


def self_metrics(results: list[dict], duration: float, now: float) -> list[Family]:
    """Метрики о самом моcте.

    Без них конвейер отказывает бесшумно: мост перестал запускаться — файлы
    остались на диске, node_exporter продолжает их отдавать, дашборд показывает
    последние значения как текущие. Именно на bks_metrics_bridge_last_run_*
    опирается правило bks:batch_job_age_seconds в слое агрегации.
    """
    run_ts = Family(
        SELF_PREFIX + "_last_run_timestamp_seconds",
        "Время последнего прогона моста (unix seconds).",
    )
    run_ts.add([], now)

    # Длительность берётся из настенных часов прогона, а не из now - started:
    # now может быть подставлен (--now) для воспроизводимых тестов, и тогда
    # разница между логическим и реальным временем давала бы отрицательную
    # длительность — метрику, невозможную по смыслу.
    duration_family = Family(
        SELF_PREFIX + "_duration_seconds", "Длительность прогона моста."
    )
    duration_family.add([], max(0.0, duration))

    ok = Family(
        SELF_PREFIX + "_source_ok",
        "Источник обработан без ошибок: 1=да, 0=нет.",
    )
    series = Family(
        SELF_PREFIX + "_source_series",
        "Число серий, снятых с источника.",
    )
    age = Family(
        SELF_PREFIX + "_source_age_seconds",
        "Возраст самых свежих ДАННЫХ источника (не mtime файла).",
    )
    stale = Family(
        SELF_PREFIX + "_source_stale",
        "Данные источника старше его max_age_seconds: 1=да, 0=нет.",
    )
    malformed = Family(
        SELF_PREFIX + "_source_malformed_lines",
        "Нечитаемых строк в прочитанном хвосте источника.",
    )

    for result in results:
        labels = [("source", result["name"])]
        ok.add(labels, 1 if result["ok"] else 0)
        series.add(labels, result.get("series", 0))
        malformed.add(labels, result.get("malformed", 0))
        newest = result.get("newest_ts")
        if newest is not None:
            age.add(labels, max(0.0, now - newest))
        stale.add(labels, 1 if result.get("stale") else 0)

    return [run_ts, duration_family, ok, series, age, stale, malformed]


# ── Прогон ───────────────────────────────────────────────────────────────────


def load_config(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise ToolError("нет файла источников: %s" % path)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ToolError("битый TOML %s: %s" % (path, exc)) from exc


def process_source(source: dict, now: float,
                   out_dir: pathlib.Path | None = None) -> tuple[list[Family], dict]:
    """Один источник. Ошибка источника не прерывает остальные.

    Относительный path трактуется относительно out_dir. Это нужно источникам,
    чей файл готовит сам конвейер (backup-status.json пишет ci/metrics-bridge.sh
    в тот же каталог): с абсолютным путём в каталоге источников переопределение
    METRICS_OUT_DIR перестало бы работать наполовину — .prom-файлы уезжали бы в
    новый каталог, а сводка искалась в старом.
    """
    name = source.get("name")
    if not name:
        raise ToolError("источник без обязательного поля name")
    kind = source.get("kind")
    if kind not in ADAPTERS:
        raise ToolError(
            "источник %s: неизвестный kind %r (есть: %s)"
            % (name, kind, ", ".join(sorted(ADAPTERS)))
        )
    if not source.get("path"):
        raise ToolError("источник %s: не задан path" % name)

    result = dict(name=name, ok=False, series=0, malformed=0, newest_ts=None,
                  stale=False, error=None)
    path = pathlib.Path(source["path"])
    if not path.is_absolute() and out_dir is not None:
        path = out_dir / path
        source = dict(source, path=str(path))
    if not path.is_file():
        # Отсутствующий файл — нормальная ситуация до первого прогона задачи или
        # до миграции хранилища. Это не ошибка инструмента, но и не «всё хорошо»:
        # ok=0 попадёт в Prometheus и будет видно на дашборде.
        result["error"] = "файла нет: %s" % path
        return [], result

    families, meta = ADAPTERS[kind](source, now)
    result.update(ok=True, series=meta.get("series", 0),
                  malformed=meta.get("malformed", 0),
                  newest_ts=meta.get("newest_ts"))

    max_age = float(source.get("max_age_seconds", 0) or 0)
    newest = meta.get("newest_ts")
    if max_age > 0:
        if newest is None:
            result["stale"] = True
            result["error"] = "в данных нет разбираемой метки времени"
        elif now - newest > max_age:
            result["stale"] = True
            result["error"] = "данные старше %.0f с (возраст %.0f с)" % (
                max_age, now - newest
            )
    return families, result


def run(config: dict, out_dir: pathlib.Path, only: list[str], to_stdout: bool,
        now: float) -> tuple[int, list[dict], str]:
    started = time.time()
    sources = config.get("source", [])
    if not sources:
        raise ToolError("в конфиге нет ни одного [[source]]")
    if only:
        known = set(s.get("name") for s in sources)
        unknown = sorted(set(only) - known)
        if unknown:
            raise ToolError("нет таких источников: %s" % ", ".join(unknown))
        sources = [s for s in sources if s.get("name") in only]

    if not to_stdout:
        out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    rendered_parts: list[str] = []
    for source in sources:
        try:
            families, result = process_source(source, now, out_dir)
        except (ValueError, OSError) as exc:
            # Изоляция источников — обязательное свойство: мост обслуживает
            # несколько независимых задач, и сбой одной не должен лишать
            # Prometheus метрик остальных. Проблема при этом не прячется:
            # bks_metrics_bridge_source_ok уйдёт нулём.
            families = []
            result = dict(name=source.get("name", "?"), ok=False, series=0,
                          malformed=0, newest_ts=None, stale=True,
                          error="%s: %s" % (type(exc).__name__, exc))
        results.append(result)
        text = render_exposition(families) if families else ""
        if text:
            if to_stdout:
                rendered_parts.append(text)
            else:
                write_atomic(out_dir / source["out"], text)

    self_text = render_exposition(
        self_metrics(results, time.time() - started, now)
    )
    if to_stdout:
        rendered_parts.append(self_text)
    else:
        write_atomic(out_dir / SELF_OUT, self_text)

    failed = sum(1 for r in results if not r["ok"] or r["stale"])
    return failed, results, "".join(rendered_parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Артефакты задач-по-таймеру -> textfile-экспозиция Prometheus"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="каталог источников (metrics/sources.toml)")
    parser.add_argument("--out-dir",
                        help="куда писать .prom (по умолчанию из [defaults])")
    parser.add_argument("--source", action="append", default=[],
                        help="обработать только этот источник (можно повторять)")
    parser.add_argument("--stdout", action="store_true",
                        help="вывести экспозицию в stdout и ничего не писать")
    parser.add_argument("--strict", action="store_true",
                        help="rc=1, если источник недоступен или данные протухли")
    parser.add_argument("--now", type=float,
                        help="зафиксировать время (unix seconds) для тестов")
    args = parser.parse_args(argv)

    try:
        config = load_config(pathlib.Path(args.config))
        defaults = config.get("defaults", {})
        out_dir = pathlib.Path(
            args.out_dir or defaults.get("out_dir", DEFAULT_OUT_DIR)
        )
        now = args.now if args.now is not None else time.time()
        failed, results, text = run(
            config, out_dir, args.source, args.stdout, now
        )
    except ToolError as exc:
        print("metrics-bridge: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("metrics-bridge: ошибка ввода-вывода: %s" % exc, file=sys.stderr)
        return 2

    if args.stdout:
        sys.stdout.write(text)

    for result in results:
        state = "ok" if result["ok"] and not result["stale"] else "ПРОБЛЕМА"
        detail = " (%s)" % result["error"] if result["error"] else ""
        print("metrics-bridge: %-14s %-8s серий=%d%s"
              % (result["name"], state, result["series"], detail),
              file=sys.stderr)

    if failed and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
