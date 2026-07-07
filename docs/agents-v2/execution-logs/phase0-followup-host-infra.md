# Phase 0 follow-up — host-infra под git/CI (2026-07-07)

Продолжение [phase0-execution-log.md](phase0-execution-log.md): пункты 0.5
(watchdog) и 0.10 (бэкапы) доведены от «скрипты лежат на хосте» до
управляемого состояния. Плюс — найдена и закрыта регрессия CI-раннера.

## Решение: watchdog остаётся systemd, НЕ контейнер

Вопрос поднят пользователем («не нравится, что watchdog — сервис на хосте,
а не контейнер»). Разобрано и решено: **слой исполнения оставить systemd**,
чинить управляемость, а не место запуска.

Аргументы (зафиксированы в ARCHITECTURE.md §2.6):
- сторож обязан стоять вне зоны отказа наблюдаемого: контейнеризованный
  watchdog молча умирает вместе с docker daemon — тот самый класс «тихих
  смертей», против которого Phase 0 затевалась;
- проверки требуют хостовых ресурсов: docker.sock (root-эквивалент),
  `nemohermes` CLI + креды, `nvidia-smi`, `df /`, каталог бэкапов —
  контейнер получился бы с фиктивной изоляцией;
- systemd — нижний supervision-слой машины: journald, авторестарт, таймеры.

Реальная проблема была не в systemd, а в том, что файлы были snowflake:
не версионировались, не деплоились, невосстановимы при потере хоста.

## Сделано

1. **Репозиторий `bks/host-infra`** (5-й в группе, создан push-to-create):
   `watchdog/` + `backup/` (скрипты, юниты, README, `watchdog.env.example`);
   секреты и рантайм (`watchdog.env`, `state/`, `metrics.jsonl`,
   `backup.log`) в git не попадают и деплоем не перезаписываются.
2. **CI**: shellcheck → deploy на main. Runner-контейнер не монтирует
   `/home/admin/servers`, поэтому деплой — tar-поток через helper-контейнер
   alpine (`--user 1000`) с bind-mount. Юниты в `/etc/systemd/system` CI
   трогать не может (нет root) — drift-check с warning'ом
   «выполните sudo install.sh».
3. **`install.sh`** — одноразовая установка юнитов + enable таймеров (sudo).
4. **Чистка по shellcheck**: SC2038 в `backup_freshness`; удалены мёртвые
   пороги `WATCHDOG_TRIAGE_AGE_LIMIT`/`BLOCKED_LIMIT` — kanban-проверка
   watchdog'а чисто liveness, стейл/blocked ловят cron-sweeps в sandbox
   (см. 0.7 в основном логе).

## Попутная находка: регрессия gitlab-runner-shell

При первом деплое вскрылась цепочка из трёх проблем:

1. `gb10-shell` — **project-runner** (привязан к router/memgraphrag/
   bksamotsvety): новый проект надо привязывать вручную
   (`Ci::Runner#assign_to` через rails).
2. Привязка задним числом не тикает per-runner кэш очереди
   (`X-GitLab-Last-Update`) — pending-job висит; лечится
   `runner.pick_build!(build)`.
3. **Главное**: контейнер `gitlab-runner-shell` был пересоздан вручную
   2026-07-07 ~13:36 UTC без `--group-add <docker gid>` → user 1000 потерял
   доступ к docker.sock → **все docker-jobs на gb10-shell молча падали**
   (deploy memgraphrag/router). Jobs bksamotsvety проходили — они docker
   не трогают. Команда запуска контейнера нигде не была записана (snowflake).

Фикс: в `host-infra/gitlab-runner-shell/` — `docker-compose.yml`
(конфиг восстановлен из `docker inspect`, `group_add: ${DOCKER_GID:-988}`),
`.env.example` (gid не константа: `getent group docker`), реконструированный
`Dockerfile` (по `docker image history`; оригинал утерян), `build.sh`.
Контейнер пересоздан из compose 2026-07-07 — регистрация пережила
пересоздание (config.toml в bind-mount). Deploy job после этого прошёл,
e2e проверен: push → lint → deploy → файлы на хосте, рантайм не задет.

Ограничение навсегда: пересоздание runner'а — только вручную с хоста
(CI-job не может пересоздать контейнер, в котором выполняется).

## Хвосты (без изменений + новое)

| # | Что | Статус |
|---|-----|--------|
| 1 | `sudo /home/admin/servers/install.sh` — юниты + таймеры | ждёт sudo (drift-check CI напоминает) |
| 2 | `WATCHDOG_TELEGRAM_CHAT_ID` в watchdog.env | пуст |
| 3 | `BKS_TELEGRAM_EXPERIMENT_TOKEN` (@BotFather) | нет |
| 4 | Restore-тест бэкапа | не выполнен |
| 5 | Идея: 10-я проверка watchdog — `docker exec gitlab-runner-shell docker version` (ловит регрессию docker.sock: контейнер healthy, а jobs падают) | предложено |
