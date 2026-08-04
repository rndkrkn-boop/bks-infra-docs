#!/usr/bin/env python3
"""Рендер комплаенс-дашборда из JSON-отчёта `scripts/compliance-audit.py`.

Один самодостаточный HTML-файл: без CDN, без сборки, без внешних шрифтов.
Причина не в аскетизме — дашборд открывают в том числе на хосте, у которого
исходящий TLS исторически нестабилен, и в артефактах CI-джоба. Страница,
которая молча ломается без интернета, для дежурного бесполезна.

Второе представление — Grafana: `compliance/grafana-dashboard.json` поверх
метрик из --metrics-out. HTML отвечает на вопрос «что сейчас нарушено»,
Grafana — «как это менялось и не пора ли алертить».

Использование:
    python3 scripts/compliance-audit.py --json-out /tmp/report.json
    python3 scripts/compliance-dashboard.py /tmp/report.json --out dashboard.html
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys

# Статусная палитра (good / warning / critical) — она зарезервирована под
# состояния и никогда не используется как цвет серии. Цвет всегда идёт в паре
# с иконкой и текстовой подписью: на светлой поверхности warning не добирает
# 3:1, да и дальтоник обязан читать дашборд без цвета вообще.
STATUS_META = {
    "pass": {"label": "PASS", "icon": "✓", "color": "var(--status-good)"},
    "waived": {"label": "WAIVED", "icon": "≈", "color": "var(--status-warning)"},
    "fail": {"label": "FAIL", "icon": "✕", "color": "var(--status-critical)"},
    "error": {"label": "ERROR", "icon": "!", "color": "var(--status-serious)"},
}
STATUS_ORDER = ("pass", "waived", "fail", "error")

CSS = """
.viz-root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --status-good: #0ca30c;
  --status-warning: #fab219;
  --status-serious: #ec835a;
  --status-critical: #d03b3b;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page);
  color: var(--text-primary);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,0.10);
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19;
  --page: #0d0d0d;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --grid: #2c2c2a;
  --baseline: #383835;
  --border: rgba(255,255,255,0.10);
}
body { margin: 0; }
.viz-root { padding: 32px 28px 48px; }
.wrap { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 15px; margin: 32px 0 12px; color: var(--text-secondary);
     font-weight: 600; }
.sub { color: var(--text-secondary); font-size: 13px; margin: 0 0 24px; }
.card { background: var(--surface-1); border: 1px solid var(--border);
        border-radius: 10px; padding: 18px 20px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 12px; }
.hero { display: flex; align-items: baseline; gap: 14px; }
.hero .figure { font-size: 54px; font-weight: 650; line-height: 1;
                letter-spacing: -0.02em; }
.tile .value { font-size: 26px; font-weight: 600; line-height: 1.1; }
.tile .label { font-size: 12px; color: var(--text-secondary); margin-top: 4px;
               display: flex; align-items: center; gap: 6px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.rows { display: flex; flex-direction: column; gap: 10px; }
.row { display: grid; grid-template-columns: 120px 1fr 76px; align-items: center;
       gap: 12px; }
.row .name { font-size: 13px; color: var(--text-secondary); }
.row .count { font-size: 12px; color: var(--text-muted);
              font-variant-numeric: tabular-nums; text-align: right; }
.bar { display: flex; height: 14px; gap: 2px; }
.seg { height: 100%; }
.seg:first-child { border-top-left-radius: 4px; border-bottom-left-radius: 4px; }
.seg:last-child { border-top-right-radius: 4px; border-bottom-right-radius: 4px; }
.track { flex: 1; background: var(--grid); border-radius: 4px; height: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { text-align: left; font-weight: 600; font-size: 12px; color: var(--text-muted);
     border-bottom: 1px solid var(--baseline); padding: 8px 10px; }
td { border-bottom: 1px solid var(--grid); padding: 9px 10px;
     color: var(--text-secondary); vertical-align: top; }
td.id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        color: var(--text-primary); white-space: nowrap; }
td.title { color: var(--text-primary); }
.pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px;
        font-weight: 600; white-space: nowrap; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary);
          margin: 0 0 14px; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.note { font-size: 12px; color: var(--text-muted); margin-top: 10px; }
.findings { margin: 6px 0 0; padding-left: 18px; font-size: 12px;
            color: var(--text-muted); }
footer { margin-top: 36px; font-size: 12px; color: var(--text-muted); }
#tip { position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
       background: var(--surface-1); color: var(--text-primary); font-size: 12px;
       border: 1px solid var(--border); border-radius: 6px; padding: 6px 9px;
       box-shadow: 0 4px 14px rgba(0,0,0,.14); z-index: 9; }
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def status_pill(status: str) -> str:
    meta = STATUS_META[status]
    return (
        '<span class="pill" style="color:' + meta["color"] + '">'
        + meta["icon"] + " " + meta["label"] + "</span>"
    )


def legend() -> str:
    """Легенда обязательна: идентичность не должна держаться на одном цвете."""
    items = []
    for status in STATUS_ORDER:
        meta = STATUS_META[status]
        items.append(
            '<span><i class="dot" style="background:' + meta["color"] + '"></i>'
            + meta["icon"] + " " + meta["label"] + "</span>"
        )
    return '<div class="legend">' + "".join(items) + "</div>"


def stacked_row(name: str, buckets: dict, total: int) -> str:
    """Строка «имя — стек по статусам — счётчик».

    Стек, а не круговая диаграмма: сравнивать длины вдоль общей базовой линии
    человек умеет, а углы секторов — нет.
    """
    if total <= 0:
        return ""
    segments = []
    for status in STATUS_ORDER:
        count = buckets.get(status, 0)
        if not count:
            continue
        meta = STATUS_META[status]
        width = round(100.0 * count / total, 2)
        segments.append(
            '<div class="seg" style="width:' + str(width) + "%;background:"
            + meta["color"] + '" data-tip="' + esc(name + ": " + str(count) + " × "
            + meta["label"]) + '"></div>'
        )
    passed = buckets.get("pass", 0)
    return (
        '<div class="row"><div class="name">' + esc(name) + "</div>"
        + '<div class="bar">' + "".join(segments) + "</div>"
        + '<div class="count">' + str(passed) + "/" + str(total) + "</div></div>"
    )


def tiles(report: dict) -> str:
    summary = report["summary"]
    status = summary["by_status"]
    blocking = len(report["gate"]["blocking_rules"])
    cells = []
    for key in STATUS_ORDER:
        meta = STATUS_META[key]
        cells.append(
            '<div class="card tile"><div class="value" style="color:' + meta["color"]
            + '">' + str(status[key]) + "</div>"
            + '<div class="label"><i class="dot" style="background:' + meta["color"]
            + '"></i>' + meta["icon"] + " " + meta["label"] + "</div></div>"
        )
    gate_color = "var(--status-critical)" if blocking else "var(--status-good)"
    gate_icon = "✕" if blocking else "✓"
    cells.append(
        '<div class="card tile"><div class="value" style="color:' + gate_color + '">'
        + str(blocking) + "</div>"
        + '<div class="label"><i class="dot" style="background:' + gate_color
        + '"></i>' + gate_icon + " блокирует пайплайн</div></div>"
    )
    return '<div class="tiles">' + "".join(cells) + "</div>"


def hero(report: dict) -> str:
    summary = report["summary"]
    score = summary["score_percent"]
    color = "var(--status-good)" if score >= 90 else (
        "var(--status-warning)" if score >= 75 else "var(--status-critical)"
    )
    return (
        '<div class="card hero"><div class="figure" style="color:' + color + '">'
        + str(score) + "%</div>"
        + "<div><div>взвешенный балл соответствия</div>"
        + '<div class="note">' + str(summary["by_status"]["pass"]) + " из "
        + str(summary["total_rules"]) + " правил выполнено; waived не"
        + " засчитывается как выполнение</div></div></div>"
    )


def severity_section(report: dict) -> str:
    rows = []
    for severity in ("critical", "high", "medium", "low"):
        bucket = report["summary"]["by_severity"][severity]
        if not bucket["total"]:
            continue
        subset = [r for r in report["results"] if r["severity"] == severity]
        counts = {}
        for item in subset:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        rows.append(stacked_row(severity, counts, bucket["total"]))
    return '<div class="card"><div class="rows">' + "".join(rows) + "</div></div>"


def framework_section(report: dict) -> str:
    rows = []
    for name, bucket in sorted(report["summary"]["by_framework"].items()):
        subset = [r for r in report["results"] if r["framework"] == name]
        counts = {}
        for item in subset:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        rows.append(stacked_row(name, counts, bucket["total"]))
    return '<div class="card"><div class="rows">' + "".join(rows) + "</div></div>"


def waivers_section(report: dict) -> str:
    """Исключения с остатком срока: waiver без срока — это отмена правила."""
    items = [item for item in report["results"] if item["waiver"]]
    if not items:
        return '<div class="card"><div class="note">Действующих исключений нет.</div></div>'
    rows = ["<table><tr><th>Правило</th><th>Действует до</th><th>Владелец</th>"
            "<th>Тикет</th><th>Обоснование</th></tr>"]
    for item in items:
        waiver = item["waiver"]
        colour = "var(--status-critical)" if waiver["expired"] else "var(--status-warning)"
        mark = "✕ истёк" if waiver["expired"] else "≈ " + waiver["expires"]
        rows.append(
            '<tr><td class="id">' + esc(item["id"]) + "</td>"
            + '<td><span class="pill" style="color:' + colour + '">' + esc(mark)
            + "</span></td><td>" + esc(waiver["owner"]) + "</td><td>"
            + esc(waiver["ticket"]) + "</td><td>" + esc(waiver["reason"]) + "</td></tr>"
        )
    rows.append("</table>")
    return '<div class="card">' + "".join(rows) + "</div>"


def rules_table(report: dict) -> str:
    """Табличное представление — обязательный не-цветовой канал чтения."""
    rows = ["<table><tr><th>Правило</th><th>Требование</th><th>Framework</th>"
            "<th>Основание</th><th>Severity</th><th>Статус</th></tr>"]
    for item in report["results"]:
        findings = ""
        if item["findings"]:
            points = "".join(
                "<li>" + esc(found["file"]
                             + (":" + str(found["line"]) if found["line"] else ""))
                + " — " + esc(found["detail"]) + "</li>"
                for found in item["findings"][:6]
            )
            more = len(item["findings"]) - 6
            if more > 0:
                points += "<li>… ещё " + str(more) + "</li>"
            findings = '<ul class="findings">' + points + "</ul>"
        rows.append(
            '<tr><td class="id">' + esc(item["id"]) + '</td><td class="title">'
            + esc(item["title"]) + findings + "</td><td>" + esc(item["framework"])
            + "</td><td>" + esc(item["reference"]) + "</td><td>"
            + esc(item["severity"]) + "</td><td>" + status_pill(item["status"])
            + "</td></tr>"
        )
    rows.append("</table>")
    return '<div class="card">' + "".join(rows) + "</div>"


TOOLTIP_JS = """
// Ховер-слой: сегмент стека сам по себе не подписан, а подписывать каждый
// сегмент числом — шум. Тултип отдаёт точное значение по наведению.
(function () {
  var tip = document.getElementById('tip');
  document.querySelectorAll('[data-tip]').forEach(function (node) {
    node.addEventListener('mouseenter', function () {
      tip.textContent = node.getAttribute('data-tip');
      tip.style.opacity = '1';
    });
    node.addEventListener('mousemove', function (event) {
      tip.style.left = (event.clientX + 12) + 'px';
      tip.style.top = (event.clientY + 14) + 'px';
    });
    node.addEventListener('mouseleave', function () {
      tip.style.opacity = '0';
    });
  });
})();
"""


def render_html(report: dict) -> str:
    summary = report["summary"]
    head = [
        "<!doctype html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Комплаенс-дашборд bks/infra</title>",
        "<style>" + CSS + "</style></head>",
        '<body><div class="viz-root"><div class="wrap">',
        "<h1>Комплаенс-дашборд bks/infra</h1>",
        '<p class="sub">Прогон ' + esc(report["generated_at"]) + " · каталог правил v"
        + esc(report["rules_version"]) + " · правил " + str(summary["total_rules"])
        + " · файлов под git " + str(summary["scanned_files"])
        + " · гейт блокирует при severity ≥ " + esc(report["gate"]["block_at"])
        + "</p>",
    ]
    body = [
        hero(report),
        "<h2>Состояние правил</h2>",
        tiles(report),
        "<h2>Разрез по критичности</h2>",
        legend(),
        severity_section(report),
        "<h2>Разрез по требованиям регуляторов</h2>",
        framework_section(report),
        "<h2>Реестр исключений</h2>",
        waivers_section(report),
        "<h2>Все правила</h2>",
        rules_table(report),
        "<footer>Источник: <code>compliance/rules.toml</code> → "
        "<code>scripts/compliance-audit.py</code> → "
        "<code>scripts/compliance-dashboard.py</code>. "
        "Совпадения секрет-правил маскированы. Исторический разрез и алерты — "
        "в Grafana поверх метрик <code>bks_compliance_*</code>.</footer>",
        '</div></div><div id="tip" role="status"></div>',
        "<script>" + TOOLTIP_JS + "</script></body></html>",
    ]
    return "\n".join(head + body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HTML-дашборд комплаенс-аудита")
    parser.add_argument("report", type=pathlib.Path, help="JSON-отчёт compliance-audit.py")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("compliance-dashboard.html"),
        help="куда писать HTML",
    )
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print("нет JSON-отчёта: " + str(args.report), file=sys.stderr)
        return 2
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_html(report), encoding="utf-8")
    print(
        "дашборд собран: " + str(args.out) + " (" + str(report["summary"]["total_rules"])
        + " правил, балл " + str(report["summary"]["score_percent"]) + "%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
