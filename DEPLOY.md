# BKS NemoHermes — операционный playbook

Актуальный справочник эксплуатации контура на хосте `192.168.2.180`.
Состояние и ограничения зафиксированы на **2026-07-25** по
[`docs/audit/full-project-audit-2026-07-25.md`](./docs/audit/full-project-audit-2026-07-25.md)
(повтор [`2026-07-21`](./docs/audit/full-project-audit-2026-07-21.md))
и [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## NO-GO

**Безусловная приёмка production: NO-GO.** Все блокеры от 2026-07-21
(бэкап, gateway-контракт, restart recovery, Telegram E2E, offline model
smoke, fault injection, документация) закрыты живыми доказательствами
2026-07-25. Текущие блокеры другие:

1. `/opt/hermes/.venv` read-only внутри sandbox сломал обязательную
   установку пакета `mcp` для MemGraphRAG MCP-инструментов у всех
   9 профилей (обнаружено при попытке подключить Matrix-платформу,
   не чинится живой policy — нужен новый sandbox rebuild с mcp/mautrix,
   запечёнными в образ);
2. GitLab pipeline/runner/image provenance по-прежнему не подтверждён
   (read-only токен не был реально предоставлен).

До закрытия этих условий MCP-память нельзя считать рабочей ни для одного
профиля, а CI/CD provenance — недоказанным.

## Текущий статус

Наблюдения аудита 2026-07-21 13:17–13:21 UTC:

| Область | Текущее состояние | Операционная оценка |
|---|---|---|
| Router `:4000` | watchdog `OK`, listener есть; 64/64 unit tests | PASS с ограниченными live-доказательствами |
| LiteLLM `127.0.0.1:4001` | listener есть | HTTP не верифицирован |
| MemGraphRAG `:8010` | watchdog `OK`, listener есть; 56/56 tests | PASS с ограниченными live-доказательствами |
| Docker Compose | watchdog не видит restarting/unhealthy | PASS с ограниченными доказательствами |
| OpenShell sandbox `bks-production` | ready | PASS |
| Telegram gateways | контракт 3, live-наблюдение 2 supervised programs | **FAIL** |
| Kanban | liveness `OK`, очереди пусты | E2E/реальное использование не доказаны |
| Backup | v2: версионированные снапшоты, GFS-ретеншен, машиночитаемый контракт полноты, автоматический restore-drill | PASS (2026-08-04, карточка #8); v1 был **FAIL** — 1 ошибка, нет profiles и 5 kanban DB |
| Watchdog | 9/9 проверок `OK`, метрики свежие | PASS; backup check проверяет только свежесть |
| Monitoring | Grafana `:3000`, Prometheus `:9090` слушают | targets/alerts не верифицированы |
| GitLab/Registry | `:8929`/`:5050` слушают | auth, runners и image digests не верифицированы |
| K3s | рабочим контуром не используется; API `:6443` не слушает | декомиссирован |

## Канонический контур управления

| Компонент | Единственный текущий способ управления |
|---|---|
| Router + LiteLLM + vLLM classifier/OCR/Whisper | Docker Compose, project `router` |
| MemGraphRAG + Qdrant | Docker Compose, project `memgraphrag` |
| Grafana + Prometheus + Loki + Alloy | Docker Compose, project `monitoring` |
| Sandbox, профили, политики, gateways | NemoClaw/`nemohermes` + OpenShell |
| Watchdog и backup | host systemd services/timers |

**K3s и `kubectl` не применяются ни к одному текущему рабочему контуру.**
Исторические сведения вынесены в приложение «НЕ ВЫПОЛНЯТЬ».

## Быстрая проверка состояния

Команды ниже не выводят секреты и не меняют состояние:

```bash
curl -fsS http://127.0.0.1:4000/health
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:3000/api/health
curl -fsS http://127.0.0.1:9090/-/healthy

docker compose -p router -f /home/admin/ci/router/docker-compose.yml ps
docker compose -p memgraphrag -f /home/admin/ci/memgraphrag/docker-compose.yml ps
docker compose -p monitoring -f /home/admin/ci/monitoring/docker-compose.yml ps

nemohermes sandbox status bks-production
nemohermes sandbox exec bks-production -- bash -c \
  'export PATH="$PATH:/sandbox/.local/bin"; supervisorctl -c /tmp/supervisord.conf status'

systemctl --no-pager status bks-watchdog.timer bks-backup.timer
systemctl list-timers --no-pager bks-watchdog.timer bks-backup.timer
journalctl -u bks-watchdog.service -n 30 --no-pager
```

Ожидание для supervisor: три программы `gw-director-bot`,
`gw-mkt-bot`, `gw-experiment` в состоянии `RUNNING`. На момент аудита было
видно только две программы; сначала определить отсутствующую по выводу, не
подменять это предположением.

## Docker Compose

Штатный деплой выполняет GitLab CI с masked/protected variables. Ручное
обновление — аварийная операция оператора; оно допустимо только из CI checkout,
где уже существует `0600` `.env`. Содержимое `.env` не печатать.

### Router

Проверка и журналы:

```bash
cd /home/admin/ci/router
docker compose -p router config -q
docker compose -p router ps
docker compose -p router logs --tail=100 router
curl -fsS http://127.0.0.1:4000/health
```

Ручное применение образов/конфигурации:

```bash
cd /home/admin/ci/router
test -s .env
docker compose -p router config -q
docker compose -p router pull
docker compose -p router up -d --remove-orphans
curl --fail --silent --show-error --retry 30 --retry-delay 2 \
  http://127.0.0.1:4000/health
```

Не использовать `docker compose build` как production-деплой: CI публикует
router image в registry. Build-only сервисы Compose может пропустить при
`pull`; это ожидаемо, если их локальные образы уже существуют.

### MemGraphRAG + Qdrant

Qdrant доступен только внутри compose-сети. Не публиковать его порт на хост.

```bash
cd /home/admin/ci/memgraphrag
test -s .env
docker compose -p memgraphrag config -q
docker compose -p memgraphrag pull memgraphrag
docker compose -p memgraphrag up -d --no-deps memgraphrag
curl --fail --silent --show-error --retry 45 --retry-delay 2 \
  http://127.0.0.1:8010/health
docker compose -p memgraphrag ps
```

Для полного восстановления стека после остановки хоста:

```bash
cd /home/admin/ci/memgraphrag
test -s .env
docker compose -p memgraphrag config -q
docker compose -p memgraphrag up -d
curl --fail --silent --show-error --retry 45 --retry-delay 2 \
  http://127.0.0.1:8010/health
```

### Monitoring

Router должен быть поднят первым: monitoring использует external network
`router_default` и volume `router_router_logs`.

```bash
cd /home/admin/ci/monitoring
test -s .env
docker compose -p monitoring config -q
docker compose -p monitoring pull
docker compose -p monitoring up -d --remove-orphans
docker compose -p monitoring restart grafana
curl --fail --silent --show-error --retry 30 --retry-delay 2 \
  http://127.0.0.1:3000/api/health
curl --fail --silent --show-error --retry 30 --retry-delay 2 \
  http://127.0.0.1:9090/-/healthy
```

После обновления отдельно проверить targets и доставку alert'ов: аудит
подтвердил listeners, но не подтвердил Prometheus targets, Loki stream,
dead-man и Telegram delivery.

#### Однократно: переход Promtail → Alloy (2026-08-04)

Сбор логов переведён с Promtail на Grafana Alloy, Loki поднят до 3.7.
Штатный `up -d --remove-orphans` выше сам снимает контейнер
`monitoring-promtail` (его больше нет в compose) — отдельных действий не
нужно, но есть три операционных нюанса:

1. **Том `promtail_positions` не удалять.** Alloy импортирует
   `/tmp/positions.yaml` как legacy-файл при первом старте. Без него он
   перечитает все три JSONL с нуля и продублирует в Loki то, что там уже
   лежит.
2. **`docker exec monitoring-loki ...` больше не работает:** образ
   loki 3.7 — distroless, в нём нет ни shell, ни wget. Пробник изнутри
   docker-сети — `monitoring-grafana` (в той же сети, с `curl`).
3. **Первые записи ждать до 5 минут:** watchdog пишет `metrics.jsonl` раз
   в 5 минут, до его прогона счётчик доставки будет стоять.

Проверка после обновления:

```bash
# alloy поднялся и готов
docker exec monitoring-grafana curl -sf http://alloy:12345/-/ready

# логи реально доезжают (счётчик растёт только при успешном push в Loki);
# dropped_entries должен быть 0
docker exec monitoring-grafana curl -s http://alloy:12345/metrics \
  | grep -E '^loki_write_(sent|dropped)_entries_total'

# отсеянный мусор виден отдельно и не молча
docker exec monitoring-grafana curl -s http://alloy:12345/metrics \
  | grep '^loki_process_dropped_lines_total'

# стримы на месте (на эти job-лейблы завязаны панели дашбордов)
docker exec monitoring-grafana curl -s \
  'http://loki:3100/loki/api/v1/label/job/values'
```

Новый фильтрующий слой (structured metadata) проверяется запросами в
Grafana Explore — они не требуют парсера и должны возвращать строки:

```
{job="bks-watchdog"} | status="fail"
{job="security-audit"} | level="error"
{job="security-audit"} | category="pii"
```

**Откат.** Вернуть сервис `promtail` и `loki:3.4.2` из git-истории
`monitoring/docker-compose.yml`, затем тот же `up -d --remove-orphans`.
Позиции сохранены в неизменном томе, поэтому откат тоже не даёт дублей.

### Конвейер метрик (агрегация, textfile, федерация)

Разворачивается в три шага, и порядок важен: экспортёр без производителей даст
пустые метрики, производители без экспортёра — файлы, которые никто не читает.
Контракты и обоснования — [`docs/metrics/README.md`](./docs/metrics/README.md).

**Шаг 1. Каталог экспозиции (один раз, sudo).** Владелец — хост: в него пишут
systemd-таймеры, node-exporter только читает.

```bash
sudo install -d -o admin -g admin -m 0755 /var/lib/node_exporter/textfile
```

**Шаг 2. Сбор (деплой bks/monitoring).** Штатный `up -d --remove-orphans`
поднимает node-exporter и подключает правила записи. Федерация — отдельным
профилем, она опциональна:

```bash
cd /home/admin/ci/monitoring
docker compose -p monitoring up -d --remove-orphans
docker compose -p monitoring restart grafana   # alerting-конфиги копирует entrypoint

# второй этаж (retention 365d, только свёртки bks:*) — по желанию
docker compose -p monitoring --profile federation up -d
```

**Шаг 3. Производители (юниты, только вручную).** CI не имеет права править
`/etc/systemd/system`, поэтому как и остальные `bks-*`:

```bash
sudo install -m 0644 /home/admin/projects/nemohermes_bks/metrics/systemd/*.service \
  /home/admin/projects/nemohermes_bks/metrics/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bks-metrics-bridge.timer bks-compliance.timer
systemctl list-timers --no-pager bks-metrics-bridge.timer bks-compliance.timer
```

`bks-compliance.timer` до этого существовал только на бумаге: раздел
«Комплаенс-аудит» ниже описывал его как предусмотренный, но файла юнита не было
ни в репозитории, ни на хосте, и метрики `bks_compliance_*` никуда не попадали.

Проверка после деплоя:

```bash
# 1. Правила записи ЗАГРУЖЕНЫ (валидный конфиг этого не гарантирует:
#    rule_files можно смонтировать не туда, и Prometheus стартует молча)
curl -s http://127.0.0.1:9090/api/v1/rules | grep -c '"name":"bks:'

# 2. Правила ДАЛИ серии (загружено != вычислено)
curl -s -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=count(count by (__name__) ({__name__=~"bks:.*"}))'

# 3. Textfile-путь жив: цель node-exporter скрейпится, экспозиция разбирается
curl -s -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=up{job="node-exporter"}'
curl -s -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=bks:textfile_errors:max'    # обязан быть 0

# 4. Производители пакетных метрик на месте (ожидается 2: compliance, metrics_bridge)
curl -s -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=bks:batch_job_age_seconds'

# 5. Федерация (только при включённом профиле): не 0 сэмплов
curl -s -G http://127.0.0.1:9091/api/v1/query \
  --data-urlencode 'query=bks:federation_samples:count'

# 6. honor_labels работает: у федерированной серии job СОБСТВЕННЫЙ,
#    а не bks-federate, и нет лейбла exported_job
curl -s -G http://127.0.0.1:9091/api/v1/query \
  --data-urlencode 'query=bks:up:by_job'
```

Ручной прогон моста (для отладки, ничего не пишет с `--stdout`):

```bash
python3 scripts/metrics-bridge.py --stdout --source watchdog
METRICS_OUT_DIR=/tmp/tf bash ci/metrics-bridge.sh   # полный цикл в песочницу
```

**Что считать нормой сразу после деплоя, а не поломкой:**

- `bks_metrics_bridge_source_ok{source="backup"}` = 0, пока не выполнена
  однократная миграция хранилища бэкапов в раскладку v2 (`backup-manager status`
  до неё возвращает rc=2 — см. «Деплой v2» выше);
- панели роутера пусты, пока через `router:4000` не пройдёт трафик:
  prometheus_client создаёт дочерние серии счётчика при первом использовании;
- свёртки `bks:*:increase1d` и `:ratio1d` показывают частичные окна первые сутки.

**Откат.** Убрать `node-exporter` и `prometheus-global` из compose (или просто
не включать профиль), закомментировать `rule_files` в `prometheus/prometheus.yml`
и остановить таймеры: `sudo systemctl disable --now bks-metrics-bridge.timer`.
Данные первого этажа при этом не теряются — правила записи ничего не удаляют,
они только добавляют серии.

## Sandbox и gateways

Первичный sandbox создаётся вручную через NemoClaw/OpenShell. Регулярный sync
выполняет GitLab job `bks/bksamotsvety:sync`: policy update, profiles sync и
`start-gateways.sh`.

Безопасная ручная повторная синхронизация:

```bash
cd /home/admin/ci/bksamotsvety
test -s deploy/.env
set -a
source deploy/.env
set +a
bash deploy/check-sandbox.sh
bash deploy/update-policies.sh
bash deploy/sync-profiles.sh
bash deploy/start-gateways.sh
```

### Ограничение после рестарта sandbox

`restart: unless-stopped` относится к host Compose, но не запускает
supervisord внутри пересозданного OpenShell sandbox. Sandbox entrypoint
поднимает базовый Hermes gateway; три per-profile gateways автоматически не
восстанавливаются. Пока startup hook не реализован и не протестирован,
после каждого restart/recreate sandbox оператор обязан вручную выполнить
start-gate:

```bash
cd /home/admin/ci/bksamotsvety
test -s deploy/.env
set -a
source deploy/.env
set +a
bash deploy/start-gateways.sh
nemohermes sandbox exec "${NEMOCLAW_SANDBOX_NAME:-bks-production}" -- bash -c \
  'export PATH="$PATH:/sandbox/.local/bin"; supervisorctl -c /tmp/supervisord.conf status'
```

Успех — ровно три `RUNNING`: director, marketing, experiment. Отдельный
experiment token должен существовать в canonical CI variables; его значение
не выводить. Отсутствие токена переводит experiment в `autostart=false` и
нарушает контракт.

С 2026-07-22 watchdog обнаруживает `telegram paused`, проверяет Telegram
egress без реального bot token и перезапускает обязательные
`gw-director-bot`/`gw-mkt-bot` через уже работающий supervisord. Перед restart
старые supervisor-логи очищаются, чтобы stale marker не создавал restart loop.
Полный bootstrap после recreate sandbox требует заполненного host-side
`deploy/.env`; на текущем хосте Telegram credentials там отсутствуют, поэтому
autorecovery после recreate всё ещё не считается доказанным.

Production-хост использует DNS `192.168.2.1`, затем `1.1.1.1`. Уже работающий
sandbox может сохранить старый `resolv.conf`; до следующего recreate его нужно
проверять отдельно. DNS-ответ не доказывает доступность Telegram: наблюдались
TLS handshake timeout, поэтому watchdog имеет отдельную проверку
`telegram_egress`. LAN, ICMP, raw TCP, NIC и host conntrack при этом исправны;
дефект локализован до прозрачного XKeen/Xray-маршрута на Keenetic. Restart
XKeen не исправил выборку (около 45% успешных HTTPS-запросов). До ремонта
outbound/VPS или успешного direct-route control test Telegram нельзя считать
стабильным.

## Router quality gate

Канонический gate — **ручной GitLab CI job `quality-gate`** на `gb10-shell`;
его запускают перед merge/release для изменений `classifier.py`,
`litellm_config.base.yaml`, `docker-compose.yml`, `Dockerfile` или
`supervisord.conf`. Обычный pipeline не запускает этот платный batch
автоматически.

Исторический локальный pre-push hook удалён. Он не является частью текущего
контроля и не должен считаться доказательством production gate.

Локальный эквивалент запускается только через eval sandbox:

```bash
cd /home/admin/projects/nemohermes_bks/router/eval/sandbox
./run.sh /sandbox/.venv/bin/python3 gate.py
```

Интерпретация:

- `GATE: OK`, exit `0` — сравнение выполнено, регрессии не найдено;
- `GATE: FAIL`, exit `1` — деплой блокировать;
- `GATE: SKIP`, exit `0` — инфраструктурная ошибка/недоступен
  `claude-cli`; **это не PASS**. Production change требует повторного
  успешного запуска или явного документированного risk waiver.

`--update-baseline` не является штатной проверкой. Его применять только после
review и одобрения намеренного изменения baseline.

## Backup: контракт полноты

Backup запускает `bks-backup.timer` ежедневно в 03:00. Единица хранения —
иммутабельный снапшот `/home/admin/backups/bks/snapshots/<YYYYMMDDTHHMMSSZ>/`
(раскладка и обоснование — `host-infra/backup/README.md`).

Полный снапшот содержит ровно 8 обязательных артефактов, перечисленных в
`backup/retention.toml` (секции `[[artifacts]]`):

1. `kanban-default.db`
2. `kanban-production.db`
3. `kanban-marketing.db`
4. `kanban-research.db`
5. `kanban-platform.db`
6. `profiles.tar.gz`
7. `memgraphrag-data.tar.gz`
8. `qdrant.tar.gz`

Контракт успеха: **`manifest.json` снапшота содержит `status: "complete"` (то
есть 8/8 обязательных артефактов не меньше `min_bytes` и `errors: 0`), снапшот
проходит `verify`, и он же проходит изолированный `restore-drill`.** Свежесть
`.last_backup` или отдельного архива по-прежнему недостаточна.

Контракт теперь машиночитаемый, а не только текстовый — состав проверяет
`scripts/backup-manager.py`, и объявить набор полным скрипт бэкапа не может:

```bash
python3 /home/admin/servers/backup/backup-manager.py \
    --policy /home/admin/servers/backup/retention.toml \
    status --root /home/admin/backups/bks
```

`status` отвечает не на вопрос «бэкап свежий», а на вопрос «можно ли из этого
восстановиться»: полнота новейшего снапшота, его возраст и давность последнего
restore-drill. Код возврата `1` означает содержательную проблему.

### Деплой v2

Файлы приходят из двух репозиториев: `bks/host-infra` (скрипт, юниты) и
`nemohermes_bks` (движок, политика). Собираются в один каталог:

```bash
# из nemohermes_bks
install -m 755 scripts/backup-manager.py /home/admin/servers/backup/
install -m 644 backup/retention.toml     /home/admin/servers/backup/

# из host-infra (или автоматически из CI по путям /home/admin/servers/)
install -m 755 backup/bks-backup.sh            /home/admin/servers/backup/
install -m 644 backup/bks-backup-drill.service /home/admin/servers/backup/
install -m 644 backup/bks-backup-drill.timer   /home/admin/servers/backup/

# юниты (drill-таймер новый, поэтому install.sh нужен повторно)
sudo /home/admin/projects/nemohermes_bks/host-infra/install.sh
```

Затем однократная миграция существующего хранилища v1 в версионированную
раскладку — сначала планом, потом применением:

```bash
python3 /home/admin/servers/backup/backup-manager.py \
    --policy /home/admin/servers/backup/retention.toml \
    migrate --root /home/admin/backups/bks            # dry-run

python3 /home/admin/servers/backup/backup-manager.py \
    --policy /home/admin/servers/backup/retention.toml \
    migrate --root /home/admin/backups/bks --apply
```

Миграция переносит (не копирует) плоские файлы: два источника истины об одном и
том же дне — та самая неоднозначность, из-за которой набор v1 нельзя было
проверить. Дата берётся из имени файла, а не из `mtime`, потому что `mtime`
показывает момент последней перезаписи.

Ожидаемый результат для наборов, снятых до пересоздания sandbox: `complete` 8/8.
Для наборов 2026-08-04 и позже — `partial`, пока kanban DB не находятся внутри
sandbox (причина не в бэкапе, см. docs/audit/backup-versioning-audit-2026-08-04.md).

Ручной прогон и проверка:

```bash
START_EPOCH="$(date +%s)"
sudo systemctl start bks-backup.service
journalctl -u bks-backup.service -n 100 --no-pager

BACKUP_DIR=/home/admin/backups/bks
MANAGER=/home/admin/servers/backup/backup-manager.py
POLICY=/home/admin/servers/backup/retention.toml

# 1. Прогон вообще состоялся (лог тронут после старта)
[ "$(stat -c %Y /home/admin/servers/backup/backup.log)" -ge "$START_EPOCH" ]

# 2. Новейший снапшот полон: 8/8 и errors=0 (rc=1 при неполном)
python3 "$MANAGER" --policy "$POLICY" verify --root "$BACKUP_DIR"

# 3. Из него реально восстанавливается (изолированный каталог, не production)
python3 "$MANAGER" --policy "$POLICY" restore-drill --root "$BACKUP_DIR" --record

# 4. Итоговая пригодность хранилища
python3 "$MANAGER" --policy "$POLICY" status --root "$BACKUP_DIR"
```

`bks-backup.service` завершается с кодом 1 при неполном снапшоте, поэтому
`systemctl status` показывает failed в тот же день. В v1 такой прогон
отчитывался успехом: `SKIP: board not yet created` не считался ошибкой, и
2026-08-04 бэкап прошёл при нуле из пяти kanban DB.

Ротация выполняется `backup-manager.py retention --apply` по политике GFS
(7 дневных / 4 недельных / 6 месячных / 2 годовых слота, `min_keep = 3`).
Возраст берётся из `snapshot_id`, а не из `mtime`: перезапись файла
«омолаживала» его и ротация v1 считала возраст неверно. Слот занимает только
`status: complete`, поэтому битый прогон не вытесняет хороший снапшот того же
дня. Проверить план без удаления:

```bash
python3 "$MANAGER" --policy "$POLICY" retention --root "$BACKUP_DIR"   # dry-run
```

Restore-drill запускается автоматически `bks-backup-drill.timer` (понедельник
04:30) и пишет отчёты в `/home/admin/backups/bks/drills/`. Drill восстанавливает
в изолированный каталог и отказывается писать в production-пути
(`retention.toml`, `drill.forbidden_targets`) — restore поверх production
остаётся ручной процедурой по утверждённому плану.

Известное ограничение сохраняется: `qdrant.tar.gz` — это
live tar каталога Qdrant, снятый без snapshot API и без остановки контейнера.
`verify` и `restore-drill` доказывают, что архив распаковывается, но не
консистентность коллекций. Целевой фикс — snapshot API либо backup при
остановленном контейнере.

Проведённые проверки (2026-08-04, карточка architecture-improvements #8):
миграция реального набора 2026-08-03 в версионированную раскладку дала
`status: complete` 8/8; `verify` — OK; `restore-drill` на копии восстановил все
8 артефактов (`integrity_check: ok` на пяти БД, 109 задач в production-доске,
2534 файла профилей, 1925 файлов Qdrant). Прод-хранилище при этом не
изменялось: проверка шла на копии в `/tmp`.

## Watchdog и systemd

Watchdog проверяет router, MemGraphRAG, Docker containers, sandbox,
Telegram egress, supervisord/gateway runtime, kanban liveness, backup
freshness, disk и GPU каждые 5 минут.
Stale/blocked kanban checks принадлежат cron jobs внутри sandbox.

```bash
sudo systemctl start bks-watchdog.service
journalctl -u bks-watchdog.service -n 50 --no-pager
tail -n 20 /home/admin/servers/watchdog/metrics.jsonl
systemctl list-timers --no-pager bks-watchdog.timer bks-backup.timer
```

`backup_freshness` проверяет только возраст новейшего backup-файла, поэтому
использовать его как заключение о recoverability по-прежнему нельзя. Для этого
есть отдельная проверка `backup_recoverability`: она спрашивает
`backup-manager.py status` о полноте новейшего снапшота (8/8) и о давности
restore-drill. Семантика `backup_freshness` намеренно не менялась — на неё
ссылаются дашборды и история метрик в Loki.

## Комплаенс-аудит

Проверка регуляторных и внутренних требований по каталогу
`compliance/rules.toml` (14 правил; контракт — `docs/compliance/README.md`).

```bash
# локально: только вердикт
python3 scripts/compliance-audit.py

# полный набор артефактов + метрики для Prometheus
COMPLIANCE_OUT_DIR=/tmp/compliance \
COMPLIANCE_METRICS_DIR=/var/lib/node_exporter/textfile \
  bash ci/compliance-report.sh
```

Коды возврата: `0` — чисто, `1` — нарушение severity ≥ `high`, `2` — сломан
сам инструмент. `2` нельзя трактовать как «всё хорошо»: отчёта в этом случае
нет вообще.

В CI это джобы `compliance-audit` и `compliance-tests` (stage `compliance`),
артефакты — `report.json`, `report.md`, `dashboard.html` на 90 дней. На хосте
таймер `bks-compliance.timer` с `COMPLIANCE_GATE=off`: его работа — снять
метрику `bks_compliance_*`, а не уронить unit. Блокирует CI, где есть кому
чинить. Юнит лежит в `metrics/systemd/` и ставится вручную (см. «Конвейер
метрик»); до 2026-08-05 он существовал только в этом абзаце, а метрики писались
в каталог, который никто не читал.

Grafana-дашборд импортируется из `compliance/grafana-dashboard.json`. Панель
«Возраст последнего прогона» важнее остальных: автоматизированный комплаенс
отказывает не громко (правило упало), а тихо — задача перестала запускаться,
а дашборд продолжает показывать последний зелёный результат.

## Endpoints

| Сервис | Текущий endpoint | Доступ/примечание |
|---|---|---|
| GitLab web | `http://192.168.2.180:8929` | требуется аутентификация |
| GitLab SSH | `ssh://192.168.2.180:2222` | git SSH |
| Container Registry | `192.168.2.180:5050` | внутренний HTTP registry |
| Router API | `http://192.168.2.180:4000` | health `/health`, OpenAI API `/v1`; sandbox: `http://host.openshell.internal:4000/v1` |
| LiteLLM direct | `http://127.0.0.1:4001` | только loopback; обход classifier, не публиковать |
| MemGraphRAG | `http://192.168.2.180:8010` | sandbox: `http://host.openshell.internal:8010` |
| Qdrant | `http://qdrant:6333` | только сеть compose `memgraphrag`, host port отсутствует |
| Grafana | `http://192.168.2.180:3000` | monitoring |
| Prometheus | `http://192.168.2.180:9090` | monitoring, сырьё, retention 30d |
| Prometheus (federated) | `http://192.168.2.180:9091` | только при профиле `federation`: свёртки `bks:*`, retention 365d |
| vLLM Qwen3.6 test contour | `http://192.168.2.180:8088/v1` | не основной router tier |
| Speech-to-text | Router `:4000`, model `whisper` | LiteLLM → `vllm-whisper`; прямой `:10301` декомиссирован |

Не включать в документацию токены, API keys, `.env` значения, chat IDs или
GitLab variable values.

## Ключевые пути

| Путь | Назначение |
|---|---|
| `/home/admin/ci/router/` | CI production checkout и Compose project `router` |
| `/home/admin/ci/memgraphrag/` | CI production checkout и Compose project `memgraphrag` |
| `/home/admin/ci/monitoring/` | CI production checkout и Compose project `monitoring` |
| `/home/admin/ci/bksamotsvety/` | CI checkout для policy/profile/gateway sync |
| `/home/admin/projects/nemohermes_bks/{router,MemGraphRAG,monitoring,bksamotsvety}/` | рабочие source repositories; не считать автоматически runtime checkout |
| `/home/admin/servers/memgraphrag/data/` | MemGraphRAG persistent data |
| `/home/admin/servers/memgraphrag/qdrant/` | Qdrant persistent storage |
| `/home/admin/servers/watchdog/` | `check.sh`, env, state, `metrics.jsonl` |
| `/home/admin/servers/backup/` | `bks-backup.sh`, `backup-manager.py`, `retention.toml`, units, `backup.log` |
| `/home/admin/backups/bks/` | `snapshots/<id>/` с `manifest.json`, `latest`, `drills/`, `.last_backup`, `.last_drill` |
| `/etc/systemd/system/bks-watchdog.{service,timer}` | host watchdog units |
| `/etc/systemd/system/bks-backup.{service,timer}` | host backup units |
| `/etc/systemd/system/bks-backup-drill.{service,timer}` | еженедельный restore-drill |
| `/etc/systemd/system/bks-metrics-bridge.{service,timer}` | мост метрик, цикл 5 мин |
| `/etc/systemd/system/bks-compliance.{service,timer}` | суточный комплаенс-аудит (метрики, без гейта) |
| `/var/lib/node_exporter/textfile/` | `.prom`-экспозиция задач по таймеру; пишет хост, node-exporter читает |
| `/home/admin/models/adapters/` | router LoRA adapters, read-only mount |
| `/tmp/supervisord.conf` внутри sandbox | runtime supervisor config; теряется при recreate |
| `/tmp/supervisor/` внутри sandbox | gateway/supervisor logs |

Secrets хранятся в masked/protected GitLab CI/CD Variables и локальных
gitignored `.env` с режимом `0600`. Проверять наличие/права, не содержимое.

## Историческое приложение — K3s: НЕ ВЫПОЛНЯТЬ

<details>
<summary><strong>НЕ ВЫПОЛНЯТЬ: декомиссированный K3s-контур (до 2026-07-06)</strong></summary>

Этот материал сохранён только для расследования старых инцидентов. Он не
является runbook и не должен копироваться в shell.

- K3s v1.32.5 использовался на однонодовом GB10.
- NVIDIA device plugin не поддерживал unified-memory GB10; применялся
  privileged pod с hostPath `/dev/nvidia*`.
- Router был удалён из K3s 2026-07-02.
- vLLM classifier был удалён 2026-07-03; прежний pod не имел Service.
- MemGraphRAG и Qdrant перенесены в Docker Compose 2026-07-06, namespace
  `memgraphrag` и device-plugin DaemonSet удалены.
- Старые файлы `router/deploy/*.yaml` и
  `MemGraphRAG/deploy/*-k3s.yaml` — deprecated documentation.

Исторически выполнялись операции установки K3s, настройки kubeconfig,
`kubectl apply/patch/rollout/logs`, создания Kubernetes Secrets и импорта
vLLM image в containerd. **Все эти операции запрещены для текущего
production-контура.** Не создавать namespaces `bks-router`/`memgraphrag`,
не патчить Kubernetes Secrets и не запускать MemGraphRAG/router через K3s.

</details>
