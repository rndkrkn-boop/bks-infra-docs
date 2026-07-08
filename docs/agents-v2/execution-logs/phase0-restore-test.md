# Phase 0.10 — restore-тест бэкапа (2026-07-09)

Закрывает хвост №4 из [phase0-followup-host-infra.md](phase0-followup-host-infra.md)
и пункт DoD 05 §8 «restore-тест пройден и записан в runbook».

## Методика (v1 — изолированное восстановление, не чистый sandbox)

Спека 05 §5 в максимуме требует восстановление «на чистом sandbox»;
для v1 выбран изолированный контур внутри существующей инфраструктуры —
проверяет читаемость и полноту бэкапа, не трогая прод. Полный тест
с пересозданием sandbox — вместе с ежеквартальным циклом (Phase 1+).

1. Свежий бэкап: `bks-backup.sh` вручную (таймер ещё не установлен) —
   `Errors: 0`, все 8 артефактов за 20260709.
2. Kanban: 5 баз скопированы в sandbox в `/tmp/restore-test/.hermes/`,
   `HERMES_HOME=/tmp/restore-test/.hermes hermes kanban boards`.
3. Профили: `profiles-*.tar.gz` распакован на хосте, проверено наличие
   всех 9 профилей.
4. MemGraphRAG: `episodes.db` из архива — `pragma integrity_check`.
5. Qdrant: временный контейнер `qdrant/qdrant:v1.9.7` на `:16333`
   с восстановленным storage, `GET /collections` + points_count.

## Результаты — ПРОЙДЕН

| Артефакт | Проверка | Результат |
|---|---|---|
| kanban-{default,production,marketing,research,platform} | integrity_check + `hermes kanban boards` из восстановленного HERMES_HOME | ok; 5 досок, default `done=1`, карточка `t_1898e3ce «TEST e2e pipeline check»` — состояние идентично живому |
| profiles-20260709.tar.gz | распаковка, 2252 файла | все 9 профилей (analytics, content, director-bot, experiment, market-monitor, mkt-bot, report-processor, research, structuring) |
| memgraphrag-data | integrity_check episodes.db + graph.graphml на месте | ok (данных мало — 1 эпизод, это известное состояние после утери K3s PVC) |
| qdrant | временный контейнер на восстановленном storage | все 6 коллекций поднялись; points: prod_chunk=1, prod_fact=3, prod_entity=5, mkt_*=0 — соответствует живому |

Проверено, что источник бэкапа MemGraphRAG верный: bind mount контейнера
`/home/admin/servers/memgraphrag/data → /data` совпадает с `MGRAG_DATA`
скрипта (маленький размер архива 4К — это реально мало данных,
а не бэкап пустого каталога).

## Runbook восстановления (проверенные команды)

```sh
B=/home/admin/backups/bks; D=<дата YYYYMMDD>
# 1. Kanban → sandbox (пути внутри sandbox: ~/.hermes/…)
docker cp $B/kanban-default-$D.db <sandbox>:/sandbox/.hermes/kanban.db
for b in production marketing research platform; do
  docker cp $B/kanban-$b-$D.db <sandbox>:/sandbox/.hermes/kanban/boards/$b/kanban.db
done
# 2. Профили (архив содержит sandbox/.hermes/profiles и cron)
tar xzf $B/profiles-$D.tar.gz -C / --strip-components=0  # внутри sandbox: tar xzf … -C /
# 3. MemGraphRAG + Qdrant (compose должен быть остановлен)
docker compose -f /home/admin/servers/memgraphrag/docker-compose.yml down
tar xzf $B/memgraphrag-data-$D.tar.gz -C /   # абсолютные пути в архиве
tar xzf $B/qdrant-$D.tar.gz -C /
docker compose -f /home/admin/servers/memgraphrag/docker-compose.yml up -d
curl -sf http://localhost:8010/health
```

Нюанс: архивы tar созданы с абсолютными путями (`/home/admin/servers/…`),
распаковка `-C /` кладёт файлы на место. Kanban-базы сняты через
`VACUUM INTO` — это консистентные снапшоты, journal-файлы не нужны.

## Хвосты Phase 0 после этого лога

| # | Что | Статус |
|---|-----|--------|
| 1 | `sudo /home/admin/servers/install.sh` — юниты + таймеры watchdog/backup | **всё ещё ждёт sudo**; без таймера бэкапы только ручные (последний до этого лога был 3 дня как несвежий) |
| 2 | `WATCHDOG_TELEGRAM_CHAT_ID` | пуст. Уточнение 2026-07-09: бот СОСТОИТ в ops-группе `bks_product` (`-1004371976422`) — это тот же бот, что у Grafana contact point (getUpdates был пуст лишь потому, что апдейты живут 24ч). Кандидат: вписать этот chat_id — watchdog и Grafana шлют в одну группу, роли разделены по §2.7 |
| 3 | `BKS_TELEGRAM_EXPERIMENT_TOKEN` | ✅ закрыт до 2026-07-09: gw-experiment работает под supervisord |
| 4 | Restore-тест | ✅ этот лог |
| 5 | 10-я проверка watchdog (`docker exec gitlab-runner-shell docker version`) | предложено, не реализовано |
| 6 | 48h-gate MemGraphRAG | перезапущен деплоем 2026-07-07 12:29 UTC (пересоздание контейнеров после фикса runner'а); истекает 2026-07-09 ~20:30 (+08) |
