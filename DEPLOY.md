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
| Backup | свежесть 18 ч, но последний запуск: 1 ошибка; нет profiles и 5 kanban DB | **FAIL** |
| Watchdog | 9/9 проверок `OK`, метрики свежие | PASS; backup check проверяет только свежесть |
| Monitoring | Grafana `:3000`, Prometheus `:9090` слушают | targets/alerts не верифицированы |
| GitLab/Registry | `:8929`/`:5050` слушают | auth, runners и image digests не верифицированы |
| K3s | рабочим контуром не используется; API `:6443` не слушает | декомиссирован |

## Канонический контур управления

| Компонент | Единственный текущий способ управления |
|---|---|
| Router + LiteLLM + vLLM classifier/OCR/Whisper | Docker Compose, project `router` |
| MemGraphRAG + Qdrant | Docker Compose, project `memgraphrag` |
| Grafana + Prometheus + Loki + Promtail | Docker Compose, project `monitoring` |
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

Backup запускает `bks-backup.timer` ежедневно в 03:00. Полный набор за дату
`YYYYMMDD` содержит ровно 8 обязательных непустых артефактов:

1. `kanban-default-YYYYMMDD.db`
2. `kanban-production-YYYYMMDD.db`
3. `kanban-marketing-YYYYMMDD.db`
4. `kanban-research-YYYYMMDD.db`
5. `kanban-platform-YYYYMMDD.db`
6. `profiles-YYYYMMDD.tar.gz`
7. `memgraphrag-data-YYYYMMDD.tar.gz`
8. `qdrant-YYYYMMDD.tar.gz`

Контракт успеха: **все 8 файлов существуют и непусты, итоговая строка содержит
`Errors: 0`, затем набор проходит изолированный restore/integrity test**.
Свежесть `.last_backup` или одного архива сама по себе недостаточна.

Текущий `bks-backup.sh` снимает live tar каталога Qdrant без snapshot API и
без остановки контейнера. Такой файл нельзя считать консистентным только по
наличию и размеру: обязательна изолированная проверка открытия коллекций.
Целевой фикс — Qdrant snapshot API либо контролируемый backup при остановленном
контейнере.

Ручной запуск и проверка текущего набора:

```bash
START_EPOCH="$(date +%s)"
sudo systemctl start bks-backup.service
journalctl -u bks-backup.service -n 100 --no-pager

BACKUP_DIR=/home/admin/backups/bks
BACKUP_LOG=/home/admin/servers/backup/backup.log
DATE="$(date +%Y%m%d)"
[ "$(stat -c %Y "$BACKUP_LOG")" -ge "$START_EPOCH" ]
expected=(
  "kanban-default-${DATE}.db"
  "kanban-production-${DATE}.db"
  "kanban-marketing-${DATE}.db"
  "kanban-research-${DATE}.db"
  "kanban-platform-${DATE}.db"
  "profiles-${DATE}.tar.gz"
  "memgraphrag-data-${DATE}.tar.gz"
  "qdrant-${DATE}.tar.gz"
)
missing=0
for artifact in "${expected[@]}"; do
  if [ ! -s "${BACKUP_DIR}/${artifact}" ]; then
    printf 'MISSING_OR_EMPTY %s\n' "${BACKUP_DIR}/${artifact}" >&2
    missing=$((missing + 1))
  fi
done
summary="$(grep '=== BKS Backup done\.' \
  "$BACKUP_LOG" | tail -n 1)"
printf '%s\n' "$summary"
case "$summary" in
  *"Errors: 0."*) ;;
  *) printf 'BACKUP_ERRORS_NOT_ZERO\n' >&2; exit 1 ;;
esac
[ "$missing" -eq 0 ]
printf 'BACKUP_ARTIFACTS_OK 8/8\n'
```

На 2026-07-21 этот контракт не выполнен. Исторический isolated restore
2026-07-09 не подтверждает текущий набор. Не проводить fault injection и не
восстанавливать поверх production; restore выполняется только в отдельных
путях/портах по утверждённому тест-плану.

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

Текущая реализация watchdog проверяет только возраст новейшего backup-файла,
не 8/8 и не `Errors: 0`. Поэтому `backup_freshness=OK` нельзя использовать
как заключение о recoverability.

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
предусмотрен таймер `bks-compliance.timer` с `COMPLIANCE_GATE=off`: его работа
— снять метрику `bks_compliance_*`, а не уронить unit. Блокирует CI, где есть
кому чинить.

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
| Prometheus | `http://192.168.2.180:9090` | monitoring |
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
| `/home/admin/servers/backup/` | `bks-backup.sh`, units, `backup.log` |
| `/home/admin/backups/bks/` | backup artifacts и `.last_backup` |
| `/etc/systemd/system/bks-watchdog.{service,timer}` | host watchdog units |
| `/etc/systemd/system/bks-backup.{service,timer}` | host backup units |
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
