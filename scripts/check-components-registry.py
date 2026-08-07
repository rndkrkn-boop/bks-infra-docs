#!/usr/bin/env python3
"""Реестр компонентов: docs/components.toml <-> файловая система <-> README.md.

Появился после инцидента с matrix/: git-репозиторий прожил больше двух
недель, ни разу не попав ни в README.md, ни в GitLab, ни в daily-backup, и
scripts/check-docs-consistency.py этого не заметил — та проверка сверяет
только ARCHITECTURE.md/ARCHITECTURE_MERMAID.md/README.md/DEPLOY.md между
собой, но не смотрит на файловую систему.

Эта проверка — наоборот, целиком про файловую систему: любой каталог с
.git обязан быть объявлен в docs/components.toml, и наоборот.
"""

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "components.toml"
README = (ROOT / "README.md").read_text(encoding="utf-8")

# Единственный источник допустимых значений kind — здесь, не в самом
# манифесте: манифест не должен иметь возможности расширить себе разрешённый
# словарь незамеченным изменением одной строки.
ALLOWED_KINDS = {
    "meta",
    "vendored-upstream",
    "config-validate-only",
    "build-and-push",
    "sandbox-profile-sync",
    "host-native-deploy",
    "compose-deploy-from-source",
}

# Каталоги, в которые сознательно не спускаемся при обходе: сам .git корня
# (это и есть запись kind="meta", path="."), плюс типичный мусор, который
# может случайно завести вложенный .git (venv/node_modules чужих репо).
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def discover_repo_dirs(root: pathlib.Path, max_depth: int = 4) -> set[str]:
    """Каталоги с .git внутри, относительно root, без самого root.

    Аналог `find . -maxdepth 4 -name .git -type d`, использованного вручную
    при архитектурном аудите — здесь тот же обход воспроизведён в коде,
    чтобы не полагаться на то, что кто-то помнит повторить его руками.
    """
    found: set[str] = set()

    def walk(path: pathlib.Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(path.iterdir())
        except (PermissionError, OSError):
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in SKIP_DIR_NAMES:
                continue
            if (child / ".git").exists():
                found.add(str(child.relative_to(root)))
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return found


def main() -> int:
    errors: list[str] = []

    if not MANIFEST_PATH.is_file():
        print(f"✗ манифест не найден: {MANIFEST_PATH}")
        return 1

    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    components = manifest.get("components", [])

    manifest_paths: dict[str, dict] = {}
    for entry in components:
        path = entry.get("path")
        if path in manifest_paths:
            errors.append(f"дубль записи для path={path!r}")
        else:
            manifest_paths[path] = entry

    # ── 1. Каждый найденный каталог с .git обязан быть в манифесте ────────
    discovered = discover_repo_dirs(ROOT)
    declared = {p for p in manifest_paths if p != "."}
    only_discovered = sorted(discovered - declared)
    only_manifest = sorted(declared - discovered)
    for path in only_discovered:
        errors.append(
            f"необъявленный компонент: {path}/ содержит .git, но не в "
            f"docs/components.toml (см. инцидент matrix/)"
        )
    for path in only_manifest:
        errors.append(
            f"в docs/components.toml, но каталога {path}/.git больше нет"
        )

    # ── 2. Ровно одна запись kind="meta", и её path — "." ──────────────────
    meta_entries = [p for p, e in manifest_paths.items() if e.get("kind") == "meta"]
    if len(meta_entries) != 1:
        errors.append(
            f"ожидалась ровно одна запись kind=\"meta\" (мета-репо сам "
            f"nemohermes_bks), найдено {len(meta_entries)}: {meta_entries}"
        )
    elif meta_entries[0] != ".":
        errors.append(f"запись kind=\"meta\" обязана иметь path=\".\", а не {meta_entries[0]!r}")

    # ── 3. kind — из фиксированного множества ──────────────────────────────
    for path, entry in manifest_paths.items():
        kind = entry.get("kind")
        if kind not in ALLOWED_KINDS:
            errors.append(
                f"{path}: неизвестный kind={kind!r}, ожидалось одно из {sorted(ALLOWED_KINDS)}"
            )

    # ── 4. readme_entry=true обязан буквально встречаться в README.md ─────
    for path, entry in manifest_paths.items():
        if entry.get("readme_entry"):
            marker = f"[`{path}/`](./{path}/)"
            if marker not in README:
                errors.append(
                    f"{path}: readme_entry=true, но {marker!r} не найдено в README.md"
                )

    if errors:
        print("Реестр компонентов разошёлся с реальностью:")
        for e in errors:
            print(f"  ✗ {e}")
        print(
            "\nПравьте docs/components.toml, README.md или сам каталог "
            "одним коммитом."
        )
        return 1

    print(f"OK: {len(manifest_paths)} компонентов зарегистрировано в docs/components.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
