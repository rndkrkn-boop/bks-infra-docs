"""Тесты реестра компонентов (scripts/check-components-registry.py).

Как и check-docs-consistency.py, скрипт при импорте читает docs/components.toml
и README.md по пути, вычисленному от собственного расположения, и завершает
процесс через sys.exit(0|1) при запуске как __main__. Гоняем копию скрипта
отдельным процессом над синтетическим деревом файлов, боевые
docs/components.toml/README.md не трогаем.
"""

import pathlib
import shutil
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check-components-registry.py"

MANIFEST_OK = """
[[components]]
path = "."
kind = "meta"
readme_entry = false

[[components]]
path = "router"
kind = "build-and-push"
readme_entry = true
"""

README_OK = """# README

## Состав

| [`router/`](./router/) | роутер |
"""


def make_tree(tmp_path, manifest=MANIFEST_OK, readme=README_OK, repo_dirs=("router",)):
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, tmp_path / "scripts" / "check-components-registry.py")
    (tmp_path / "docs" / "components.toml").write_text(manifest, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    for name in repo_dirs:
        (tmp_path / name / ".git").mkdir(parents=True, exist_ok=True)
    return tmp_path / "scripts" / "check-components-registry.py"


def run(script_path):
    return subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)


def test_согласованный_реестр_проходит(tmp_path):
    script = make_tree(tmp_path)
    result = run(script)

    assert result.returncode == 0
    assert "OK: 2 компонентов" in result.stdout


def test_необъявленный_компонент_валится(tmp_path):
    """Ровно инцидент matrix/: каталог с .git есть, записи в манифесте нет."""
    script = make_tree(tmp_path, repo_dirs=("router", "matrix"))
    result = run(script)

    assert result.returncode == 1
    assert "необъявленный компонент: matrix" in result.stdout


def test_запись_без_каталога_валится(tmp_path):
    script = make_tree(tmp_path, repo_dirs=())  # router из манифеста, но каталога нет
    result = run(script)

    assert result.returncode == 1
    assert "router" in result.stdout
    assert "больше нет" in result.stdout


def test_отсутствие_meta_валится(tmp_path):
    manifest = """
[[components]]
path = "router"
kind = "build-and-push"
readme_entry = true
"""
    script = make_tree(tmp_path, manifest=manifest)
    result = run(script)

    assert result.returncode == 1
    assert 'kind="meta"' in result.stdout


def test_неизвестный_kind_валится(tmp_path):
    manifest = """
[[components]]
path = "."
kind = "meta"
readme_entry = false

[[components]]
path = "router"
kind = "totally-made-up"
readme_entry = true
"""
    script = make_tree(tmp_path, manifest=manifest)
    result = run(script)

    assert result.returncode == 1
    assert "неизвестный kind" in result.stdout


def test_readme_entry_без_упоминания_валится(tmp_path):
    script = make_tree(tmp_path, readme="# README\n\nничего про роутер\n")
    result = run(script)

    assert result.returncode == 1
    assert "не найдено в README.md" in result.stdout


def test_боевой_манифест_валиден_и_прогоняется():
    """Смоук по реальному репозиторию: манифест и файловая система не разъехались."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=SCRIPT.parent.parent,
    )
    assert result.returncode == 0, result.stdout
    assert "OK:" in result.stdout
