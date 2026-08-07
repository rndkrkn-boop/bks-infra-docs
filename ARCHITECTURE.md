# Полная схема взаимодействия nemohermes_bks

> [!CAUTION]
> **Статус на 2026-07-25: NO-GO** для безусловной приёмки production.
> Источник актуального статуса:
> [`docs/audit/full-project-audit-2026-07-25.md`](./docs/audit/full-project-audit-2026-07-25.md)
> (повтор [`2026-07-21`](./docs/audit/full-project-audit-2026-07-21.md)).
> Все блокеры от 2026-07-21 закрыты; текущие блокеры — сломанная установка
> `mcp` для MemGraphRAG MCP (read-only `/opt/hermes/.venv`) и неподтверждённый
> GitLab provenance. Схемы ниже описывают проектный контракт. Блоки
> «Наблюдалось 2026-07-21» отделяют подтверждённый на тот момент runtime от
> целевого устройства системы.

## Состав репозитория

| Папка | Роль |
|---|---|
| `NemoClaw/` | Апстрим-инструмент NVIDIA: CLI + blueprint для запуска агентов (Hermes/OpenClaw) в изолированных OpenShell-песочницах. Управляет жизненным циклом sandbox (onboard/policy/exec). |
| `sandbox-templates/` | Наш слой enterprise-политик НАД NemoClaw: пресеты `internal-api`, `github-hermes`, `claude-code-strict` и т.д. — шаблон для *любого* клиента/команды. |
| `bksamotsvety/` | Конкретное прод-развёртывание для клиента «БайкалКварцСамоцветы»: один sandbox `bks-production` с 9 агентскими профилями. |
| `router/` | Свой LLM-роутер (classifier + LiteLLM) — единая точка инференса для всех профилей bksamotsvety. |
| `MemGraphRAG/` | Сервис графовой памяти (episodes + retrieval), используется профилями `structuring`/`research`/`experiment`/`market-monitor`/`analytics`/`content`. |
| `host-infra/` | Хостовый supervision-слой: watchdog (9 проверок / 5 мин / Telegram-алерты), ежедневные бэкапы, compose-определение CI-раннера `gitlab-runner-shell`. Работает systemd-таймерами НА хосте — сознательно слоем ниже docker (см. §2.6). |
| `monitoring/` | Observability-модуль (выделен из router 2026-07-08): Grafana + Prometheus + Loki + Alloy отдельным compose-проектом (Promtail заменён на Alloy 2026-08-04). История/дашборды/dead-man алерт на watchdog; первичная тревога остаётся за watchdog (см. §2.7). |
| `matrix/` | Self-hosted Matrix Synapse + PostgreSQL — задел под внутренний LAN-чат/ops-алерты без зависимости от `api.telegram.org`, federation выключена. **Единственный компонент вне контракта соседей**: локальный git-репозиторий без проекта в GitLab-группе `bks` (ветка `improvement/task-1`, `main` не создан), не в `docs-consistency`-проверке, не в daily-backup контракте host-infra. Уже работает на хосте (Synapse+PG живые, боты `director-bot`/`mkt-bot` зарегистрированы в Synapse, network policy `host.openshell.internal:8008` на sandbox уже применена), но `platforms.matrix` ни в один профильный `config.yaml` не подключён — см. R13 в [аудите 2026-07-25](./docs/audit/full-project-audit-2026-07-25.md). |

---

## 0. CI/CD и инфраструктура (GitLab CE + GitHub)

Самохостинг на том же физическом хосте (192.168.2.180).
`bksamotsvety/` живёт в GitLab (`bks/bksamotsvety`) и одновременно пушится в
GitHub (`rndkrkn-boop/bksamotsvety`) как личный бэкап — это два независимых
`git push`, не настроенное зеркалирование одного в другое.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  GitLab CE 19.1.1   http://192.168.2.180:8929                               │
│  Container Registry  192.168.2.180:5050  (HTTP)                             │
│                                                                               │
│  Group bks — group-level CI/CD Variables (единый секрет на все три          │
│  проекта ниже, без переименований на границах):                             │
│    LITELLM_MASTER_KEY, LITE_LLM_ENDPOINT, MEMGRAPHRAG_API_KEY, QDRANT_API_KEY │
│                                                                               │
│  Репозитории (+ project-level Variables для того, что специфично одному):    │
│    bks/router          → CI: lint → eval-config → unit-test(64) → build     │
│                           → deploy (gb10-shell): docker compose + sync-триггер│
│                           project vars: NVIDIA_API_KEY*, OPENAI/ANTHROPIC_KEY,│
│                             HF_TOKEN, GRAFANA_*, BKS_SYNC_TRIGGER_TOKEN       │
│    bks/sandbox-templates → CI: validate-presets                              │
│    bks/memgraphrag     → CI: lint → unit-test(56) → build → offline-smoke  │
│                           → deploy (gb10-shell): docker compose + sync-триггер│
│                           project var: MEMGRAPH_MODEL (tier роутера),        │
│                             BKS_SYNC_TRIGGER_TOKEN                           │
│    bks/bksamotsvety    → CI: lint (shellcheck) → sync (gb10-shell)          │
│                           project vars: BKS_TELEGRAM_*, chat/allowed id,     │
│                             NEMOCLAW_SANDBOX_NAME                            │
│    bks/host-infra      → CI: shellcheck → deploy (gb10-shell): tar-поток    │
│                           в /home/admin/servers/ через helper-контейнер +    │
│                           drift-check systemd-юнитов (см. §2.6)              │
│    bks/monitoring      → CI: lint (json/yaml) → deploy (gb10-shell):        │
│                           docker compose -p monitoring up -d (см. §2.7)      │
│                           project vars: GRAFANA_ADMIN_PASSWORD,              │
│                             GRAFANA_TELEGRAM_BOT_TOKEN/CHAT_ID               │
│                                                                               │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────────┐  │
│  │ gitlab-runner (CPU)      │  │ gitlab-runner-gpu                        │  │
│  │ docker executor          │  │ docker executor + nvidia CDI             │  │
│  │ jobs: lint, unit-test,   │  │ tags: [gpu]                              │  │
│  │       kaniko build       │  │ jobs: vLLM smoke, LoRA eval (Phase 5)   │  │
│  └──────────────────────────┘  └──────────────────────────────────────────┘  │
│  gb10-shell runner (shell executor) — единственный раннер, который может     │
│  деплоить docker-compose и дёргать sandbox. Это PROJECT-runner: новый проект │
│  привязывается вручную (rails: runner.assign_to + pick_build! для тика       │
│  кэша очереди). Контейнер gitlab-runner-shell управляется compose-файлом из  │
│  bks/host-infra (group_add: DOCKER_GID для docker.sock — потеря этой         │
│  настройки при ручном пересоздании 2026-07-07 молча ломала все docker-jobs). │
└─────────────────────────────────────────────────────────────────────────────┘
          │ kaniko push (на каждый push в main)
          ▼
  192.168.2.180:5050/bks/router:latest
  192.168.2.180:5050/bks/memgraphrag:latest
          │
          │ deploy job (gb10-shell): docker compose pull && up -d
          ▼
  docker-compose на хосте (K3s декомиссирован, см. §2.5)
```

### Sync bksamotsvety после деплоя router/MemGraphRAG (SYNC_ONLY)

До 2026-07-07 `LITELLM_MASTER_KEY`/ключ MemGraphRAG приходилось вручную
копировать в `bksamotsvety/deploy/.env` — при ротации на стороне
router/MemGraphRAG связь с уже работающим sandbox тихо рвалась до
следующего ручного захода оператора. Теперь секреты — group CI Variables
(см. выше), а обновление доезжает до sandbox сразу, активным push:

```
router: deploy job                    memgraphrag: deploy job
  docker compose up -d                   docker compose up -d
        │                                       │
        ▼                                       ▼
  health-check OK                         health-check OK
        │                                       │
        └───────────────────┬───────────────────┘
                             │ curl -X POST .../trigger/pipeline
                             │   token=$BKS_SYNC_TRIGGER_TOKEN
                             │   variables[SYNC_ONLY]=true
                             │   (best-effort — не валит свой job при сбое)
                             ▼
                  bks/bksamotsvety: новый pipeline
                             │
             rules: SYNC_ONLY=true → job `lint` пропущен (when: never)
                                    → job `sync` запускается
                             │
                             ▼
        sync: cp deploy/.env.example deploy/.env
              + source: ${VAR:-default} берёт значения из уже экспортированных
                group/project Variables (LITELLM_MASTER_KEY,
                MEMGRAPHRAG_API_KEY, QDRANT_API_KEY, BKS_TELEGRAM_*,
                NEMOCLAW_SANDBOX_NAME)
              + bash deploy/sync-profiles.sh   (идемпотентна)
                             │
                             ▼
        sandbox "bks-production" получает свежие креды без участия
        человека. Обычный push в main этого же репо гоняет lint+sync —
        SYNC_ONLY лишь выключает lint для внешнего триггера.
```

`setup.sh` (первичный `nemohermes onboard`, сетевые политики,
`openshell provider create`) в CI не участвует — это ручной one-time шаг,
не часть регулярного деплоя.

### Quality gate роутера: ручной GitLab CI

```
изменение router
        │
        ▼
GitLab CI: ручной quality-gate job
        │
        └── eval/sandbox/run.sh → gate.py
                  ├── регрессия качества → GATE: FAIL, exit 1
                  ├── норма              → GATE: OK, exit 0
                  └── сбой инфраструктуры/claude-cli
                                             → GATE: SKIP, exit 0
```

Это **не автоматический fail-closed барьер**: job запускается вручную, а
`GATE: SKIP` не блокирует pipeline. Поэтому production-изменение может пройти
без оценки качества; аудит классифицирует это как High risk. Ранее описанный
локальный `.githooks/pre-push` удалён и частью текущего контроля не является.

### Quality gates nemohermes_bks (AUDIT-204 Phase 1)

PHASE 1 (2026-08-07 — текущий спринт):
- **unit-tests-coverage** ≥75%: добавлен в `.gitlab-ci.yml` job `daily-cycle-tests`
  - требует: `pytest-cov==5.0.0` в requirements-dev.txt
  - флаги: `--cov=. --cov-fail-under=75 --cov-report=xml --cov-report=term-missing`
  - артефакты: coverage.xml, .coverage, htmlcov/ (30 дней)
  
Полный **roadmap качественных гейтов** (Phase 1-5): см. [`docs/roadmap-gates.yaml`](./docs/roadmap-gates.yaml)
- Phase 2: docker-build-success
- Phase 3: security-scan-pass (trivy)
- Phase 4: helm-lint (если Helm/K3s вернётся)
- Phase 5: performance-regression monitoring (Prometheus)

Ссылка: AUDIT-204 (MEDIUM)

---

## 1. Контур деплоя (хост → sandbox)

Ниже — первичный one-time онбординг (`setup.sh`, вручную). Регулярное
обновление после первого раза идёт только через шаг 5 (`sync-profiles.sh`),
без 1-4 — см. подраздел "Sync bksamotsvety" в разделе 0.

```
GitLab CI/CD Variables  ──export──▶  deploy/.env  ──source──▶  setup.sh
(group + project, canonical store)     (gitignored, local copy)
                              │
                              ├─ 1. nemohermes onboard --agent hermes  ──▶  создаёт sandbox "bks-production" (NemoClaw/OpenShell)
                              │
                               ├─ 2. openshell policy update            ──▶  white-list egress:
                               │        • api.telegram.org:443
                               │        • host.openshell.internal:4000  (LLM Router, Docker Compose)
                              │        • host.openshell.internal:11434/8000 (local-inference, опц.)
                              │        • api.openai.com / api.anthropic.com (опц.)
                              │
                              ├─ 3. openshell provider create           ──▶  Telegram-токены (production/marketing)
                              │                                              как secrets, не в файлах sandbox
                              │
                              ├─ 4. sync-profiles.sh                    ──▶  копирует bksamotsvety/profiles/*
                              │                                              (config.yaml, SOUL.md, skills/) в sandbox;
                              │                                              MEMGRAPHRAG_API_KEY подставляется прямо в
                              │                                              .env профиля (host-side) — Hermes не понимает
                              │                                              openshell:resolve:env:, провайдер здесь не нужен
                              │
                              └─ 5. start-gateways.sh                   ──▶  внутри sandbox:
                                       supervisord → gw-director-bot
                                                    gw-mkt-bot
                                                    gw-experiment
                                       каждый запускает hermes gateway run --profile <name>;
                                       kanban dispatch встроен в gateway
```

Песочница = OpenShell sandbox, управляется только NemoClaw (`nemohermes sandbox …`); K3s не участвует (декомиссирован, см. §2.5).
Канонический источник секретов — **GitLab CI/CD Variables**: часть — group-level
группы `bks` (общие с router/MemGraphRAG, см. раздел 0), часть — project-level
`bks/bksamotsvety` (Telegram-токены, chat/allowed id, имя sandbox).
Локально: `deploy/.env` (gitignored, заполняется вручную или экспортом из GitLab).
Telegram-токены инжектируются через `openshell provider` / `inference.local`;
MEMGRAPHRAG_API_KEY и inference-ключи роутера — host-side литералами в `.env`
профиля (см. §2 ниже про MCP).

---

## 2. Рантайм: 9 профилей-агентов в одном sandbox

```
                         ┌────────────────────── sandbox "bks-production" (OpenShell, изолирован SSRF-guard) ──────────────────────┐
                         │                                                                                                          │
   Telegram (прод-группа)│   ┌───────────────┐        ┌──────────────┐                                                             │
   ───────────────────── │──▶│ director-bot  │        │  experiment  │  (тоже прод-группа)                                        │
                         │   │ model: auto    │        └──────────────┘                                                            │
                         │   └───────┬───────┘                                                                                      │
   Telegram (mkt-группа) │   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                                              │
   ───────────────────── │──▶│   mkt-bot     │  │market-monitor │  │   analytics   │  ── дайджесты ──▶ обратно в mkt-группу        │
                         │   │ model: auto    │  │ model: mid    │  │ model: large  │                                              │
                         │   └───────┬───────┘  └───────┬───────┘  └───────┬───────┘                                              │
                         │           │                   │                   │                                                      │
                         │   ┌───────────────┐  ┌───────────────┐                                                                  │
                         │   │ structuring   │  │   research    │  ── MCP (mcp_memgraphrag_*) ──▶ MemGraphRAG API                   │
                         │   │ (пишет)       │  │ (читает)      │     (пишут также: experiment, market-monitor;                    │
                         │   │               │  │               │      читают также: analytics, content — см. ниже)               │
                         │   │ model: mid    │  │ model: large  │  ── web_search/extract (nous-web) ──▶ интернет                    │
                         │   └───────┬───────┘  └───────┬───────┘                                                                  │
                         │           │                   │            ┌───────────────┐  ┌──────────────────┐                      │
                         │           │                   │            │   content     │  │ report-processor │                      │
                         │           │                   │            │ model: large  │  │ kanban worker    │                      │
                         │           │                   │            └───────┬───────┘  └────────┬─────────┘                      │
                         │           └───────────────────┴────────────────────┘                                                     │
                         │                                │                                                                          │
                           │                     все profile/model.base_url = ${BKS_ROUTER_URL}                                       │
                          └────────────────────────────────┼──────────────────────────────────────────────────────────────────────┘
                                                             │  http (через host.openshell.internal:4000, Docker Compose, allowed-ip whitelisted)
                                                             ▼
                                           ┌──────────────────────────────────────────────┐
                                           │   router  (docker-compose, на хосте)         │
                                           │                                                │
                                           │  classifier.py :4000 (FastAPI)                 │
                                          │   model=="auto" (три пути):                    │
                                          │    1. 1 msg < 2K симв. → cheap (fast-path)    │
                                          │    2. ≥5 tool-calls / >100K → large (fast)    │
                                          │    3. иначе → vllm-classifier (LLM, GPU)      │
                                          │       first_user[:400]+last_user[:400]         │
                                          │       → tier ∈ {cheap, mid, large}             │
                                          │   + security_check() параллельно (LoRA)        │
                                          │   иначе: передаёт tier как есть               │
                                          │        │                                       │
                                          │        ▼                                       │
                                          │  LiteLLM proxy :4001                           │
                                          │    cheap → nvidia/nemotron-3-nano-30b-a3b      │
                                          │    mid   → nvidia/llama-3.3-nemotron-super-49b │
                                          │    large → nvidia/nemotron-3-ultra-550b-a55b   │
                                          │            ↳ fallback: anthropic/claude-sonnet │
                                          └───────────────────┬────────────────────────────┘
                                                               │ https
                                                               ▼
                                          integrate.api.nvidia.com  /  api.anthropic.com
```

Девять профилей: `analytics`, `content`, `director-bot`, `experiment`,
`market-monitor`, `mkt-bot`, `report-processor`, `research`, `structuring`.
Не каждый профиль является постоянно работающим gateway: проектный контракт
supervision — **два** обязательных Telegram gateway (`director-bot`,
`mkt-bot`), остальные профили вызываются как исполнители. `experiment`
(`gw-experiment`) зарегистрирован в supervisord, но осознанно не входит
в обязательный контракт и не запускается `start-gateways.sh`/watchdog
autoheal — это ручной/опциональный процесс.

> **Обновлено 2026-07-25:** контракт из 3 gateway (наблюдение 2026-07-21)
> пересмотрен до 2 обязательных после аудита — процесс, которого не хватало,
> это `gw-experiment`. Watchdog проверяет `telegram_egress`, health и лог обоих
> обязательных gateway и автоматически восстанавливает их через
> `supervisorctl restart` (подтверждено: recovery за 57s после остановки
> обоих процессов). Полный recreate sandbox с нуля (bootstrap через
> `start-gateways.sh` из `deploy/.env`) по-прежнему не проверен — на хосте
> отсутствуют Telegram credentials в `deploy/.env`.

### Дополнительные внешние связи профилей

- **MemGraphRAG** (см. раздел 2.5), все связи — через MCP-сервер
  `bksamotsvety/mcp/memgraphrag_mcp.py` (stdio-подпроцесс, тонкий прокси к
  HTTP API; инструменты `mcp_memgraphrag_retrieve/get_episode/list_entities/
  store_episode`). Namespace read/write ACL задаётся per-profile в
  `mcp_servers.env` профильного `config.yaml`, а не на уровне API-ключа:
  - **structuring** пишет эпизоды в namespace `prod` (`store_episode`,
    LLM-экстракция сущностей/связей из карточки директора).
  - **research** читает эпизод и делает `retrieve` по namespace `prod`.
  - **experiment** пишет результат эксперимента в namespace `prod` после
    завершения (`store_episode`, source привязан к task_id).
  - **market-monitor** пишет ценовые находки в namespace `mkt`.
  - **analytics** читает `retrieve` по обоим namespace (`prod` и `mkt`).
  - **content** читает `retrieve` по namespace `prod` и `mkt`.
- **Kanban**: диспетчер встроен в gateway Hermes v2026.5.16 и включается
  `kanban.dispatch_in_gateway: true`. Отдельный `hermes kanban daemon`,
  отдельный pidfile и отдельный процесс supervision рабочему контракту не
  нужны; живучесть dispatch совпадает с живучестью gateway. Stale/blocked
  карточки обрабатывают cron sweeps, а не watchdog.
- **director-bot / mkt-bot**: STT идёт через общий router `:4000`:
  deployment `whisper` в LiteLLM → `vllm-whisper`. Требуется явная секция
  `stt:` в `config.yaml` профиля (`provider: openai`,
  `openai.base_url: "${STT_API_URL}/v1"`). Старый прямой контур
  `host.openshell.internal:10301` декомиссирован 2026-07-04 и не является
  fallback.
- Любой профиль можно индивидуально переключить на локальный Ollama/vLLM
  (`host.openshell.internal:11434|8088`) или прямой облачный провайдер.

---

## 2.5 К3s: ДЕКОМИССИРОВАН (2026-07-06)

> **Статус 2026-07-01:** K3s установлен и работает. Все поды `Running`.
> GB10 Grace Blackwell: `nvmlDeviceGetMemoryInfo()` не поддерживается →
> device plugin обходился privileged pod + hostPath `/dev/nvidia*`.

> **Статус 2026-07-02:** K8s router удалён. Роутер переехал в docker-compose (port 4000).

> **Статус 2026-07-03:** vllm-classifier (security/PII LoRA) выведен из K3s в
> docker-compose. Namespace `bks-router` опустел.

> **Статус 2026-07-06 (agents-v2 Phase 0.4):** K3s полностью декомиссирован.
> MemGraphRAG + Qdrant перенесены в docker-compose (см. §2.4 ниже).
> Namespace `memgraphrag` удалён (`kubectl delete namespace memgraphrag`).
> DaemonSet `nvidia-device-plugin-daemonset` (kube-system) удалён.
> K8s-манифесты в `MemGraphRAG/deploy/*-k3s.yaml` сохранены как документация, помечены deprecated.
> `kubectl` на хосте больше не нужен ни одному рабочему контуру.

**Текущий операционный контракт:** активных K3s-деплоев и процедур нет;
production использует Docker Compose + OpenShell. Аудит 2026-07-21 не обнаружил
listener `6443` и symlink enablement, хотя unit K3s всё ещё установлен;
`systemctl`-состояние независимо проверить не удалось. Исторические команды
K3s не являются инструкциями для текущей эксплуатации.

## 2.4 MemGraphRAG + Qdrant (docker-compose на хосте, с 2026-07-06)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  docker-compose -p memgraphrag  (на хосте 192.168.2.180)               │
│                                                                         │
│  memgraphrag  :8010→:8000   restart: unless-stopped  healthcheck /health│
│    image: 192.168.2.180:5050/bks/memgraphrag:latest                    │
│    data:   /home/admin/servers/memgraphrag/data  (bind mount)          │
│    env:    TRANSFORMERS_OFFLINE=1, HF_HUB_OFFLINE=1                    │
│            NAMESPACES=prod:bks_prod,mkt:bks_mkt                        │
│                   │                                                     │
│                   │ http://qdrant:6333 (docker network)                 │
│                   ▼                                                     │
│  qdrant           (внутренняя сеть, порты наружу не публикуются)       │
│    image: qdrant/qdrant:v1.9.7                                         │
│    data:   /home/admin/servers/memgraphrag/qdrant  (bind mount)        │
└─────────────────────────────────────────────────────────────────────────┘
```

- Агенты используют `MEMGRAPHRAG_API_URL=http://host.openshell.internal:8010`
- Деплой: `cd MemGraphRAG && docker compose -p memgraphrag pull && docker compose -p memgraphrag up -d`
- CI: kaniko build → registry → `docker compose -p memgraphrag pull && up -d` (не `kubectl rollout restart`)

**Роутер (2026-07-03: всё на хосте, docker-compose):**
- Агент использует `BKS_ROUTER_URL=http://host.openshell.internal:4000` (docker-compose, порт 4000)
- Docker-compose роутер (`router-proxy`) → docker-compose `vllm-classifier` (тот же compose-файл,
  Docker DNS `http://vllm-classifier:8000`, теперь с `--enable-lora`)
- К8s router Deployment удалён 2026-07-02: `kubectl -n bks-router delete deployment/router`
- K3s vllm-classifier удалён 2026-07-03 (был без Service — недостижим, LoRA простаивал);
  единственный экземпляр на этом железе не выигрывает от K8s-репликации

## 2.6 Хостовый supervision-слой (bks/host-infra, с 2026-07-07)

Watchdog и бэкапы (agents-v2 Phase 0.5/0.10) переведены из snowflake-файлов
в репозиторий `bks/host-infra`. Исполняются **systemd-таймерами на хосте,
сознательно НЕ в контейнере**: сторож должен стоять слоем ниже всего, что
он сторожит. Контейнеризованный watchdog умирал бы вместе с docker daemon —
ровно в момент, когда нужен; плюс потребовал бы docker.sock (root-эквивалент),
GPU и маунты `/` — «контейнер» с фиктивной изоляцией. Цепочка надзора:
systemd → docker/OpenShell → контейнеры → sandbox-процессы.

```
bks/host-infra (git) ──push──> CI: shellcheck ──> deploy (gb10-shell)
                                                    │ tar-поток через helper-
                                                    │ контейнер alpine (runner
                                                    ▼ не монтирует /servers)
/home/admin/servers/{watchdog,backup,gitlab-runner-shell}/   ← скрипты: CI
/etc/systemd/system/bks-*.{service,timer}   ← только вручную: sudo install.sh
                                              (CI делает drift-check и warning)
watchdog.env, state/, metrics.jsonl, backup.log, /home/admin/backups/bks/
                                            ← рантайм и секреты: CI не трогает
```

- **watchdog** (`bks-watchdog.timer`, каждые 5 мин): router /health,
  memgraphrag /health, Restarting/unhealthy-контейнеры, sandbox ready,
  supervisord-программы, kanban liveness, свежесть бэкапа (<26ч), диск (<85%),
  GPU. Алерты в Telegram при OK→FAIL/FAIL→OK + ежедневная сводка 09:00.
  Пороги стейл/blocked-карточек — НЕ здесь (cron-sweeps внутри sandbox).
- **backup** (`bks-backup.timer`, ежедневно 03:00): контракт полноты —
  **8 артефактов**: `kanban.db` пяти досок, архив профилей Hermes, архив
  MemGraphRAG и архив Qdrant; ротация 7 дней. Текущий скрипт архивирует
  live storage-каталог Qdrant без snapshot API/остановки контейнера, поэтому
  консистентность этого артефакта не гарантирована и требует отдельного
  integrity/restore-теста.

> **Наблюдалось 2026-07-21:** последний запуск завершился с одной ошибкой,
> архив профилей не создан, ни одна из пяти ожидаемых kanban БД не найдена.
> Лог сообщил о создании архивов MemGraphRAG и Qdrant, но inventory независимо
> не подтверждён. Текущая копия неполна (**FAIL**), хотя freshness-проверка
> watchdog показывала OK.
>
> **Исправлено 2026-07-25:** причиной была не потеря данных, а `PATH` без
> `~/.local/bin` в systemd unit `bks-backup.service` (`User=admin`, без
> `Environment=`) — `nemohermes` не резолвился (exit 127), поэтому все
> kanban/profiles-шаги молча проваливались. Добавлен явный `Environment=PATH=...`
> в unit; проверочный прогон — `Errors: 0`, все 8 артефактов, isolated-restore
> integrity_check прошёл на всех SQLite. См.
> [`docs/audit/full-project-audit-2026-07-25.md`](./docs/audit/full-project-audit-2026-07-25.md).
- **gitlab-runner-shell/**: compose-определение CI-раннера (см. §0) —
  пересоздание только вручную с хоста (runner не может пересоздать сам себя):
  `cd /home/admin/servers/gitlab-runner-shell && docker rm -f gitlab-runner-shell && docker compose up -d`.

## 2.7 Monitoring (bks/monitoring, выделен из router 2026-07-08)

Grafana + Prometheus + Loki + Alloy — отдельный compose-проект
`monitoring`: Grafana смотрит за всей системой, не только за роутером.
К router-стеку подключается external'ами: сеть `router_default`
(скрейп по Docker DNS) и volume `router_router_logs` (читает логи
роутера). Данные — в своих volume'ах `monitoring_*` (мигрированы из
`router_*` 2026-07-08). Рост логов ограничен: stdout контейнеров —
json-file 10m×3, Loki/Prometheus — retention 30d; исключение —
`{job="security-audit"}`, retention 90d (`retention_stream` в
`loki/loki-config.yml`): единственный поток с текстом запросов, по нему
разбирают инциденты постфактум. metrics.jsonl watchdog — самозачистка
через mv при >5 МБ (shipper-safe: новый inode, без дубликатов в Loki).

Разделение ролей с watchdog (§2.6) — **взаимный надзор, без дублирующих
алертов** (один инцидент = один канал тревоги):

| Слой | Роль | Кто его страхует |
|---|---|---|
| systemd-watchdog | первичная тревога (Telegram при OK→FAIL), ниже docker | Grafana: алерт `Watchdog Silent` — записи watchdog отсутствуют в Loki > 20 мин |
| bks/monitoring | история, дашборды (BKS Router, BKS Health), тренды | watchdog: проверка `docker_containers` ловит unhealthy у monitoring-* |

Потоки: audit/security_audit.jsonl роутера (volume) и metrics.jsonl
watchdog (bind mount) → alloy → Loki; /metrics router/litellm/vllm-* и
самого alloy (job `alloy`) → Prometheus (по Docker DNS через
external-сеть).

Сбор логов переведён с Promtail на **Grafana Alloy** 2026-08-04: promtail
deprecated с февраля 2025, поддержка закончилась в марте 2026. Вместе с
Loki 3.7 это дало фильтрующий слой, которого раньше не было: поля записей
(`check`, `status`, `tier`, `provider`, `profile`, `category`, `risk`) и
`level` уходят в **structured metadata** — фильтруются на чтении, но не
входят в идентичность стрима, поэтому stream-лейблы остались прежними
(`job`, `filename`) и кардинальность не выросла. Время записи берётся из
её собственного `ts`/`ts_ms`, а не из момента приёма. Подробности и
процедура отката — `monitoring/README.md`. Дашборд «BKS Health»:
провалы за 15 мин, dead-man счётчик, разбивка по 9 проверкам, лента fail.

## 2.8 Конвейер метрик (агрегация и федерация, с 2026-08-05)

Три этажа вместо одного скрейпа: сбор сырья, слой агрегации `bks:*` и
федерация в отдельный инстанс с годовым горизонтом. Контракты и обоснования —
[`docs/metrics/README.md`](./docs/metrics/README.md).

```
router/litellm/vllm-*/alloy /metrics ──► prometheus (30d, сырьё)
watchdog metrics.jsonl ─┐                      │ правила записи bks:* (60 правил)
backup-manager status  ─┼─► metrics-bridge.py ─┤ 30s/1m: SLI, 5m: суточные свёртки
compliance-audit.py    ─┘   *.prom → node-exporter (textfile) ──┐
                                                                ▼
                                        prometheus-global: /federate раз в 60 с,
                                        только bks:* и up, retention 365d
                                        (compose-профиль federation, опционален)
```

**Зачем слой агрегации.** `litellm_*` несут до 17 лейблов, среди них
неограниченно растущие `user_agent`, `client_ip`, `hashed_api_key`, `model_id`.
Правила оставляют стабильный разрез (`tier`, `provider`, `model`,
`status_code`), нормализуют `requested_model` LiteLLM в `tier` роутера — и тем
делают два слоя сопоставимыми в одном запросе. Второй эффект важнее первого:
выражение живёт в ОДНОМ месте и проверено юнит-тестами `promtool test rules`.
Раньше каждая панель и каждый алерт несли свою копию, и это уже трижды
расходилось с реальностью (мёртвое правило High Error Rate без общих лейблов,
anthropic-spike, делящий серию саму на себя, NoData-шум в пяти правилах).

**Textfile-путь для задач по таймеру.** Комплаенс-аудит, бэкап и мост нельзя
скрейпить: в момент скрейпа процесса уже нет. Они пишут `.prom`, node-exporter
отдаёт их как обычную экспозицию. До 2026-08-05 этот путь был ОПИСАН в §4.1 и
DEPLOY.md, но не существовал: node-exporter в стеке не было, каталога textfile
не было, `bks-compliance.timer` не было ни в репозитории, ни на хосте — метрики
`bks_compliance_*` писались в никуда. Конвейер закрывает обе половины.

**Зачем второй Prometheus при одном хосте.** Не география, а разделение
горизонтов и рисков: сырьё живёт 30 дней (разбор инцидента), свёртки — год
(стоимость, доступность, объём контекста в облако как аудиторское
свидетельство). Первый этаж перезапускается на каждом деплое и принимает
всплески кардинальности; у второго свой volume и около 60 стабильных серий.
`honor_labels: true` обязателен — без него `job`/`instance` федерированных серий
переименовываются в `exported_*`, и запросы по job слепнут молча.

**Алерты — только про то, что не сторожит никто другой:** правила записи падают,
группа не укладывается в интервал, мост или комплаенс перестали запускаться,
производитель вообще не появился, экспозиция битая, кардинальность растёт,
федерация встала. Метрики watchdog, диска и GPU записываются для истории, но
алертов по ним нет: первичная тревога по ним у systemd-watchdog (§2.6), и
дублировать канал нельзя.

---

## 3. Слой безопасности (sandbox-templates, применяется через NemoClaw)

```
OpenShell tiers: restricted (по умолчанию для прода) ⊂ balanced ⊂ open (не используем)
                       │
        ┌──────────────┴───────────────┐
        │                               │
  база агента (NemoClaw)          пресеты (opt-in, sandbox-templates/presets/)
  agents/hermes/policy-additions     • internal-api.yaml — точечный allowlist
  (или sandbox base для claude-code)   внутренних API (MemGraphRAG и т.п.)
                                      • github-hermes / gitlab-hermes — read-only
                                        git (upload-pack) + MR/PR через API
                                      • claude-code-strict — урезанный
                                        телеметрия/sentry, только api.anthropic.com
                                      • github-claude-code / gitlab-claude-code —
                                        полный git (включая push, в отличие от Hermes)
                                      • web-reference-claude-code — WebFetch только
                                        на курируемый allowlist документации
```

Три профиля sandbox-templates (один sandbox-под на агента, не на профиль bksamotsvety):

| Профиль | База | Пресеты |
|---|---|---|
| `hermes-local` | hermes + локальный inference | `local-inference` + `github-hermes`/`gitlab-hermes` + `internal-api` |
| `hermes-cloud` | hermes + облачный провайдер | `github-hermes`/`gitlab-hermes` + `internal-api` |
| `claude-code` | sandbox base | `claude-code-strict` + `github-claude-code`/`gitlab-claude-code` + `web-reference-claude-code` (+ `internal-api` если нужен) |

Принципы (зафиксированы 2026-06-11, см. `sandbox-templates/README.md`):
SSRF-guard блокирует приватные сети по умолчанию → каждый внутренний сервис
открывается явным `allowed_ips`/`endpoint` правилом, не общим послаблением
tier'а; credential rewrite на egress применяется к provider-секретам
(в production — Telegram), чтобы токены не попадали в память процесса агента.
Это не универсальная гарантия: router/MemGraphRAG ключи сейчас доставляются
host-side литералами в `.env` профилей (см. §1). Push в git — только у
claude-code-агентов (`github-claude-code`/
`gitlab-claude-code`), Hermes — read-only git (upload-pack) + MR/PR через API;
телеметрия Claude Code (statsig/sentry) вырезана в `claude-code-strict`,
оставлен только `api.anthropic.com`; phone-home Hermes (`nousresearch.com`)
осознанно не трогаем — это апстримная база агента.

---

## 4. Качество и CI роутера

```
router/
 ├── tests/
 │     ├── unit/test_classifier.py       — classify(), /v1/chat/completions, моки AsyncMock
 │     ├── unit/test_render_config.py    — key discovery, deployments, YAML output
 │     └── ...                           — полный suite: 64 теста на аудите
 │
 ├── eval/
 │     ├── gate.py          — регрессионный гейт: avg correctness по тиру vs baseline
 │     ├── eval_router.py   — batch прогон cheap/mid через роутер + golden Sonnet + judge
 │     ├── claude_cli.py    — тонкий wrapper над `claude -p` (ClaudeCliError → INFRA-WARN)
 │     ├── dataset.py       — curated / production датасеты
 │     ├── baselines/       — зафиксированные результаты (curated.json, production.json)
 │     └── sandbox/run.sh   — запуск gate.py в claude-eval OpenShell sandbox
 │
 └── training/              — LoRA дообучение Qwen3.5-0.8B (Phase 5)
       ├── gen_dataset.py        — генерация запросов + judge-разметка через claude-cli
       ├── train_classifier.py   — LoRA SFT классификатора тиров (trl/transformers)
       ├── eval_classifier.py    — accuracy vs бейзлайн
       ├── train_adapter.py      — универсальный SFT для S2L адаптеров (security/pii/etc.)
       ├── prepare_security_data.py — jailbreak-detection-dataset → security_train/val.jsonl
       └── prepare_pii_data.py   — pii-detection-dataset NER→[TYPE] → pii_train/val.jsonl
```

CI-пайплайн роутера (`bks/router/.gitlab-ci.yml`):

```
push → lint (ruff check + format) → eval-config (render_litellm_config.py)
     → unit-test (64/64 на аудите, python:3.11-slim, без GPU)
     → build (kaniko → 192.168.2.180:5050/bks/router:latest)
     ↘ quality-gate (manual; GATE: SKIP при infra failure имеет exit 0)
```

MemGraphRAG на том же аудите: **56/56** тестов прошли. Это repository evidence,
а не доказательство полного live E2E.

### 4.1 Комплаенс-аудит монорепо (`bks/infra`)

Регуляторные и внутренние требования проверяются исполняемым каталогом правил,
а не сверкой глазами:

```
compliance/rules.toml            — 14 правил + реестр исключений (источник истины)
scripts/compliance-audit.py      — движок (zero-dep, stdlib; область — git ls-files)
scripts/compliance-dashboard.py  — самодостаточный HTML-дашборд из JSON-отчёта
compliance/grafana-dashboard.json— тот же комплаенс во времени, поверх Prometheus
ci/compliance-report.sh          — один вход для CI-джоба и host-таймера
tests/test_compliance_audit.py   — 17 тестов движка
```

Покрытие: секреты вне git (NFR-5), изоляция runtime-репозиториев, отсутствие
`.env`/ключей под версионным контролем, непубликация chat_id и персональных
данных (152-ФЗ ст.5 ч.4), описанный PII-контур (NFR-6), namespace ACL графа,
пиннинг образов и зависимостей CI (SLSA Build L2), численный контракт полноты
бэкапа (NFR-4), свежесть аудиторских свидетельств, явный retention журналов
(GDPR art.5(1)(e)), лицензия, change-management через git (NFR-9).

```
push/MR/schedule → compliance-audit (python:3.11-slim, GIT_DEPTH=0)
                   ├── artifacts: report.json, report.md, dashboard.html (90 дней)
                   ├── метрики bks_compliance_* → node-exporter textfile → Prometheus
                   │   (путь реально существует с 2026-08-05, см. §2.8; до этого
                   │    .prom писался в каталог, который никто не читал)
                   └── exit 1 при нарушении severity ≥ high ([gate].block_at)
                 → compliance-tests (pytest, пиннутый)
```

Waiver — риск с владельцем, тикетом и сроком; просроченный не применяется и
сам становится находкой. WAIVED не засчитывается как выполнение правила, чтобы
балл соответствия нельзя было поднять выпиской исключений. Детали контракта —
`docs/compliance/README.md`.

---

## Итоговая сводка потоков данных

1. **Telegram → агент**: per-profile gateway (`hermes gateway run --profile X`)
   принимает сообщения, разграничение по chat_id группы.
2. **Агент → LLM**: всегда через `router:4000` (или напрямую cloud/local,
   если профиль переопределён) → classifier выбирает tier → LiteLLM → NVIDIA/Anthropic.
3. **Агент → память/граф**: `structuring`/`research`/`experiment`/
   `market-monitor`/`analytics`/`content` читают/пишут в MemGraphRAG через
   MCP-инструменты `mcp_memgraphrag_*` (см. §2, «Дополнительные внешние связи
   профилей»). `code_execution` не входит в этот контракт.
   Отдельно от графа — встроенная Hermes `memory` toolset (файловая, в
   самом sandbox, `memory_enabled: true`): включена у `research`
   (паттерны между сессиями), `experiment` (история экспериментов) и
   `analytics`; выключена у `structuring`, `market-monitor`, `content`.
4. **Агент → веб**: `research`/`market-monitor` — `web_search`/`web_extract`
   через пресет `nous-web`.
5. **CI/CD**: `router` выполняет lint, config-check, unit tests, Kaniko build и
   Compose deploy; `MemGraphRAG` — lint, unit tests, Kaniko build,
   offline-smoke и Compose deploy; `sandbox-templates` — только
   `validate-presets`. K3s декомиссирован 2026-07-06, `kubectl rollout restart`
   больше не используется.
   `bksamotsvety`: GitLab и GitHub обновляются двумя независимыми `git push`;
   автоматического зеркалирования между ними нет.
6. **Метрики**: `/metrics` роутера, LiteLLM, vLLM и alloy → Prometheus (сырьё,
   30 дней) → правила записи `bks:*` (агрегация и суточные свёртки) → federation
   в `prometheus-global` (только `bks:*` и `up`, 365 дней). Задачи по таймеру
   (watchdog, бэкап, комплаенс) не скрейпятся: `scripts/metrics-bridge.py`
   раскладывает их артефакты в textfile-экспозицию, которую отдаёт node-exporter
   (§2.8, [`docs/metrics/README.md`](./docs/metrics/README.md)).
7. **Управление**: разворачивание sandbox'ов и сетевые политики — через NemoClaw
   CLI (`nemohermes ...`); docker-compose сервисы — через `docker compose` на хосте;
   канонический store секретов — GitLab CI/CD Variables
   (`bks/bksamotsvety`); Telegram-токены доставляются через OpenShell provider,
   а ключи роутера/MemGraphRAG фактически записываются host-side литералами
   в `.env` профилей. Это документированное исключение и риск, а не
   credential-rewrite гарантия для всех секретов.
