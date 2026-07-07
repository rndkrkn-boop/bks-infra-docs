#!/usr/bin/env python3
"""Анти-drift проверки ARCHITECTURE.md <-> ARCHITECTURE_MERMAID.md.

Появился после инцидента 2026-07-08: текстовый ARCHITECTURE.md вёл хронику
декомиссии K3s (§2.5), а параллельный Mermaid-файл два дня показывал
«K3s ✅ Running» — дублирующая документация разошлась на эпоху.

Проверки сознательно грубые и дешёвые: ловим возврат маркеров прошлых эпох
и расхождение списка репозиториев, а не полную семантику диаграмм.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCH = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
MERM = (ROOT / "ARCHITECTURE_MERMAID.md").read_text(encoding="utf-8")

errors: list[str] = []

# ── 1. Наборы репозиториев bks/* в обоих файлах должны совпадать ──────────
# Новый репозиторий, описанный в тексте, обязан появиться и в диаграммах
# (и наоборот). "infra" исключён: монорепо сам себя в диаграммах не рисует.
repo_re = re.compile(r"\bbks/([a-z0-9_-]+)")
EXCLUDE = {"infra"}
arch_repos = set(repo_re.findall(ARCH)) - EXCLUDE
merm_repos = set(repo_re.findall(MERM)) - EXCLUDE
if arch_repos != merm_repos:
    only_arch = sorted(arch_repos - merm_repos)
    only_merm = sorted(merm_repos - arch_repos)
    if only_arch:
        errors.append(
            f"репо есть в ARCHITECTURE.md, но нет в диаграммах: {only_arch}"
        )
    if only_merm:
        errors.append(
            f"репо есть в диаграммах, но нет в ARCHITECTURE.md: {only_merm}"
        )

# ── 2. Маркеры прошлых эпох не должны возвращаться в диаграммы ────────────
# Проверяем ТОЛЬКО Mermaid-файл: в ARCHITECTURE.md §2.5 эти строки легально
# живут как историческая хроника.
STALE_MARKERS = [
    (r"K3s[^\n]*(?:✅|Running)", "K3s как работающий (декомиссирован 2026-07-06)"),
    (r"memgraphrag\.(?:memgraphrag\.)?svc", "K3s-DNS MemGraphRAG (теперь host.openshell.internal:8010)"),
    (r"MemGraphRAG[^\n]*:8000", "старый порт MemGraphRAG (теперь :8010)"),
    (r"pull mirror", "несуществующее зеркалирование bksamotsvety (два независимых push)"),
    (r"kubectl", "kubectl не нужен ни одному контуру после декомиссии K3s"),
]
for pattern, why in STALE_MARKERS:
    found = re.search(pattern, MERM)
    if found:
        line_no = MERM[: found.start()].count("\n") + 1
        errors.append(
            f"устаревший маркер в ARCHITECTURE_MERMAID.md:{line_no}: "
            f"{found.group(0)!r} — {why}"
        )

# ── 3. Целостность mermaid-fence'ов ───────────────────────────────────────
# Незакрытый блок ломает рендер всего файла в GitLab/GitHub.
open_fences = MERM.count("```mermaid")
all_fences = len(re.findall(r"^```", MERM, re.M))
if open_fences == 0:
    errors.append("в ARCHITECTURE_MERMAID.md не найдено ни одного mermaid-блока")
if all_fences != open_fences * 2:
    errors.append(
        f"непарные code-fence'ы: {open_fences} ```mermaid при {all_fences} ``` всего"
    )

if errors:
    print("Документация разошлась:")
    for e in errors:
        print(f"  ✗ {e}")
    print("\nПравьте ARCHITECTURE.md и ARCHITECTURE_MERMAID.md одним коммитом.")
    sys.exit(1)

print(f"OK: {len(merm_repos)} bks-репо в обоих файлах, {open_fences} mermaid-блоков, маркеров прошлых эпох нет")
