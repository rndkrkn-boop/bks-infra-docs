# Надёжность и масштабирование

Урок аудита: система умирала не из-за сложных багов, а из-за отсутствия
трёх скучных вещей — supervision, watchdog, бэкапов. Этот документ — их
спецификация плюс план масштабирования.

## 1. Инвентарь долгоживущих процессов (что именно должно жить)

| Процесс | Где живёт | Кто следит (целевое) |
|---|---|---|
| OpenShell / sandbox `bks-production` | docker на хосте | docker restart policy + watchdog |
| gateway director-bot / mkt-bot / experiment | внутри sandbox | supervisord внутри sandbox |
| `hermes kanban daemon` | внутри sandbox | supervisord внутри sandbox |
| cron-джобы Hermes (sweeps, monitor, digest) | внутри sandbox (hermes cron) | сам Hermes; контроль «джобы существуют» — watchdog |
| router (classifier, litellm, vllm-classifier, whisper, ocr) | docker-compose хоста | `restart: unless-stopped` + healthchecks compose + watchdog |
| memgraphrag + qdrant | docker-compose хоста (Phase 0: миграция из K3s, кластер декомиссируется) | `restart: unless-stopped` + healthcheck compose + watchdog |
| GitLab + runners | docker-compose хоста | restart policy + watchdog (не критично для конвейера) |

## 2. Supervision внутри sandbox

Заменить `nohup ... &` из start-gateways.sh на supervisord:

- `deploy/supervisord.conf.tpl` в git; sync-profiles.sh рендерит
  (host-side envsubst, как уже делается для config.yaml — токены НЕ
  попадают в файл: supervisord получает их через `environment=` из
  файла с правами 0600, создаваемого при старте и живущего в tmpfs,
  либо через существующий механизм `openshell provider`, если он
  применим к не-inference секретам — уточнить при реализации);
- программы: `gw-director-bot`, `gw-mkt-bot`, `gw-experiment`,
  `kanban-daemon`; у всех `autorestart=true`, `startretries=5`,
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
4. `nemohermes bks-production status --json`* — sandbox Ready
   (*флаг проверить; иначе парсить exit code `doctor`);
5. `exec`: supervisord запущен и все программы RUNNING;
6. `exec`: возраст самой старой карточки в triage/ready < порога
   (24ч по умолчанию) и количество blocked с префиксом не-review < порога;
7. свежесть последнего успешного бэкапа < 26ч;
8. диск хоста < 85%, VRAM/GPU доступен (nvidia-smi).

Политика алертов: немедленный алерт при переходе OK→FAIL и при
восстановлении (FAIL→OK); повтор раз в 4ч, пока не починено (не заспамить);
ежедневная сводка «всё зелёное» одной строкой в 09:00 — тишина не должна
быть неотличима от смерти watchdog'а. Канал: отдельный Telegram-топик/чат
`bks-alerts`.

Watchdog-скрипт сам под systemd timer → его отказ виден по отсутствию
ежедневной сводки (и это единственное, за чем следит человек глазами).

## 4. Метрики (Phase 1 — файл, Phase 4 — Prometheus)

Watchdog складывает каждую проверку строкой JSON в
`/var/log/bks-watchdog/metrics.jsonl` + kanban-метрики из 03 §6.
Этого достаточно для ретроспектив («сколько задач в сутки», «где узкое
место») без развёртывания стека мониторинга. Prometheus/Grafana — только
когда jsonl станет тесен (осознанное откладывание).

## 5. Бэкапы и восстановление

| Что | Как | Куда | Частота |
|---|---|---|---|
| kanban.db | `exec sqlite3 .backup` (горячий бэкап корректен для SQLite) | `/home/admin/backups/bks/` + внешний носитель/вторая машина | ежедневно |
| ~/.hermes профилей (MEMORY.md, templates, skills, cron) | `hermes backup` или tar через exec | там же | ежедневно |
| MemGraphRAG data (episodes SQLite + igraph) | tar bind-mount каталога хоста (после Phase 0.1 это обычный каталог, не PVC) | там же | ежедневно |
| Qdrant | snapshot API (или tar storage-каталога при остановленном контейнере) | там же | ежедневно |
| deploy/.env, GitLab variables | GitLab — канонический стор (уже так) | — | при изменении |
| Код, конфиги, профили | git (GitHub/GitLab) | уже есть | постоянно |

Обязателен **restore-тест** (Phase 1, потом ежеквартально): на чистом
sandbox восстановить kanban.db + профили и убедиться, что конвейер
продолжает с места остановки. Бэкап без restore-теста — это лотерея.

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
Ручки: `daemon --max`, tier-политика (auto→cheap для механики),
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
- [ ] `docker stop` memgraphrag-пода → алерт в Telegram ≤ 15 мин (NFR-1)
- [ ] недельный прогон: ежедневные сводки приходят, ложных алертов < 3/нед
- [ ] restore-тест пройден и записан в runbook
- [ ] runbook восстановления хоста в DEPLOY.md
