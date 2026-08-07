# Надёжность и масштабирование

> **Актуальный статус на 2026-07-21:** monitoring уже развёрнут, но Gate 0
> **NOT MET / NO-GO**. Текущий backup неполон, из 3 gateway-контракта
> наблюдались 2, sandbox autorecovery не доказан. См.
> [полный аудит](../audit/full-project-audit-2026-07-21.md).

Урок аудита: система умирала не из-за сложных багов, а из-за отсутствия
трёх скучных вещей — supervision, watchdog, бэкапов. Этот документ — их
спецификация плюс план масштабирования.

## 1. Инвентарь долгоживущих процессов (что именно должно жить)

| Процесс | Где живёт | Кто следит (целевое) |
|---|---|---|
| OpenShell / sandbox `bks-production` | docker на хосте | docker restart policy + watchdog |
| gateway director-bot / mkt-bot / experiment (контракт 3; текущий факт 2 RUNNING) | внутри sandbox | supervisord внутри sandbox |
| kanban-диспетчер (встроен в gateway, 03 §3.1) | внутри sandbox | supervision gateway = supervision диспетчера |
| cron-джобы Hermes (sweeps, monitor, digest) | внутри sandbox (hermes cron) | сам Hermes; декларация и проверка наличия — `sync-profiles.sh`/acceptance |
| router (classifier, litellm, vllm-classifier, whisper, ocr) | docker-compose хоста | `restart: unless-stopped` + healthchecks compose + watchdog |
| memgraphrag + qdrant | docker-compose хоста (K3s декомиссирован 2026-07-06) | `restart: unless-stopped` + healthcheck compose + watchdog |
| GitLab + runners | docker-compose хоста | restart policy + watchdog (не критично для конвейера) |

## 2. Supervision внутри sandbox

Заменить `nohup ... &` из start-gateways.sh на supervisord:

- `deploy/supervisord.conf.tpl` в git; `sync-profiles.sh` рендерит без
  значений секретов. Канонический источник — protected GitLab Variables;
  допустимая реализация — host-side runtime env, файл 0600 (предпочтительно
  tmpfs) или OpenShell provider. Значения не коммитятся, не логируются и
  не бэкапируются; file/process permissions входят в acceptance;
- программы: `gw-director-bot`, `gw-mkt-bot`, `gw-experiment`,
  без отдельного `kanban-daemon`: dispatch встроен в gateway. У gateway
  `autorestart=true`, `startretries=5`,
  экспоненциальный backoff, логи в `/tmp/supervisor/*.log`;
- `start-gateways.sh` вырождается в `supervisorctl reread/update/restart` —
  идемпотентно, без pkill-гонок;
- известная грабля stale `gateway.lock` — в pre-start скрипт программы:
  удалить lock, если pid из него мёртв.

## 3. Watchdog (главный новый компонент, при этом тривиальный)

**Скрипт на хосте** (`/home/admin/servers/watchdog/check.sh` + systemd
timer каждые 5 мин), вне sandbox и вне docker — чтобы видеть смерть их самих.
Никаких LLM внутри: чистый bash/python + Telegram Bot API sendMessage.

Проверки (каждая = строка в отчёте, отказ = алерт):

1. `curl -fsS localhost:4000/health` — роутер;
2. `curl -fsS localhost:8010/health` — memgraphrag (docker-compose);
3. `docker ps` — все compose-контейнеры (router, memgraphrag, qdrant,
   gitlab) healthy/Up, нет Restarting-петель дольше 10 мин;
4. `nemohermes sandbox status bks-production` — sandbox Ready;
5. `exec`: supervisord запущен и все программы RUNNING;
6. `exec`: kanban liveness и доступность stats; stale/blocked policy
   принадлежит cron-джобам `stale-triage-sweep` и `blocked-digest`;
7. свежесть последнего успешного бэкапа < 26ч;
8. диск хоста < 85%, VRAM/GPU доступен (nvidia-smi).

Политика алертов: немедленный алерт при переходе OK→FAIL и при
восстановлении (FAIL→OK); повтор раз в 4ч, пока не починено (не заспамить);
ежедневная сводка «всё зелёное» одной строкой в 09:00 — тишина не должна
быть неотличима от смерти watchdog'а. Канал: отдельный Telegram-топик/чат
`bks-alerts`.

Watchdog-скрипт сам под systemd timer → его отказ виден по отсутствию
ежедневной сводки (и это единственное, за чем следит человек глазами).

## 4. Метрики и monitoring (развёрнуты, требуют верификации)

Watchdog складывает каждую проверку строкой JSON в
`/home/admin/servers/watchdog/metrics.jsonl` + kanban-метрики из 03 §6.
Prometheus, Grafana, Loki и Promtail уже развёрнуты. Наличие listener'ов
не доказывает корректность targets, alert rules, dead-man и доставки:
эти элементы остаются обязательной проверкой перед Go.

## 5. Бэкапы и восстановление

Полный ежедневный set состоит ровно из **10 артефактов**: 5 kanban DB,
1 архив профилей, 1 архив MemGraphRAG, 1 архив Qdrant, 1 архив Matrix
Synapse и 1 дамп Matrix Postgres (добавлены 2026-08-07). Backup считается
успешным только при наличии и integrity-check всех десяти; одна freshness
метка недостаточна.

| Что | Как | Куда | Частота |
|---|---|---|---|
| kanban.db | `sqlite3 VACUUM INTO` через `nemohermes sandbox exec`, затем поток на хост | `/home/admin/backups/bks/` | ежедневно |
| ~/.hermes профилей (MEMORY.md, templates, skills, cron) | tar внутри sandbox, затем поток на хост | там же | ежедневно |
| MemGraphRAG data (episodes SQLite + igraph) | tar bind-mount каталога хоста (после Phase 0.1 это обычный каталог, не PVC) | там же | ежедневно |
| Qdrant | **текущая реализация:** live tar storage-каталога; консистентность не гарантирована, целевой фикс — snapshot API или tar остановленного контейнера | там же | ежедневно |
| deploy/.env, GitLab variables | GitLab — канонический стор (уже так) | — | при изменении |
| Код, конфиги, профили | git (GitHub/GitLab) | уже есть | постоянно |

Обязателен **restore-тест** (Phase 1, потом ежеквартально): на чистом
sandbox восстановить kanban.db + профили и убедиться, что конвейер
продолжает с места остановки. Бэкап без restore-теста — это лотерея.

Исторический set 2026-07-09 прошёл изолированный restore. Текущий запуск
2026-07-21 завершился с ошибкой: нет архива профилей и пяти kanban DB.
Следовательно, текущие backup/restore criteria — **FAIL**.

RPO ≤ 24ч (NFR-4); RTO цели: процесс — минуты (supervisor), sandbox —
< 1ч (`setup.sh` + restore), хост — день (переустановка по DEPLOY.md +
бэкапы; документировать runbook).

## 6. Деградация и изоляция отказов

- **Роутер недоступен** → агенты не работают. Митигиция: LiteLLM fallback
  уже есть на уровне тиров; сам роутер — `restart: unless-stopped` +
  healthcheck; премортем: диск заполнен логами → logrotate в compose.
- **MemGraphRAG недоступен** → spool-деградация (04 §6), конвейер жив;
  compose-рестарт возвращает сервис за секунды (Contriever грузится из
  запечённого кэша ~20с).
- **Telegram недоступен** → карточки копятся, воркеры работают; боты
  переподключаются (long polling); алерты watchdog в этот период не
  доставляются — фиксируются в jsonl (осознанный остаточный риск, резервный канал алертов
  вне Telegram — Phase 4+).
- **NVIDIA API квота/недоступность** → LiteLLM fallback anthropic (есть
  для large; добавить fallback-цепочки для cheap/mid).
- **GPU занят/умер** → страдают только vllm-classifier/whisper/ocr:
  classifier имеет fast-path без LLM; whisper/ocr вернут ошибку → боты
  сообщают «голос/фото временно не обрабатываю» (проверить текущее
  поведение, не должно быть молчаливого зависания).
- **Один воркер заспамил бюджет** → `--max` диспетчера + `max_turns`
  профилей (уже стоят) + суточный лимит ключей в LiteLLM (проверить,
  включить budget-фичу LiteLLM).

## 7. Масштабирование

### 7.1 По задачам (вертикально, в рамках хоста)
Ручки: `kanban.max_concurrent_workers` встроенного gateway dispatch,
tier-политика (auto→cheap для механики),
`reasoning_effort` per-profile. Узкое место — бюджет NVIDIA API и один
GPU для аукс-моделей. Метрики §4 покажут, когда упёрлись.

### 7.2 По доменам (новые контуры)
Шаблонная процедура (цель — «новый домен за день»):
1. профиль(и) в `profiles/` (config.yaml + SOUL.md + skills);
2. доска в kanban + строки в sweeps;
3. пресет политики в sandbox-templates (если нужны новые эндпоинты);
4. namespace в MemGraphRAG (env NAMESPACES) + ключ ACL;
5. cron/webhook входы;
6. строка в watchdog (если появился новый процесс/сервис).
Оформить как чек-лист `docs/agents-v2/checklists/new-domain.md` при
первом исполнении (Phase 3, на домене equipment).

### 7.3 По sandbox'ам (изоляция рисков, Phase 3)
`bks-equipment`, `bks-org` — отдельные sandbox'ы со своими kanban.db.
Мосты — webhook/«карточка-посол» (02 §2). Supervisord+watchdog-строки
добавляются по чек-листу.

### 7.4 По железу (Phase 4+, опционально)
Второй хост: offsite-бэкапы, реплики compose-сервисов, перенос части
sandbox'ов. До тех пор честно живём с single-host рисками (00 §5).

## 8. Definition of Done

- [ ] kill -9 любого процесса из §1 → восстановление ≤ 2 мин без человека
- [ ] `docker stop` контейнера MemGraphRAG → алерт в Telegram ≤ 15 мин (NFR-1)
- [ ] недельный прогон: ежедневные сводки приходят, ложных алертов < 3/нед
- [ ] новый полный set из 8 артефактов создан без ошибок и восстановлен
      изолированно (restore 2026-07-09 — только исторический snapshot:
      [execution-logs/phase0-restore-test.md](execution-logs/phase0-restore-test.md))
- [ ] runbook восстановления хоста в DEPLOY.md
