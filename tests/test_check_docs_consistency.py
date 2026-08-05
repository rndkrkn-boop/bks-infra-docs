"""Тесты анти-drift проверки ARCHITECTURE.md <-> ARCHITECTURE_MERMAID.md
(scripts/check-docs-consistency.py).

Скрипт при импорте сразу читает четыре файла по пути, вычисленному от
собственного расположения (`ROOT = parent.parent __file__`), и завершает
процесс через sys.exit(0|1) — тестировать его in-process нельзя. Поэтому
гоняем копию скрипта отдельным процессом над синтетическим деревом файлов, не
трогая боевые ARCHITECTURE.md/ARCHITECTURE_MERMAID.md/README.md/DEPLOY.md.
"""

import pathlib
import shutil
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check-docs-consistency.py"

ARCH_OK = """# ARCHITECTURE

> Текущий статус: NO-GO по чек-листу.

Профилей в проде: 9 профилей (три gateway и шесть worker).

Диспетчеризация канбана встроена: dispatch_in_gateway.

Контракт памяти: mcp_memgraphrag_read/write.

Обязателен offline-smoke MemGraphRAG перед релизом.

Бэкап: live storage-каталог Qdrant копируется еженедельно.

Компоненты: bks/router, bks/monitoring.
"""

MERM_OK = """# ARCHITECTURE_MERMAID

```mermaid
graph TD
  router[bks/router]
  monitoring[bks/monitoring]
```

Текущее состояние: contract 3 / live 2 gateway.
"""

README_OK = """# README

Профилей в проде: 9 профилей.
"""

DEPLOY_OK = """# DEPLOY

Бэкап обязателен: 8 обязательных компонентов.

Метод: live tar каталога Qdrant.

## Историческое приложение — K3s

kubectl get pods -A
"""


def make_tree(tmp_path, arch=ARCH_OK, merm=MERM_OK, readme=README_OK, deploy=DEPLOY_OK):
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, tmp_path / "scripts" / "check-docs-consistency.py")
    (tmp_path / "ARCHITECTURE.md").write_text(arch, encoding="utf-8")
    (tmp_path / "ARCHITECTURE_MERMAID.md").write_text(merm, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "DEPLOY.md").write_text(deploy, encoding="utf-8")
    return tmp_path / "scripts" / "check-docs-consistency.py"


def run(script_path):
    return subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)


def test_согласованная_документация_проходит(tmp_path):
    script = make_tree(tmp_path)
    result = run(script)

    assert result.returncode == 0
    assert "OK:" in result.stdout


def test_пустой_architecture_md_валится_на_обязательных_маркерах(tmp_path):
    """Пустой входной файл — вырожденный случай, а не синтаксическая ошибка,
    но для этого скрипта (нет парсера, только текстовые проверки) это
    ближайший аналог «синтаксически некорректного» входа: обязательные
    маркеры пропадают и репозитории в ARCH/MERM расходятся."""
    script = make_tree(tmp_path, arch="")
    result = run(script)

    assert result.returncode == 1
    assert "Документация разошлась" in result.stdout
    assert "NO-GO" in result.stdout
