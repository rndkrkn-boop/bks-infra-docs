"""Тесты движка версионированных бэкапов (scripts/backup-manager.py).

Проверяем инструмент, а не состояние прода: политика хранения меняется, а
движок обязан одинаково честно считать полноту, удерживать GFS-слоты и
отказываться восстанавливать в production-пути.

Каждый тест собирает синтетическое хранилище во временном каталоге: реальные
sqlite-базы и реальные gzip-архивы, потому что половина проверок (integrity_check,
обход tar) на заглушках попросту не сработала бы.
"""

import datetime as dt
import importlib.util
import io
import pathlib
import sqlite3
import tarfile

import pytest

ENGINE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backup-manager.py"


def load_engine():
    """Импортировать модуль из файла с дефисом в имени."""
    spec = importlib.util.spec_from_file_location("backup_manager", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_engine()

# Минимальная политика: два обязательных артефакта вместо десяти прод-набора.
# Тесты про логику полноты, а не про конкретный состав — состав живёт в
# backup/retention.toml (движок и политика — nemohermes_bks, не host-infra;
# host-infra даёт только сам скрипт bks-backup.sh и systemd-юниты) и
# меняется без правки тестов.
POLICY = dict(
    meta=dict(version="test"),
    layout=dict(root="/unused", snapshots_dir="snapshots"),
    retention=dict(daily=2, weekly=1, monthly=1, yearly=1, min_keep=1,
                   gfs_requires_complete=True, incomplete_max_age_days=1),
    artifacts=[
        dict(name="kanban-default.db", kind="sqlite", required=True, min_bytes=1),
        dict(name="profiles.tar.gz", kind="tar.gz", required=True, min_bytes=1),
    ],
    drill=dict(forbidden_targets=["/etc"], max_age_days=30),
)


def make_db(path, rows=3, corrupt=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO cards (title) VALUES (?)", [("c%d" % i,) for i in range(rows)])
    conn.commit()
    conn.close()
    if corrupt:
        # Портим середину файла: заголовок остаётся валидным, поэтому «файл
        # похож на базу» — ровно тот случай, который ловит только integrity_check.
        data = bytearray(path.read_bytes())
        for offset in range(len(data) // 2, min(len(data) // 2 + 512, len(data))):
            data[offset] = 0
        path.write_bytes(bytes(data))
    return path


def make_tar(path, files=2, truncate=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for i in range(files):
            payload = b"payload-%d" % i
            info = tarfile.TarInfo("dir/file-%d.txt" % i)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    if truncate:
        # Обрезаем архив: gzip открывается, но чтение членов падает — типичный
        # результат снятия tar с каталога, в который параллельно пишут.
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])
    return path


def make_snapshot(root, snapshot_id, complete=True, errors=0, corrupt_db=False, truncate_tar=False):
    """Создать снапшот с манифестом, как это делает bks-backup.sh."""
    snap = root / "snapshots" / snapshot_id
    snap.mkdir(parents=True, exist_ok=True)
    make_db(snap / "kanban-default.db", corrupt=corrupt_db)
    if complete:
        make_tar(snap / "profiles.tar.gz", truncate=truncate_tar)
    manifest = engine.build_manifest(snap, POLICY, errors=errors, source="test")
    engine.write_manifest(snap, manifest)
    return engine.Snapshot(snap)


NOW = dt.datetime(2026, 8, 4, 18, 0, 0, tzinfo=dt.timezone.utc)


# ── Манифест и полнота ───────────────────────────────────────────────────────


def test_complete_snapshot_gets_complete_status(tmp_path):
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    assert snap.status == engine.STATUS_COMPLETE
    assert snap.manifest["present_required"] == 2
    assert snap.manifest["missing"] == []


def test_missing_required_artifact_is_partial_not_complete(tmp_path):
    """Регрессия: в v1 отсутствие пяти kanban DB давало «успешный» бэкап.

    SKIP не инкрементировал счётчик ошибок, скрипт выходил с кодом 0 и писал
    .last_backup. Полноту теперь считает движок по фактическому содержимому.
    """
    snap = make_snapshot(tmp_path, "20260804T030000Z", complete=False)
    assert snap.status == engine.STATUS_PARTIAL
    assert snap.manifest["missing"] == ["profiles.tar.gz"]


def test_errors_from_backup_run_downgrade_status(tmp_path):
    """Полный набор файлов при errors>0 всё равно не complete.

    Ошибка на шаге сбора означает, что часть данных могла не попасть в
    артефакт, который формально существует нужного размера.
    """
    snap = make_snapshot(tmp_path, "20260804T030000Z", errors=1)
    assert snap.status == engine.STATUS_PARTIAL


def test_snapshot_without_manifest_is_failed(tmp_path):
    """Без манифеста полнота непроверяема, значит снапшот не годится под GFS."""
    snap_dir = tmp_path / "snapshots" / "20260804T030000Z"
    make_db(snap_dir / "kanban-default.db")
    make_tar(snap_dir / "profiles.tar.gz")
    snap = engine.Snapshot(snap_dir)
    assert snap.status == engine.STATUS_FAILED
    assert "manifest.json missing" in snap.manifest_error


def test_undersized_artifact_is_flagged(tmp_path):
    """Файл есть, но подозрительно мал — типичный признак сорвавшейся выгрузки."""
    policy = dict(POLICY)
    policy["artifacts"] = [
        dict(name="kanban-default.db", kind="sqlite", required=True, min_bytes=10 ** 9),
        dict(name="profiles.tar.gz", kind="tar.gz", required=True, min_bytes=1),
    ]
    snap_dir = tmp_path / "snapshots" / "20260804T030000Z"
    make_db(snap_dir / "kanban-default.db")
    make_tar(snap_dir / "profiles.tar.gz")
    manifest = engine.build_manifest(snap_dir, policy)
    assert manifest["status"] == engine.STATUS_PARTIAL
    assert manifest["undersized"] == ["kanban-default.db"]


def test_extra_files_do_not_affect_completeness(tmp_path):
    """Посторонний файл получает sha256, но на 2/2 обязательных не влияет."""
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    (snap.path / "note.txt").write_text("ручная выгрузка перед миграцией", encoding="utf-8")
    manifest = engine.build_manifest(snap.path, POLICY)
    extras = [a for a in manifest["artifacts"] if a.get("extra")]
    assert [a["name"] for a in extras] == ["note.txt"]
    assert manifest["status"] == engine.STATUS_COMPLETE


# ── verify ───────────────────────────────────────────────────────────────────


def test_verify_passes_on_intact_snapshot(tmp_path):
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    result = engine.verify_snapshot(snap, POLICY)
    assert result["ok"], result["violations"]


def test_verify_detects_byte_level_corruption(tmp_path):
    """Подмена байта ловится sha256 даже при неизменном размере файла."""
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    target = snap.path / "kanban-default.db"
    data = bytearray(target.read_bytes())
    data[100] ^= 0xFF
    target.write_bytes(bytes(data))
    result = engine.verify_snapshot(snap, POLICY)
    assert not result["ok"]
    assert any("sha256_mismatch" in v for v in result["violations"])


def test_verify_detects_corrupt_sqlite_page(tmp_path):
    """integrity_check ловит порчу страниц: файл открывается, данные битые."""
    snap_dir = tmp_path / "snapshots" / "20260804T030000Z"
    make_db(snap_dir / "kanban-default.db", rows=400, corrupt=True)
    make_tar(snap_dir / "profiles.tar.gz")
    engine.write_manifest(snap_dir, engine.build_manifest(snap_dir, POLICY))
    result = engine.verify_snapshot(engine.Snapshot(snap_dir), POLICY)
    assert not result["ok"]
    assert any("integrity" in v or "sqlite" in v for v in result["violations"])


def test_verify_detects_truncated_tar(tmp_path):
    """Усечённый gzip открывается — падает только полный обход архива."""
    snap_dir = tmp_path / "snapshots" / "20260804T030000Z"
    make_db(snap_dir / "kanban-default.db")
    make_tar(snap_dir / "profiles.tar.gz", files=40, truncate=True)
    engine.write_manifest(snap_dir, engine.build_manifest(snap_dir, POLICY))
    result = engine.verify_snapshot(engine.Snapshot(snap_dir), POLICY)
    assert not result["ok"]
    assert any("tar" in v for v in result["violations"])


def test_verify_read_only_does_not_change_checksums(tmp_path):
    """verify не имеет права изменить архив: иначе он сам создаёт «повреждение».

    sqlite3.connect на запись выполняет recovery и дописывает файл; следующий
    verify сообщил бы о несовпадении sha256, которого не было до проверки.
    """
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    before = engine.sha256_file(snap.path / "kanban-default.db")
    engine.verify_snapshot(snap, POLICY)
    engine.verify_snapshot(snap, POLICY)
    assert engine.sha256_file(snap.path / "kanban-default.db") == before


# ── Ретеншен (GFS) ───────────────────────────────────────────────────────────


def plan_for(tmp_path, ids, incomplete=(), now=NOW, policy=POLICY, protected=None):
    for snapshot_id in ids:
        make_snapshot(tmp_path, snapshot_id, complete=snapshot_id not in incomplete)
    snapshots, _ = engine.discover_snapshots(tmp_path / "snapshots")
    plan = engine.plan_retention(snapshots, policy, now, protected=protected)
    return plan, set(i["id"] for i in plan["keep"]), set(i["id"] for i in plan["delete"])


def test_daily_slots_keep_newest_per_day(tmp_path):
    """Два прогона в одни сутки: дневной слот занимает более свежий.

    В v1 второй прогон физически затирал первый, потому что имя файла содержало
    только дату. Теперь существуют оба снапшота, а выбор делает политика — и его
    видно в плане построчно, вместо «файл молча исчез».
    """
    plan, keep, delete = plan_for(tmp_path, [
        "20260804T030000Z", "20260804T170000Z", "20260803T030000Z", "20260802T030000Z",
    ])
    reasons = {i["id"]: i["reasons"] for i in plan["keep"]}
    assert "daily[2026-08-04]" in reasons["20260804T170000Z"]
    # Утренний прогон того же дня слот не занимает — при daily=2 второй слот
    # уходит предыдущим суткам, а не второму снапшоту тех же суток.
    assert "20260804T030000Z" in delete
    assert "daily[2026-08-03]" in reasons["20260803T030000Z"]
    assert "20260802T030000Z" in delete


def test_weekly_and_monthly_slots_are_independent(tmp_path):
    """Суть GFS: вчерашний снапшот может уйти, а двухмесячный — остаться.

    Слоты разных периодов независимы, поэтому старый снапшот удерживается
    месячным слотом даже когда все дневные слоты заняты более свежими.
    """
    policy = dict(POLICY)
    policy["retention"] = dict(daily=1, weekly=2, monthly=3, yearly=1, min_keep=1,
                               gfs_requires_complete=True, incomplete_max_age_days=0)
    plan, keep, delete = plan_for(tmp_path, [
        "20260804T030000Z", "20260803T030000Z", "20260727T030000Z", "20260601T030000Z",
    ], policy=policy)
    reasons = {i["id"]: i["reasons"] for i in plan["keep"]}
    assert "monthly[2026-06]" in reasons["20260601T030000Z"]
    assert "weekly[2026-W31]" in reasons["20260727T030000Z"]
    assert "20260803T030000Z" in delete
    # Один снапшот занимает несколько слотов одновременно, и это не «тратит» их:
    # 20260601 всё равно удержан своим месячным слотом.
    assert len([r for r in reasons["20260804T030000Z"] if "[" in r]) >= 3


def test_retention_counts_buckets_not_wall_clock(tmp_path):
    """Простой системы не должен приводить к очистке хранилища.

    Считаем «последние N бакетов», как restic/borg. Если считать «моложе N
    дней», то месяц без бэкапов означал бы потерю всех копий — авария
    наблюдаемости превратилась бы в потерю данных.
    """
    far_future = dt.datetime(2027, 8, 4, tzinfo=dt.timezone.utc)
    _, keep, _ = plan_for(tmp_path, ["20260804T030000Z", "20260803T030000Z"], now=far_future)
    assert keep == {"20260804T030000Z", "20260803T030000Z"}


def test_incomplete_snapshot_never_takes_gfs_slot(tmp_path):
    """Битый снапшот не вытесняет хороший из слота того же дня.

    Это и есть механизм, которого не хватало v1: 2026-08-04 неполный прогон
    занял бы дневную позицию и через 7 дней хороший снапшот удалился бы, а
    неполный остался.
    """
    plan, keep, _ = plan_for(
        tmp_path,
        ["20260804T030000Z", "20260804T170000Z", "20260803T030000Z"],
        incomplete=("20260804T170000Z",),
    )
    reasons = {i["id"]: i["reasons"] for i in plan["keep"]}
    assert "daily[2026-08-04]" in reasons["20260804T030000Z"]
    assert not any(r.startswith("daily") for r in reasons.get("20260804T170000Z", []))


def test_incomplete_snapshot_kept_within_grace_window(tmp_path):
    """Неполный снапшот — улика для разбора, а не мусор: держим его сутки."""
    plan, keep, _ = plan_for(
        tmp_path, ["20260804T030000Z", "20260804T170000Z"], incomplete=("20260804T170000Z",))
    reasons = {i["id"]: i["reasons"] for i in plan["keep"]}
    assert "incomplete_grace" in reasons["20260804T170000Z"]


def test_old_incomplete_snapshot_is_deleted(tmp_path):
    """За пределами grace-окна неполный снапшот удаляется."""
    _, keep, delete = plan_for(
        tmp_path,
        ["20260804T030000Z", "20260803T030000Z", "20260710T170000Z"],
        incomplete=("20260710T170000Z",),
    )
    assert "20260710T170000Z" in delete


def test_min_keep_protects_newest_even_if_broken(tmp_path):
    """Хранилище нельзя обнулить: min_keep держит новейшие вне зависимости от статуса."""
    policy = dict(POLICY)
    policy["retention"] = dict(daily=0, weekly=0, monthly=0, yearly=0, min_keep=2,
                               gfs_requires_complete=True, incomplete_max_age_days=0)
    _, keep, delete = plan_for(
        tmp_path, ["20260804T030000Z", "20260803T030000Z", "20260802T030000Z"], policy=policy)
    assert keep == {"20260804T030000Z", "20260803T030000Z"}
    assert delete == {"20260802T030000Z"}


def test_protected_snapshot_is_never_deleted(tmp_path):
    """Цель симлинка latest не удаляется: битая ссылка ломает restore-процедуры."""
    policy = dict(POLICY)
    policy["retention"] = dict(daily=1, weekly=0, monthly=0, yearly=0, min_keep=1,
                               gfs_requires_complete=True, incomplete_max_age_days=0)
    _, keep, delete = plan_for(
        tmp_path, ["20260804T030000Z", "20260701T030000Z"], policy=policy,
        protected={"20260701T030000Z"})
    assert "20260701T030000Z" in keep


def test_apply_retention_is_idempotent(tmp_path):
    """Повторный apply не удаляет ничего: план — функция состояния, а не истории."""
    plan, _, delete = plan_for(tmp_path, [
        "20260804T030000Z", "20260803T030000Z", "20260601T030000Z", "20260501T030000Z",
    ])
    snap_dir = tmp_path / "snapshots"
    removed = engine.apply_retention(snap_dir, plan)
    assert all(r["removed"] for r in removed)
    snapshots, _ = engine.discover_snapshots(snap_dir)
    plan2 = engine.plan_retention(snapshots, POLICY, NOW)
    assert plan2["delete"] == []


def test_non_snapshot_directories_are_reported_not_deleted(tmp_path):
    """Посторонний каталог пропускается явно: молчаливый пропуск неотличим от бага."""
    (tmp_path / "snapshots" / "ручная-копия").mkdir(parents=True)
    make_snapshot(tmp_path, "20260804T030000Z")
    snapshots, invalid = engine.discover_snapshots(tmp_path / "snapshots")
    assert invalid == ["ручная-копия"]
    assert [s.id for s in snapshots] == ["20260804T030000Z"]


def test_iso_week_buckets_span_year_boundary(tmp_path):
    """31 декабря и 1 января одной ISO-недели делят один недельный слот."""
    keys_dec = engine.bucket_keys(dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc))
    keys_jan = engine.bucket_keys(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))
    assert keys_dec["weekly"] == keys_jan["weekly"]
    assert keys_dec["monthly"] != keys_jan["monthly"]


# ── restore-drill ────────────────────────────────────────────────────────────


def test_drill_restores_and_validates_data(tmp_path):
    """Drill не просто читает архив: он восстанавливает и считает строки."""
    store = tmp_path / "store"
    snap = make_snapshot(store, "20260804T030000Z")
    report = engine.restore_drill(snap, POLICY, store, workdir=tmp_path / "restore")
    assert report["ok"], report["failures"]
    by_name = {a["name"]: a for a in report["artifacts"]}
    assert by_name["kanban-default.db"]["rows"] == 3
    assert by_name["kanban-default.db"]["integrity"] == "ok"
    assert by_name["profiles.tar.gz"]["files"] == 2


def test_drill_fails_on_empty_database(tmp_path):
    """Формально валидная, но пустая база — типичный «бэкап» несуществующего файла."""
    store = tmp_path / "store"
    snap_dir = store / "snapshots" / "20260804T030000Z"
    snap_dir.mkdir(parents=True)
    sqlite3.connect(snap_dir / "kanban-default.db").close()  # 0 таблиц
    make_tar(snap_dir / "profiles.tar.gz")
    engine.write_manifest(snap_dir, engine.build_manifest(snap_dir, POLICY))
    report = engine.restore_drill(engine.Snapshot(snap_dir), POLICY, store,
                                  workdir=tmp_path / "restore")
    assert not report["ok"]
    assert any("без таблиц" in f for f in report["failures"])


def test_drill_refuses_production_targets(tmp_path):
    """Опечатка в --workdir не должна давать restore поверх production.

    Регламент аудита запрещает восстановление в production-пути, поэтому запрет
    живёт в коде, а не только в README.
    """
    snap = make_snapshot(tmp_path / "store", "20260804T030000Z")
    with pytest.raises(engine.DrillRefused):
        engine.restore_drill(snap, POLICY, tmp_path / "store",
                             workdir=pathlib.Path("/etc/bks-restore"))


def test_drill_refuses_to_restore_into_backup_root(tmp_path):
    """Восстановление внутрь самого хранилища смешало бы копии с восстановленным."""
    snap = make_snapshot(tmp_path, "20260804T030000Z")
    with pytest.raises(engine.DrillRefused):
        engine.restore_drill(snap, POLICY, tmp_path, workdir=tmp_path / "snapshots" / "restore")


def test_drill_does_not_mutate_snapshot(tmp_path):
    """Восстановление обязано быть неразрушающим для архива."""
    store = tmp_path / "store"
    snap = make_snapshot(store, "20260804T030000Z")
    before = {p.name: engine.sha256_file(p) for p in snap.path.iterdir() if p.is_file()}
    engine.restore_drill(snap, POLICY, store, workdir=tmp_path / "restore")
    after = {p.name: engine.sha256_file(p) for p in snap.path.iterdir() if p.is_file()}
    assert before == after


def test_drill_report_is_recorded_for_audit(tmp_path):
    """Отчёт переживает временный каталог: это свидетельство для аудита."""
    store = tmp_path / "store"
    snap = make_snapshot(store, "20260804T030000Z")
    report = engine.restore_drill(snap, POLICY, store, workdir=tmp_path / "restore")
    path = engine.record_drill(store, report)
    assert path.is_file()
    marker = (store / ".last_drill").read_text(encoding="utf-8")
    assert "20260804T030000Z" in marker and "ok" in marker


def test_drill_tar_extraction_cannot_escape_workdir(tmp_path):
    """Архив с абсолютными путями не должен распаковываться в реальный /home.

    profiles.tar.gz собирается из ~/.hermes и содержит абсолютные пути; без
    filter="data" распаковка ушла бы за пределы каталога восстановления.
    """
    store = tmp_path / "store"
    snap_dir = store / "snapshots" / "20260804T030000Z"
    make_db(snap_dir / "kanban-default.db")
    evil = snap_dir / "profiles.tar.gz"
    evil.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(evil, "w:gz") as tar:
        payload = b"pwned"
        info = tarfile.TarInfo("/home/admin/.hermes/escaped.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    engine.write_manifest(snap_dir, engine.build_manifest(snap_dir, POLICY))
    workdir = tmp_path / "restore"
    engine.restore_drill(engine.Snapshot(snap_dir), POLICY, store, workdir=workdir)
    escaped = list((workdir / "profiles.extracted").rglob("escaped.txt"))
    assert escaped, "файл должен распаковаться внутрь workdir"
    for path in escaped:
        assert workdir.resolve() in path.resolve().parents


# ── status и миграция ────────────────────────────────────────────────────────


def test_status_flags_missing_complete_snapshot(tmp_path):
    """«Свежий файл есть» не равно «можно восстановиться»."""
    make_snapshot(tmp_path, "20260804T030000Z", complete=False)
    info = engine.storage_status(tmp_path, POLICY, NOW)
    assert not info["ok"]
    assert any("полного снапшота" in p for p in info["problems"])


def test_status_flags_stale_drill(tmp_path):
    """Отсутствие drill — самостоятельная проблема, а не деталь."""
    make_snapshot(tmp_path, "20260804T030000Z")
    info = engine.storage_status(tmp_path, POLICY, NOW)
    assert any("restore-drill" in p for p in info["problems"])


def test_status_ok_after_successful_drill(tmp_path):
    store = tmp_path / "store"
    make_snapshot(store, "20260804T030000Z")
    snapshots, _ = engine.discover_snapshots(store / "snapshots")
    report = engine.restore_drill(snapshots[-1], POLICY, store, workdir=tmp_path / "restore")
    engine.record_drill(store, report)
    info = engine.storage_status(store, POLICY, NOW)
    assert info["ok"], info["problems"]


def test_migration_groups_legacy_files_by_date_from_name(tmp_path):
    """Дата берётся из имени, а не из mtime: mtime у файлов v1 недостоверен.

    Их перезаписывали несколько раз за сутки, поэтому mtime показывает момент
    последней перезаписи, а не момент съёма данных.
    """
    make_db(tmp_path / "kanban-default-20260803.db")
    make_tar(tmp_path / "profiles-20260803.tar.gz")
    make_db(tmp_path / "kanban-default-20260804.db")
    plan = engine.plan_migration(tmp_path, POLICY)
    assert set(plan["groups"]) == {"20260803T000000Z", "20260804T000000Z"}
    applied = engine.apply_migration(tmp_path, POLICY, plan)
    by_id = {a["id"]: a for a in applied}
    assert by_id["20260803T000000Z"]["status"] == engine.STATUS_COMPLETE
    assert by_id["20260804T000000Z"]["status"] == engine.STATUS_PARTIAL
    assert not (tmp_path / "kanban-default-20260803.db").exists()


def test_migration_ignores_foreign_files(tmp_path):
    """Файл не по шаблону v1 не трогается: миграция не должна съедать чужое."""
    (tmp_path / "notes.txt").write_text("руками положили", encoding="utf-8")
    plan = engine.plan_migration(tmp_path, POLICY)
    assert plan["groups"] == {}
    assert "notes.txt" in plan["skipped"]
    engine.apply_migration(tmp_path, POLICY, plan)
    assert (tmp_path / "notes.txt").is_file()
