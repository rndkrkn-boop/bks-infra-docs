#!/usr/bin/env python3
"""Версионированные бэкапы BKS: манифесты, GFS-ретеншен, verify, restore-drill.

Инструмент решает три проблемы v1-схемы бэкапа (`host-infra/backup/bks-backup.sh`
до 2026-08-04), каждая из которых наблюдалась на живой системе:

1. Дата в имени файла ⇒ перезапись. Три прогона 2026-08-04 подряд переписали
   `memgraphrag-data-20260804.tar.gz`; вернуться к первому (хорошему) было
   некуда. Здесь единица хранения — иммутабельный каталог-снапшот
   `snapshots/<YYYYMMDDTHHMMSSZ>/`, а имена артефактов внутри него без даты.

2. Полнота не отслеживалась. `SKIP: board not yet created` не инкрементировал
   счётчик ошибок, поэтому 2026-08-04 бэкап отчитался успехом при нуле из пяти
   kanban DB. Манифест фиксирует ожидаемый и фактический состав, и status
   `complete` выставляется только при 8/8.

3. Ротация по mtime. `find -mtime +7` смотрит на время файла, а перезапись его
   «омолаживает». Ретеншен здесь работает по snapshot_id (моменту съёма данных)
   и по бакетам GFS, а не по времени модификации inode.

Zero-dep: только stdlib (tomllib с 3.11). Причина та же, что у движка
комплаенса, — CI-джобы репо стартуют на python:3.11-slim без `pip install`.

Подкоманды:
  manifest       собрать/пересобрать manifest.json для каталога снапшота
  verify         проверить целостность снапшота (sha256, sqlite, tar)
  retention      план и применение политики хранения
  restore-drill  восстановить снапшот в изолированный каталог и проверить данные
  migrate        перенести плоские файлы v1 в версионированную раскладку
  status         сводка пригодности хранилища к восстановлению

Все подкоманды поддерживают --json: вывод машиночитаем для watchdog и CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

# Версия схемы манифеста. Растёт при несовместимом изменении формата: verify
# отказывается читать незнакомую схему, вместо того чтобы молча считать
# отсутствующее поле пустым.
MANIFEST_SCHEMA_VERSION = 1

MANIFEST_NAME = "manifest.json"
ID_FORMAT = "%Y%m%dT%H%M%SZ"
# Политика живёт в этом же репозитории, а не в host-infra: host-infra —
# отдельный git-репозиторий, заигноренный родителем, поэтому CI и
# комплаенс-аудит его файлов не видят вообще. Политика, которую нельзя
# проверить в CI, проверяется только ночью на живом бэкапе.
DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "backup" / "retention.toml"

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# Размер блока чтения для sha256: 1 МиБ — компромисс между syscall-оверхедом и
# резидентной памятью. profiles.tar.gz на проде ~21 МБ, qdrant ~2 МБ.
CHUNK = 1024 * 1024


# ─── Политика ────────────────────────────────────────────────────────────────


def load_policy(path: Path) -> dict:
    """Прочитать retention.toml и заполнить дефолты отсутствующих секций."""
    with open(path, "rb") as fh:
        policy = tomllib.load(fh)
    policy.setdefault("layout", {})
    policy.setdefault("retention", {})
    policy.setdefault("artifacts", [])
    policy.setdefault("drill", {})
    return policy


def required_artifacts(policy: dict) -> list[dict]:
    return [a for a in policy["artifacts"] if a.get("required", True)]


def snapshots_dir(policy: dict, root_override: str | None = None) -> Path:
    root = Path(root_override or policy["layout"].get("root", "/home/admin/backups/bks"))
    return root / policy["layout"].get("snapshots_dir", "snapshots")


# ─── Утилиты ─────────────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_snapshot_id(snapshot_id: str) -> dt.datetime:
    """snapshot_id -> aware UTC datetime. Бросает ValueError на мусоре."""
    return dt.datetime.strptime(snapshot_id, ID_FORMAT).replace(tzinfo=dt.timezone.utc)


def iso_z(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Snapshot:
    """Каталог снапшота вместе с прочитанным манифестом (если он есть)."""

    def __init__(self, path: Path):
        self.path = path
        self.id = path.name
        self.manifest: dict | None = None
        self.manifest_error: str | None = None
        manifest_path = path / MANIFEST_NAME
        if manifest_path.is_file():
            try:
                self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.manifest_error = "manifest unreadable: %s" % exc
        else:
            self.manifest_error = "manifest.json missing"

    @property
    def timestamp(self) -> dt.datetime:
        return parse_snapshot_id(self.id)

    @property
    def status(self) -> str:
        """Снапшот без читаемого манифеста не может считаться полным.

        Это не педантизм: манифест — единственный источник ожидаемого состава и
        контрольных сумм. Без него полнота непроверяема, а непроверяемую полноту
        нельзя записывать в слот GFS.
        """
        if not self.manifest:
            return STATUS_FAILED
        return self.manifest.get("status", STATUS_FAILED)

    def age_days(self, now: dt.datetime) -> float:
        return (now - self.timestamp).total_seconds() / 86400.0

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())


def discover_snapshots(snap_dir: Path) -> tuple[list[Snapshot], list[str]]:
    """Найти снапшоты. Второй элемент — каталоги с невалидным snapshot_id.

    Посторонний каталог не игнорируется молча: «ретеншен ничего не удалил» и
    «ретеншен не увидел половину хранилища» выглядят в логе одинаково, если о
    пропуске не сообщить.
    """
    snapshots: list[Snapshot] = []
    invalid: list[str] = []
    if not snap_dir.is_dir():
        return snapshots, invalid
    for child in sorted(snap_dir.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            parse_snapshot_id(child.name)
        except ValueError:
            invalid.append(child.name)
            continue
        snapshots.append(Snapshot(child))
    snapshots.sort(key=lambda s: s.id)
    return snapshots, invalid


# ─── manifest ────────────────────────────────────────────────────────────────


def build_manifest(snapshot_dir: Path, policy: dict, errors: int = 0,
                   source: str = "bks-backup.sh") -> dict:
    """Собрать manifest.json по фактическому содержимому каталога снапшота.

    Статус вычисляется, а не передаётся снаружи: у скрипта бэкапа не должно быть
    возможности объявить набор полным. Ровно эта возможность и давала «успешные»
    бэкапы без пяти kanban DB.
    """
    snapshot_id = snapshot_dir.name
    entries: list[dict] = []
    missing: list[str] = []
    undersized: list[str] = []

    for spec in policy["artifacts"]:
        name = spec["name"]
        path = snapshot_dir / name
        if not path.is_file():
            if spec.get("required", True):
                missing.append(name)
            continue
        size = path.stat().st_size
        min_bytes = int(spec.get("min_bytes", 1))
        if size < min_bytes:
            undersized.append(name)
        entries.append(dict(
            name=name,
            kind=spec.get("kind", "opaque"),
            required=bool(spec.get("required", True)),
            bytes=size,
            sha256=sha256_file(path),
            min_bytes=min_bytes,
            undersized=size < min_bytes,
        ))

    # Файлы, которых нет в политике, попадают в манифест как extra: их sha256
    # нужен для verify, но на полноту они не влияют.
    known = set(spec["name"] for spec in policy["artifacts"])
    for path in sorted(snapshot_dir.iterdir()):
        if path.name == MANIFEST_NAME or not path.is_file() or path.name in known:
            continue
        entries.append(dict(
            name=path.name,
            kind="opaque",
            required=False,
            bytes=path.stat().st_size,
            sha256=sha256_file(path),
            min_bytes=0,
            undersized=False,
            extra=True,
        ))

    expected = len(required_artifacts(policy))
    present = sum(1 for e in entries if e.get("required"))
    if present == 0:
        status = STATUS_FAILED
    elif missing or undersized or errors > 0:
        status = STATUS_PARTIAL
    else:
        status = STATUS_COMPLETE

    return dict(
        schema_version=MANIFEST_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        created_at=iso_z(utcnow()),
        snapshot_taken_at=iso_z(parse_snapshot_id(snapshot_id)),
        host=os.uname().nodename,
        source=source,
        policy_version=policy.get("meta", {}).get("version", "unknown"),
        status=status,
        errors=int(errors),
        expected_required=expected,
        present_required=present,
        missing=sorted(missing),
        undersized=sorted(undersized),
        artifacts=entries,
    )


def write_manifest(snapshot_dir: Path, manifest: dict) -> Path:
    """Записать манифест атомарно: сначала .tmp, потом rename.

    Прерванная запись манифеста превратила бы валидный снапшот в status=failed
    (см. Snapshot.status), то есть в мусор с точки зрения ретеншена.
    """
    target = snapshot_dir / MANIFEST_NAME
    tmp = snapshot_dir / (MANIFEST_NAME + ".tmp")
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


# ─── verify ──────────────────────────────────────────────────────────────────


def check_sqlite(path: Path) -> dict:
    """PRAGMA integrity_check + перечисление пользовательских таблиц.

    Открываем read-only (URI mode=ro): verify не имеет права дописать WAL в
    архивную копию и тем изменить её sha256 — иначе следующий verify сообщит о
    несовпадении контрольной суммы, которое он сам же и создал.
    """
    result = dict(kind="sqlite", integrity=None, tables=[], rows=0, error=None)
    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        result["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        result["tables"] = tables
        total = 0
        for table in tables:
            # Имя таблицы приходит из sqlite_master, а не от пользователя, но
            # квотируем всё равно: таблица с дефисом в имени иначе даёт
            # синтаксическую ошибку.
            total += conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        result["rows"] = total
    except sqlite3.Error as exc:
        result["error"] = "sqlite: %s" % exc
    finally:
        if conn is not None:
            conn.close()
    return result


def check_targz(path: Path) -> dict:
    """Полный проход архива.

    Именно проход, а не `tarfile.is_tarfile`: усечённый gzip открывается
    успешно и падает только при чтении последних членов. Живой tar каталога
    Qdrant (без snapshot API) — как раз тот случай, где усечение вероятно.
    """
    result = dict(kind="tar.gz", members=0, error=None)
    try:
        with tarfile.open(path, "r:gz") as tar:
            count = 0
            for _ in tar:
                count += 1
            result["members"] = count
        if result["members"] == 0:
            result["error"] = "tar: архив не содержит ни одного члена"
    except (tarfile.TarError, OSError, EOFError) as exc:
        result["error"] = "tar: %s" % exc
    return result


def verify_snapshot(snapshot: Snapshot, policy: dict, deep: bool = True) -> dict:
    """Сверить снапшот с его манифестом и политикой.

    Три независимых уровня, потому что они ловят разные отказы:
      * манифест vs политика — недостающий обязательный артефакт (полнота);
      * файл vs манифест     — битый или подменённый байт (sha256);
      * содержимое           — sqlite/tar реально открываются (консистентность).
    """
    violations: list[str] = []
    checks: list[dict] = []

    if snapshot.manifest is None:
        return dict(snapshot=snapshot.id, ok=False, status=snapshot.status,
                    violations=[snapshot.manifest_error or "no manifest"], checks=[])

    schema = snapshot.manifest.get("schema_version")
    if schema != MANIFEST_SCHEMA_VERSION:
        violations.append("schema_version=%r не поддерживается (ожидается %d)" % (schema, MANIFEST_SCHEMA_VERSION))

    manifest_names = set(a["name"] for a in snapshot.manifest.get("artifacts", []))
    for spec in required_artifacts(policy):
        if spec["name"] not in manifest_names:
            violations.append("missing_required: %s" % spec["name"])

    for entry in snapshot.manifest.get("artifacts", []):
        name = entry["name"]
        path = snapshot.path / name
        check = dict(name=name, present=path.is_file(), sha256_ok=None, content=None)
        if not path.is_file():
            violations.append("missing_file: %s" % name)
            checks.append(check)
            continue
        size = path.stat().st_size
        check["bytes"] = size
        if size != entry.get("bytes"):
            violations.append("size_mismatch: %s (%d != %d)" % (name, size, entry.get("bytes", -1)))
        actual = sha256_file(path)
        check["sha256_ok"] = actual == entry.get("sha256")
        if not check["sha256_ok"]:
            violations.append("sha256_mismatch: %s" % name)
        if entry.get("undersized"):
            violations.append("undersized: %s (%d < %d)" % (name, size, entry.get("min_bytes", 0)))
        if deep:
            if entry.get("kind") == "sqlite":
                content = check_sqlite(path)
                if content["error"]:
                    violations.append("%s: %s" % (name, content["error"]))
                elif content["integrity"] != "ok":
                    violations.append("integrity_check: %s -> %s" % (name, content["integrity"]))
                check["content"] = content
            elif entry.get("kind") == "tar.gz":
                content = check_targz(path)
                if content["error"]:
                    violations.append("%s: %s" % (name, content["error"]))
                check["content"] = content
        checks.append(check)

    return dict(snapshot=snapshot.id, ok=not violations, status=snapshot.status,
                violations=violations, checks=checks)


# ─── retention (GFS) ─────────────────────────────────────────────────────────


def bucket_keys(moment: dt.datetime) -> dict:
    """Ключи бакетов GFS для момента съёма снапшота.

    ISO-неделя (isocalendar), а не «номер недели от начала года»: у ISO-недели
    год собственный, поэтому 2026-12-31 и 2027-01-01 могут попасть в одну
    неделю — и должны, иначе на стыке лет два снапшота занимают два слота.
    """
    iso = moment.isocalendar()
    return dict(
        daily=moment.strftime("%Y-%m-%d"),
        weekly="%04d-W%02d" % (iso[0], iso[1]),
        monthly=moment.strftime("%Y-%m"),
        yearly=moment.strftime("%Y"),
    )


def plan_retention(snapshots: list[Snapshot], policy: dict, now: dt.datetime,
                   protected: set[str] | None = None) -> dict:
    """Решить, какие снапшоты сохранить, а какие удалить.

    Считаем «последние N бакетов», а не «моложе N дней» (как restic/borg).
    Разница видна при простое: отсчёт от «сейчас» вычистил бы всё хранилище,
    если бэкап не запускался месяц, — то есть авария наблюдаемости превратилась
    бы в потерю данных. Отсчёт по бакетам сохраняет N последних существующих.

    Возвращает keep/delete и причины удержания по каждому снапшоту: ретеншен,
    который нельзя объяснить построчно, невозможно ревьюить.
    """
    ret = policy["retention"]
    protected = protected or set()
    desc = sorted(snapshots, key=lambda s: s.id, reverse=True)
    reasons: dict[str, list[str]] = {s.id: [] for s in snapshots}

    # 1. Абсолютный минимум — до любых политик и вне зависимости от статуса.
    min_keep = int(ret.get("min_keep", 0))
    for snap in desc[:min_keep]:
        reasons[snap.id].append("min_keep")

    # 2. GFS-слоты. Кандидатами по умолчанию идут только полные снапшоты: иначе
    #    битый прогон вытесняет из дневного слота хороший снапшот того же дня —
    #    именно так v1 терял данные при пересоздании sandbox'а.
    requires_complete = bool(ret.get("gfs_requires_complete", True))
    candidates = [s for s in desc if s.status == STATUS_COMPLETE or not requires_complete]
    for period in ("daily", "weekly", "monthly", "yearly"):
        limit = int(ret.get(period, 0))
        if limit <= 0:
            continue
        taken: dict[str, str] = {}
        for snap in candidates:
            key = bucket_keys(snap.timestamp)[period]
            if key in taken:
                continue
            if len(taken) >= limit:
                break
            taken[key] = snap.id
            reasons[snap.id].append("%s[%s]" % (period, key))

    # 3. Окно разбора для неполных снапшотов: они не данные, а улика.
    grace = float(ret.get("incomplete_max_age_days", 0))
    if grace > 0:
        for snap in desc:
            if snap.status != STATUS_COMPLETE and snap.age_days(now) <= grace:
                reasons[snap.id].append("incomplete_grace")

    # 4. Явная защита (например, цель симлинка latest). Удалить снапшот, на
    #    который указывает latest, — значит оставить систему с битой ссылкой.
    for sid in protected:
        if sid in reasons:
            reasons[sid].append("protected")

    keep = [s for s in desc if reasons[s.id]]
    delete = [s for s in desc if not reasons[s.id]]
    return dict(
        keep=[dict(id=s.id, status=s.status, reasons=reasons[s.id]) for s in keep],
        delete=[dict(id=s.id, status=s.status, bytes=s.size_bytes(),
                     age_days=round(s.age_days(now), 2)) for s in delete],
        policy_version=policy.get("meta", {}).get("version", "unknown"),
        evaluated_at=iso_z(now),
        total=len(snapshots),
    )


def apply_retention(snap_dir: Path, plan: dict) -> list[dict]:
    """Удалить снапшоты из плана. Возвращает журнал фактических удалений."""
    removed: list[dict] = []
    for item in plan["delete"]:
        target = snap_dir / item["id"]
        # Пути формируются из имён каталогов, найденных внутри snap_dir, но
        # проверка на выход за корень стоит копейки, а цена ошибки — rm -rf по
        # чужому пути.
        if snap_dir.resolve() not in target.resolve().parents:
            removed.append(dict(id=item["id"], removed=False, error="path escapes snapshots dir"))
            continue
        try:
            shutil.rmtree(target)
            removed.append(dict(id=item["id"], removed=True, bytes=item.get("bytes", 0)))
        except OSError as exc:
            removed.append(dict(id=item["id"], removed=False, error=str(exc)))
    return removed


def latest_target(root: Path) -> str | None:
    """snapshot_id, на который указывает симлинк latest (если он есть)."""
    link = root / "latest"
    if not link.is_symlink():
        return None
    try:
        return Path(os.readlink(link)).name
    except OSError:
        return None


# ─── restore-drill ───────────────────────────────────────────────────────────


class DrillRefused(Exception):
    """Drill отказался работать по соображениям безопасности цели."""


def assert_safe_target(workdir: Path, policy: dict, root: Path) -> None:
    """Запретить восстановление в production-пути.

    Регламент аудита (DEPLOY.md, «Backup: контракт полноты») прямо запрещает
    restore поверх production. Самый дешёвый способ этот запрет нарушить —
    опечатка в --workdir, поэтому проверка живёт в коде, а не в инструкции.
    """
    resolved = workdir.resolve()
    forbidden = [Path(p) for p in policy.get("drill", {}).get("forbidden_targets", [])]
    forbidden.append(root)
    for bad in forbidden:
        bad = bad.resolve() if bad.exists() else bad
        if resolved == bad or bad in resolved.parents:
            raise DrillRefused("отказ: %s находится внутри запрещённой цели %s" % (resolved, bad))


def restore_drill(snapshot: Snapshot, policy: dict, root: Path,
                  workdir: Path | None = None, keep: bool = False) -> dict:
    """Восстановить снапшот в изолированный каталог и проверить данные.

    Это не verify: verify читает архив на месте, drill проделывает реальную
    процедуру восстановления — копирует, распаковывает, открывает базы и
    считает строки. «Файл целый» и «из файла можно восстановиться» — разные
    утверждения, и первое исторически принимали за второе.
    """
    created_tmp = workdir is None
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="bks-restore-drill-"))
        assert_safe_target(workdir, policy, root)
    else:
        # Проверка ДО mkdir: иначе опечатка в --workdir успевает создать каталог
        # в запрещённом пути (или упасть с PermissionError вместо внятного
        # отказа), то есть запрет срабатывает уже после побочного эффекта.
        assert_safe_target(workdir, policy, root)
        workdir.mkdir(parents=True, exist_ok=True)

    report = dict(
        snapshot=snapshot.id,
        started_at=iso_z(utcnow()),
        workdir=str(workdir),
        status=snapshot.status,
        artifacts=[],
        failures=[],
        ok=False,
    )

    try:
        if snapshot.manifest is None:
            report["failures"].append(snapshot.manifest_error or "no manifest")
            return report

        for entry in snapshot.manifest.get("artifacts", []):
            name = entry["name"]
            src = snapshot.path / name
            item = dict(name=name, kind=entry.get("kind", "opaque"), restored=False)
            if not src.is_file():
                item["error"] = "отсутствует в снапшоте"
                report["failures"].append("%s: отсутствует" % name)
                report["artifacts"].append(item)
                continue

            # Копируем, а не читаем на месте: восстановление обязано быть
            # неразрушающим для архива, а sqlite при открытии на запись создаёт
            # рядом -wal/-shm.
            dst = workdir / name
            shutil.copy2(src, dst)

            if entry.get("kind") == "sqlite":
                content = check_sqlite(dst)
                item.update(content)
                if content["error"] or content["integrity"] != "ok":
                    report["failures"].append("%s: %s" % (name, content["error"] or content["integrity"]))
                elif not content["tables"]:
                    # Формально валидная, но пустая база — типичный результат
                    # «бэкапа» несуществующего файла.
                    report["failures"].append("%s: восстановленная база без таблиц" % name)
                else:
                    item["restored"] = True
            elif entry.get("kind") == "tar.gz":
                extract_to = workdir / (name.replace(".tar.gz", "") + ".extracted")
                extract_to.mkdir(parents=True, exist_ok=True)
                try:
                    with tarfile.open(dst, "r:gz") as tar:
                        # filter="data" (3.11.4+) отсекает абсолютные пути,
                        # ".." и спецфайлы: архив profiles.tar.gz собран из
                        # ~/.hermes с абсолютными путями, и без фильтра
                        # распаковка ушла бы в реальный /home.
                        tar.extractall(path=extract_to, filter="data")
                    files = [p for p in extract_to.rglob("*") if p.is_file()]
                    item["files"] = len(files)
                    if not files:
                        report["failures"].append("%s: распаковка дала 0 файлов" % name)
                    else:
                        item["restored"] = True
                except (tarfile.TarError, OSError, EOFError) as exc:
                    item["error"] = str(exc)
                    report["failures"].append("%s: распаковка не удалась (%s)" % (name, exc))
            else:
                item["restored"] = dst.stat().st_size > 0

            report["artifacts"].append(item)

        required_names = set(spec["name"] for spec in required_artifacts(policy))
        restored_required = set(i["name"] for i in report["artifacts"] if i["restored"] and i["name"] in required_names)
        missing = sorted(required_names - restored_required)
        if missing:
            report["failures"].append("не восстановлены обязательные артефакты: %s" % ", ".join(missing))
        report["restored_required"] = len(restored_required)
        report["expected_required"] = len(required_names)
        report["ok"] = not report["failures"]
        return report
    finally:
        report["finished_at"] = iso_z(utcnow())
        if created_tmp and not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def record_drill(root: Path, report: dict) -> Path:
    """Сохранить отчёт drill в root/drills/ и обновить маркер .last_drill.

    Отчёт — свидетельство для аудита (ISO 27001 A.8.13 требует проверять
    восстанавливаемость, а не только наличие копий), поэтому он переживает
    временный каталог, в котором проходил сам drill.
    """
    drills = root / "drills"
    drills.mkdir(parents=True, exist_ok=True)
    stamp = report.get("started_at", iso_z(utcnow())).replace(":", "").replace("-", "")
    path = drills / ("drill-%s-%s.json" % (report["snapshot"], stamp))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    marker = root / ".last_drill"
    marker.write_text("%s %s %s\n" % (report.get("started_at"), report["snapshot"],
                                      "ok" if report["ok"] else "FAIL"), encoding="utf-8")
    return path


# ─── migrate ─────────────────────────────────────────────────────────────────

# Плоские имена v1: <artifact>-YYYYMMDD.<ext>. Дата берётся из имени, а не из
# mtime: mtime у файлов v1 недостоверен именно потому, что их перезаписывали.
LEGACY_RE = None


def legacy_pattern():
    global LEGACY_RE
    if LEGACY_RE is None:
        import re
        LEGACY_RE = re.compile(r"^(?P<name>.+)-(?P<date>\d{8})\.(?P<ext>db|tar\.gz)$")
    return LEGACY_RE


def plan_migration(root: Path, policy: dict) -> dict:
    """Сгруппировать плоские файлы v1 в снапшоты по дате из имени.

    Существующие бэкапы не выбрасываются: 90 МБ архивов 2026-07-28..08-04 — это
    единственное, из чего можно восстановиться сегодня. Пока они лежат вне
    версионированной раскладки, для нового движка их не существует.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    skipped: list[str] = []
    if not root.is_dir():
        return dict(groups={}, skipped=[])
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        match = legacy_pattern().match(path.name)
        if not match:
            skipped.append(path.name)
            continue
        target_name = "%s.%s" % (match.group("name"), match.group("ext"))
        snapshot_id = "%sT000000Z" % match.group("date")
        groups.setdefault(snapshot_id, []).append((path.name, target_name))
    return dict(groups=groups, skipped=skipped)


def apply_migration(root: Path, policy: dict, plan: dict, copy: bool = False) -> list[dict]:
    """Перенести (или скопировать) файлы v1 в snapshots/<id>/ и собрать манифесты.

    Перенос по умолчанию: копирование удвоило бы 90 МБ и оставило бы два
    источника истины о том же дне — ровно ту неоднозначность, из-за которой v1
    и не удавалось проверить.
    """
    snap_dir = snapshots_dir(policy, str(root))
    snap_dir.mkdir(parents=True, exist_ok=True)
    migrated: list[dict] = []
    for snapshot_id, files in sorted(plan["groups"].items()):
        target_dir = snap_dir / snapshot_id
        target_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for src_name, dst_name in files:
            src = root / src_name
            dst = target_dir / dst_name
            if not src.is_file():
                continue
            if copy:
                shutil.copy2(src, dst)
            else:
                shutil.move(str(src), str(dst))
            moved += 1
        manifest = build_manifest(target_dir, policy, errors=0, source="legacy-v1-migration")
        write_manifest(target_dir, manifest)
        migrated.append(dict(id=snapshot_id, files=moved, status=manifest["status"],
                            present_required=manifest["present_required"],
                            expected_required=manifest["expected_required"]))
    return migrated


# ─── status ──────────────────────────────────────────────────────────────────


def storage_status(root: Path, policy: dict, now: dt.datetime) -> dict:
    """Сводка: можно ли из этого хранилища восстановиться сегодня.

    Сознательно отвечает не на вопрос «бэкап свежий?», а на вопрос
    «recoverability». Watchdog v1 проверял только возраст новейшего файла,
    поэтому backup_freshness=OK сосуществовал с нулём kanban DB в архиве.
    """
    snap_dir = snapshots_dir(policy, str(root))
    snapshots, invalid = discover_snapshots(snap_dir)
    complete = [s for s in snapshots if s.status == STATUS_COMPLETE]
    problems: list[str] = []

    latest = snapshots[-1] if snapshots else None
    latest_complete = complete[-1] if complete else None

    if not snapshots:
        problems.append("хранилище пусто: снапшотов нет")
    if latest_complete is None:
        problems.append("нет ни одного полного снапшота")
    else:
        age = latest_complete.age_days(now)
        # RPO 24 ч + запас на пропущенный запуск таймера и RandomizedDelaySec.
        if age > 2:
            problems.append("свежайший полный снапшот старше 2 суток (%.1f дн.)" % age)

    drill_marker = root / ".last_drill"
    drill_age = None
    if drill_marker.is_file():
        parts = drill_marker.read_text(encoding="utf-8").split()
        if parts:
            try:
                drill_age = (now - dt.datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=dt.timezone.utc)).total_seconds() / 86400.0
            except ValueError:
                problems.append(".last_drill не разбирается")
        if len(parts) > 2 and parts[2] != "ok":
            problems.append("последний restore-drill завершился неудачей")
    max_drill_age = float(policy.get("drill", {}).get("max_age_days", 0))
    if max_drill_age > 0:
        if drill_age is None:
            problems.append("restore-drill никогда не проводился")
        elif drill_age > max_drill_age:
            problems.append("restore-drill старше %s дней (%.1f)" % (max_drill_age, drill_age))

    if invalid:
        problems.append("посторонние каталоги в snapshots/: %s" % ", ".join(invalid))

    plan = plan_retention(snapshots, policy, now, protected=set(filter(None, [latest_target(root)])))

    return dict(
        root=str(root),
        snapshots=len(snapshots),
        complete=len(complete),
        partial=sum(1 for s in snapshots if s.status == STATUS_PARTIAL),
        failed=sum(1 for s in snapshots if s.status == STATUS_FAILED),
        latest=latest.id if latest else None,
        latest_complete=latest_complete.id if latest_complete else None,
        latest_complete_age_days=round(latest_complete.age_days(now), 2) if latest_complete else None,
        last_drill_age_days=round(drill_age, 2) if drill_age is not None else None,
        total_bytes=sum(s.size_bytes() for s in snapshots),
        retention_would_delete=len(plan["delete"]),
        problems=problems,
        ok=not problems,
        evaluated_at=iso_z(now),
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def human(num: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return "%.1f %s" % (num, unit)
        num /= 1024.0
    return "%d B" % num


def emit(payload: dict | list, as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)


def cmd_manifest(args, policy: dict) -> int:
    snapshot_dir = Path(args.snapshot).resolve()
    if not snapshot_dir.is_dir():
        print("нет такого каталога снапшота: %s" % snapshot_dir, file=sys.stderr)
        return 2
    manifest = build_manifest(snapshot_dir, policy, errors=args.errors, source=args.source)
    write_manifest(snapshot_dir, manifest)
    lines = ["%s: status=%s %d/%d обязательных, errors=%d" % (
        manifest["snapshot_id"], manifest["status"], manifest["present_required"],
        manifest["expected_required"], manifest["errors"])]
    if manifest["missing"]:
        lines.append("  отсутствуют: %s" % ", ".join(manifest["missing"]))
    if manifest["undersized"]:
        lines.append("  подозрительно малы: %s" % ", ".join(manifest["undersized"]))
    emit(manifest, args.json, lines)
    # Код возврата — свойство данных, а не удача записи файла: CI-джоб и
    # bks-backup.sh должны падать на неполном снапшоте.
    return 0 if manifest["status"] == STATUS_COMPLETE else 1


def select_snapshots(args, policy: dict) -> tuple[Path, list[Snapshot]]:
    root = Path(args.root or policy["layout"].get("root"))
    snap_dir = snapshots_dir(policy, str(root))
    snapshots, _ = discover_snapshots(snap_dir)
    if args.snapshot:
        snapshots = [s for s in snapshots if s.id == args.snapshot]
    elif getattr(args, "all", False):
        pass
    else:
        complete = [s for s in snapshots if s.status == STATUS_COMPLETE]
        # По умолчанию берём новейший ПОЛНЫЙ снапшот: проверять надо то, из чего
        # реально будут восстанавливаться, а не то, что просто оказалось последним.
        chosen = complete[-1:] if complete else snapshots[-1:]
        snapshots = chosen
    return root, snapshots


def cmd_verify(args, policy: dict) -> int:
    root, snapshots = select_snapshots(args, policy)
    if not snapshots:
        print("нечего проверять: снапшоты не найдены в %s" % snapshots_dir(policy, str(root)), file=sys.stderr)
        return 2
    results = [verify_snapshot(s, policy, deep=not args.shallow) for s in snapshots]
    lines = []
    for res in results:
        lines.append("%s: %s (status=%s)" % (res["snapshot"], "OK" if res["ok"] else "FAIL", res["status"]))
        for violation in res["violations"]:
            lines.append("  - %s" % violation)
    emit(results, args.json, lines)
    return 0 if all(r["ok"] for r in results) else 1


def cmd_retention(args, policy: dict) -> int:
    root = Path(args.root or policy["layout"].get("root"))
    snap_dir = snapshots_dir(policy, str(root))
    snapshots, invalid = discover_snapshots(snap_dir)
    now = parse_iso_now(args.now)
    plan = plan_retention(snapshots, policy, now, protected=set(filter(None, [latest_target(root)])))
    plan["invalid_dirs"] = invalid

    lines = ["план ретеншена (%s), снапшотов: %d" % (plan["policy_version"], plan["total"])]
    for item in plan["keep"]:
        lines.append("  KEEP   %s [%s] %s" % (item["id"], item["status"], ", ".join(item["reasons"])))
    for item in plan["delete"]:
        lines.append("  DELETE %s [%s] %s, возраст %s дн." % (
            item["id"], item["status"], human(item["bytes"]), item["age_days"]))
    if invalid:
        lines.append("  ПРОПУЩЕНЫ (не снапшоты): %s" % ", ".join(invalid))

    if args.apply:
        removed = apply_retention(snap_dir, plan)
        plan["removed"] = removed
        freed = sum(r.get("bytes", 0) for r in removed if r["removed"])
        lines.append("удалено: %d, освобождено %s" % (sum(1 for r in removed if r["removed"]), human(freed)))
        for entry in removed:
            if not entry["removed"]:
                lines.append("  ОШИБКА %s: %s" % (entry["id"], entry.get("error")))
        emit(plan, args.json, lines)
        return 0 if all(r["removed"] for r in removed) else 1

    lines.append("(dry-run; для применения нужен --apply)")
    emit(plan, args.json, lines)
    return 0


def cmd_drill(args, policy: dict) -> int:
    root, snapshots = select_snapshots(args, policy)
    if not snapshots:
        print("нет снапшота для drill", file=sys.stderr)
        return 2
    snapshot = snapshots[-1]
    try:
        report = restore_drill(snapshot, policy, root,
                               workdir=Path(args.workdir) if args.workdir else None,
                               keep=args.keep)
    except DrillRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.record:
        report["report_path"] = str(record_drill(root, report))
    lines = ["restore-drill %s: %s" % (report["snapshot"], "OK" if report["ok"] else "FAIL")]
    for item in report["artifacts"]:
        detail = ""
        if item.get("kind") == "sqlite":
            detail = "таблиц %d, строк %s, integrity=%s" % (
                len(item.get("tables", [])), item.get("rows"), item.get("integrity"))
        elif item.get("kind") == "tar.gz":
            detail = "файлов %s" % item.get("files")
        lines.append("  %s %s %s" % ("✓" if item["restored"] else "✗", item["name"], detail))
    for failure in report["failures"]:
        lines.append("  - %s" % failure)
    emit(report, args.json, lines)
    return 0 if report["ok"] else 1


def cmd_migrate(args, policy: dict) -> int:
    root = Path(args.root or policy["layout"].get("root"))
    plan = plan_migration(root, policy)
    lines = ["миграция v1 -> версионированная раскладка: групп %d" % len(plan["groups"])]
    for snapshot_id, files in sorted(plan["groups"].items()):
        lines.append("  %s: %d файлов" % (snapshot_id, len(files)))
    if plan["skipped"]:
        lines.append("  не тронуты (не подходят под шаблон v1): %s" % ", ".join(plan["skipped"]))
    payload = dict(plan=dict(groups={k: v for k, v in plan["groups"].items()}, skipped=plan["skipped"]))
    if args.apply:
        applied = apply_migration(root, policy, plan, copy=args.copy)
        payload["applied"] = applied
        for entry in applied:
            lines.append("  -> %s: %s (%d/%d обязательных)" % (
                entry["id"], entry["status"], entry["present_required"], entry["expected_required"]))
    else:
        lines.append("(dry-run; для применения нужен --apply)")
    emit(payload, args.json, lines)
    return 0


def cmd_status(args, policy: dict) -> int:
    root = Path(args.root or policy["layout"].get("root"))
    info = storage_status(root, policy, parse_iso_now(args.now))
    lines = [
        "хранилище: %s" % info["root"],
        "снапшотов: %d (полных %d, частичных %d, битых %d)" % (
            info["snapshots"], info["complete"], info["partial"], info["failed"]),
        "новейший полный: %s (возраст %s дн.)" % (info["latest_complete"], info["latest_complete_age_days"]),
        "последний restore-drill: %s дн. назад" % info["last_drill_age_days"],
        "занято: %s; ретеншен удалил бы: %d" % (human(info["total_bytes"]), info["retention_would_delete"]),
        "ИТОГ: %s" % ("OK" if info["ok"] else "ПРОБЛЕМЫ"),
    ]
    for problem in info["problems"]:
        lines.append("  - %s" % problem)
    emit(info, args.json, lines)
    return 0 if info["ok"] else 1


def parse_iso_now(value: str | None) -> dt.datetime:
    """--now для детерминированных тестов и воспроизводимых прогонов."""
    if not value:
        return utcnow()
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup-manager.py",
        description="Версионированные бэкапы BKS: манифесты, GFS-ретеншен, verify, restore-drill.",
    )
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="путь к retention.toml")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    sub = parser.add_subparsers(dest="command", required=True)

    p_manifest = sub.add_parser("manifest", help="собрать manifest.json для каталога снапшота")
    p_manifest.add_argument("--snapshot", required=True, help="путь к каталогу снапшота")
    p_manifest.add_argument("--errors", type=int, default=0, help="число ошибок прогона бэкапа")
    p_manifest.add_argument("--source", default="bks-backup.sh", help="кто создал снапшот")
    p_manifest.set_defaults(func=cmd_manifest)

    p_verify = sub.add_parser("verify", help="проверить целостность снапшота")
    p_verify.add_argument("--root", help="корень хранилища (по умолчанию из политики)")
    p_verify.add_argument("--snapshot", help="конкретный snapshot_id")
    p_verify.add_argument("--all", action="store_true", help="проверить все снапшоты")
    p_verify.add_argument("--shallow", action="store_true",
                          help="только sha256/размеры, без sqlite и tar")
    p_verify.set_defaults(func=cmd_verify)

    p_ret = sub.add_parser("retention", help="план и применение политики хранения")
    p_ret.add_argument("--root")
    p_ret.add_argument("--apply", action="store_true", help="реально удалить (по умолчанию dry-run)")
    p_ret.add_argument("--now", help="зафиксировать «сейчас» (YYYY-MM-DDTHH:MM:SSZ)")
    p_ret.set_defaults(func=cmd_retention)

    p_drill = sub.add_parser("restore-drill", help="восстановить снапшот в изолированный каталог")
    p_drill.add_argument("--root")
    p_drill.add_argument("--snapshot")
    p_drill.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    p_drill.add_argument("--workdir", help="куда восстанавливать (по умолчанию mkdtemp)")
    p_drill.add_argument("--keep", action="store_true", help="не удалять каталог восстановления")
    p_drill.add_argument("--record", action="store_true", help="сохранить отчёт в root/drills/")
    p_drill.set_defaults(func=cmd_drill)

    p_mig = sub.add_parser("migrate", help="перенести плоские файлы v1 в snapshots/")
    p_mig.add_argument("--root")
    p_mig.add_argument("--apply", action="store_true")
    p_mig.add_argument("--copy", action="store_true", help="копировать, а не перемещать")
    p_mig.set_defaults(func=cmd_migrate)

    p_status = sub.add_parser("status", help="сводка пригодности хранилища к восстановлению")
    p_status.add_argument("--root")
    p_status.add_argument("--now")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy_path = Path(args.policy)
    if not policy_path.is_file():
        print("политика не найдена: %s" % policy_path, file=sys.stderr)
        return 2
    try:
        policy = load_policy(policy_path)
    except tomllib.TOMLDecodeError as exc:
        print("политика не разбирается: %s" % exc, file=sys.stderr)
        return 2
    return args.func(args, policy)


if __name__ == "__main__":
    sys.exit(main())
