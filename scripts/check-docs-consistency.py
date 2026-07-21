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
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEPLOY = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")

errors: list[str] = []


def without_historical_chronology(text: str) -> str:
    """Blank explicitly dated old status lines, not current caution callouts."""
    return re.sub(r"(?m)^>\s*\*\*Статус 2026-07-0[1-9][^\n]*$", "", text)


def check_markers(
    document: str, text: str, markers: list[tuple[str, str]]
) -> None:
    """Report the first active occurrence of every stale marker."""
    active_text = without_historical_chronology(text)
    for pattern, why in markers:
        found = re.search(pattern, active_text, re.IGNORECASE | re.MULTILINE)
        if found:
            line_no = active_text[: found.start()].count("\n") + 1
            errors.append(
                f"устаревший маркер в {document}:{line_no}: "
                f"{found.group(0)!r} — {why}"
            )


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

# ── 2. Маркеры прошлых эпох не должны быть текущей архитектурой ───────────
# Цитированные строки (`> ...`) — датированная хроника и не являются текущим
# контрактом. Это позволяет хранить историю K3s без ложных срабатываний.
SHARED_STALE_MARKERS = [
    (
        r"\b8\s+(?:(?:Hermes|агентск\w+)\s+)?(?:agents?|profiles?|профил\w*)",
        "в production девять профилей: три gateway и шесть worker",
    ),
    (
        r"(?<!not )(?<!не )\bpull[\s-]+mirror\b",
        "bksamotsvety публикуется двумя независимыми push, не pull mirror",
    ),
    (
        r"(?:через|via)\s+\bcode_execution\b|(?:--?>|\|)\s*code_execution\b",
        "канонический путь MemGraphRAG — stdio MCP proxy → host :8010",
    ),
    (
        r"\bmemgraphrag\b(?![^\n]*:8010)[^\n]{0,80}:8000\b",
        "внешний host endpoint MemGraphRAG — :8010 (:8000 только container port)",
    ),
    (
        r"(?:unit-test\s*\(\s*25\s*\)|\b25\s+(?:tests?|тест\w*))",
        "актуальный router suite — 64 теста",
    ),
    (
        r"(?:\b54\s+(?:total|tests?|тест\w*)|unit-test\s*\(\s*54\s*\))",
        "актуальный MemGraphRAG suite — 56 тестов",
    ),
    (
        r"K3s[^\n]{0,100}(?:✅\s*(?:Running)?|\bRunning\b|"
        r"установлен\s+и\s+работает|кластер\s+(?:активен|работает))",
        "активного K3s-кластера нет; установленный unit сам по себе не активность",
    ),
    (
        r"k3s\.service[^\n]{0,40}\b(?:active|enabled|running)\b",
        "K3s unit может быть установлен, но активный/включённый cluster недопустим",
    ),
]
check_markers("ARCHITECTURE.md", ARCH, SHARED_STALE_MARKERS)
check_markers("ARCHITECTURE_MERMAID.md", MERM, SHARED_STALE_MARKERS)

# Эти маркеры исполняемой K3s-топологии запрещены именно в текущих диаграммах.
MERMAID_STALE_MARKERS = [
    (
        r"memgraphrag\.(?:memgraphrag\.)?svc",
        "K3s DNS заменён на host.openshell.internal:8010",
    ),
    (
        r"\bkubectl\b",
        "kubectl не нужен ни одному текущему контуру после декомиссии K3s",
    ),
]
check_markers("ARCHITECTURE_MERMAID.md", MERM, MERMAID_STALE_MARKERS)

# ── 3. Обязательные маркеры текущего runtime-контракта ────────────────────
REQUIRED_MARKERS = [
    (ARCH, "ARCHITECTURE.md", "NO-GO", "нет текущего acceptance status"),
    (ARCH, "ARCHITECTURE.md", "9 профилей", "канонический profile count — 9"),
    (
        ARCH,
        "ARCHITECTURE.md",
        "dispatch_in_gateway",
        "не зафиксирован integrated kanban dispatch",
    ),
    (
        ARCH,
        "ARCHITECTURE.md",
        "mcp_memgraphrag_",
        "не зафиксирован MCP-контракт памяти",
    ),
    (
        ARCH,
        "ARCHITECTURE.md",
        "offline-smoke",
        "потерян обязательный MemGraphRAG offline-smoke",
    ),
    (
        ARCH,
        "ARCHITECTURE.md",
        "live storage-каталог Qdrant",
        "не задокументирован consistency risk текущего Qdrant backup",
    ),
    (
        MERM,
        "ARCHITECTURE_MERMAID.md",
        "contract 3 / live 2",
        "не показан gateway contract/runtime drift",
    ),
    (README, "README.md", "9 профилей", "канонический profile count — 9"),
    (DEPLOY, "DEPLOY.md", "8 обязательных", "нет backup completeness contract"),
    (
        DEPLOY,
        "DEPLOY.md",
        "live tar каталога Qdrant",
        "не задокументирован текущий Qdrant backup method",
    ),
]
for text, document, marker, why in REQUIRED_MARKERS:
    if marker not in text:
        errors.append(f"{document}: {why} (ожидался маркер {marker!r})")

# В активной части playbook не должно быть исполняемых K3s-команд.
active_deploy = DEPLOY.split("## Историческое приложение — K3s", maxsplit=1)[0]
if re.search(r"(?m)^\s*(?:sudo\s+)?kubectl\b", active_deploy):
    errors.append("DEPLOY.md: kubectl найден вне исторического приложения")
for line_no, line in enumerate(active_deploy.splitlines(), start=1):
    if "10301" in line and not re.search(
        r"(?i)(?:декомиссирован|historical|legacy)", line
    ):
        errors.append(
            f"DEPLOY.md:{line_no}: прямой STT :10301 "
            "не помечен декомиссированным"
        )

# ── 4. Целостность mermaid-fence'ов ───────────────────────────────────────
# Незакрытый блок ломает рендер всего файла в GitLab/GitHub.
open_fences = MERM.count("```mermaid")
all_fences = len(re.findall(r"^```", MERM, re.M))
if open_fences == 0:
    errors.append("в ARCHITECTURE_MERMAID.md не найдено ни одного mermaid-блока")
if all_fences != open_fences * 2:
    errors.append(
        f"непарные code-fence'ы: {open_fences} ```mermaid "
        f"при {all_fences} ``` всего"
    )

if errors:
    print("Документация разошлась:")
    for e in errors:
        print(f"  ✗ {e}")
    print("\nПравьте ARCHITECTURE.md и ARCHITECTURE_MERMAID.md одним коммитом.")
    sys.exit(1)

print(
    f"OK: {len(merm_repos)} bks-репо в обоих файлах, "
    f"{open_fences} mermaid-блоков, маркеров прошлых эпох нет"
)
