# Полная схема взаимодействия nemohermes_bks

## Состав репозитория

| Папка | Роль |
|---|---|
| `NemoClaw/` | Апстрим-инструмент NVIDIA: CLI + blueprint для запуска агентов (Hermes/OpenClaw) в изолированных OpenShell-песочницах. Управляет жизненным циклом sandbox (onboard/policy/exec). |
| `sandbox-templates/` | Наш слой enterprise-политик НАД NemoClaw: пресеты `internal-api`, `github-hermes`, `claude-code-strict` и т.д. — шаблон для *любого* клиента/команды. |
| `bksamotsvety/` | Конкретное прод-развёртывание для клиента «БайкалКварцСамоцветы»: один sandbox `bks-production` с 8 агентскими профилями. |
| `router/` | Свой LLM-роутер (classifier + LiteLLM) — единая точка инференса для всех профилей bksamotsvety. |
| `MemGraphRAG/` | Сервис графовой памяти (episodes + retrieval), используется профилями `structuring`/`research`/`experiment`/`market-monitor`/`analytics`/`content`. |
| `host-infra/` | Хостовый supervision-слой: watchdog (9 проверок / 5 мин / Telegram-алерты), ежедневные бэкапы, compose-определение CI-раннера `gitlab-runner-shell`. Работает systemd-таймерами НА хосте — сознательно слоем ниже docker (см. §2.6). |
| `monitoring/` | Observability-модуль (выделен из router 2026-07-08): Grafana + Prometheus + Loki + Promtail отдельным compose-проектом. История/дашборды/dead-man алерт на watchdog; первичная тревога остаётся за watchdog (см. §2.7). |

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
│    bks/router          → CI: lint → eval-config → unit-test(25) → build     │
│                           → deploy (gb10-shell): docker compose + sync-триггер│
│                           project vars: NVIDIA_API_KEY*, OPENAI/ANTHROPIC_KEY,│
│                             HF_TOKEN, GRAFANA_*, BKS_SYNC_TRIGGER_TOKEN       │
│    bks/sandbox-templates → CI: validate-presets                              │
│    bks/memgraphrag     → CI: lint → unit-test(12) → build                   │
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
              + допись значений из group/project Variables
                (LITELLM_MASTER_KEY, MEMGRAPHRAG_API_KEY, QDRANT_API_KEY,
                 BKS_TELEGRAM_*, NEMOCLAW_SANDBOX_NAME)
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

### Pre-push eval gate (router)

```
git push origin main
        │
        ▼
.githooks/pre-push
        │
        ├── изменились classifier.py / litellm_config.yaml /
        │   docker-compose.yml / Dockerfile / supervisord.conf?
        │        нет → пропустить
        │        да  ↓
        │
        ├── все коммиты помечены "style:"?
        │        да  → пропустить (форматирование, логика не менялась)
        │        нет ↓
        │
        └── eval/sandbox/run.sh python3 gate.py
                  │
                  ├── claude-cli недоступен (протухшая сессия)
                  │        → [INFRA-WARN], exit 0 (push НЕ блокируется)
                  │
                  ├── avg correctness упал ≥ 1.5 по тиру ИЛИ curated-кейс pass→fail
                  │        → GATE: FAIL, exit 1 (push заблокирован)
                  │
                  └── в пределах порога
                           → GATE: OK, exit 0
```

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
                               │        • host.openshell.internal:10301 (STT)
                              │        • host.openshell.internal:11434/8000 (local-inference, опц.)
                              │        • api.openai.com / api.anthropic.com (опц.)
                              │
                              ├─ 3. openshell provider create           ──▶  Telegram-токены (production/marketing)
                              │                                              как secrets, не в файлах sandbox
                              │
                              ├─ 4. openshell provider create           ──▶  MEMGRAPHRAG_API_KEY как secret
                              │
                              ├─ 5. sync-profiles.sh                    ──▶  копирует bksamotsvety/profiles/*
                              │                                              (config.yaml, SOUL.md, skills/) в sandbox
                              │
                              └─ 6. start-gateways.sh                   ──▶  внутри sandbox:
                                       nemohermes sandbox exec bks-production --
                                         hermes gateway run --profile <name>
                                       (по одному процессу на профиль с Telegram)
```

Песочница = OpenShell sandbox, управляется только NemoClaw (`nemohermes sandbox …`); K3s не участвует (декомиссирован, см. §2.5).
Канонический источник секретов — **GitLab CI/CD Variables**: часть — group-level
группы `bks` (общие с router/MemGraphRAG, см. раздел 0), часть — project-level
`bks/bksamotsvety` (Telegram-токены, chat/allowed id, имя sandbox).
Локально: `deploy/.env` (gitignored, заполняется вручную или экспортом из GitLab).
Сам процесс агента ключи не видит — они инжектируются через `openshell provider` / `inference.local`.

---

## 2. Рантайм: 8 профилей-агентов в одном sandbox

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
                         │   │ structuring   │  │   research    │  ── code_execution ──▶ HTTP к MemGraphRAG API                    │
                         │   │ (пишет)       │  │ (читает)      │     (пишут также: experiment, market-monitor;                    │
                         │   │               │  │               │      читают также: analytics, content — см. ниже)               │
                         │   │ model: mid    │  │ model: large  │  ── web_search/extract (nous-web) ──▶ интернет                    │
                         │   └───────┬───────┘  └───────┬───────┘                                                                  │
                         │           │                   │            ┌───────────────┐                                            │
                         │           │                   │            │   content     │  model: large                              │
                         │           │                   │            └───────┬───────┘                                            │
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

### Дополнительные внешние связи профилей

- **MemGraphRAG** (см. раздел 2.5), все связи — `code_execution` → HTTP:
  - **structuring** пишет эпизоды в namespace `prod` (`POST /api/episodes`,
    LLM-экстракция сущностей/связей из карточки директора).
  - **research** читает эпизод и делает `POST /api/retrieve` по namespace `prod`.
  - **experiment** пишет результат эксперимента в namespace `prod` после
    завершения (`POST /api/episodes`, source привязан к task_id).
  - **market-monitor** пишет ценовые находки в namespace `mkt`.
  - **analytics** читает `POST /api/retrieve` по обоим namespace (`prod` и `mkt`).
  - **content** читает `POST /api/retrieve` по namespace `prod`.
- **director-bot / mkt-bot** (опц.): STT на `host.openshell.internal:10301`
  (speaches/faster-whisper-server, модель `Systran/faster-whisper-large-v3`).
  Требует явной секции `stt:` в `config.yaml` профиля (`provider: openai`,
  `openai.base_url: "${STT_API_URL}/v1"`) — без неё автоопределение даёт «none».
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
> k3s.service остановить вручную: `sudo systemctl stop k3s && sudo systemctl disable k3s`.
> K8s-манифесты в `MemGraphRAG/deploy/*-k3s.yaml` сохранены как документация, помечены deprecated.
> `kubectl` на хосте больше не нужен ни одному рабочему контуру.

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
- **backup** (`bks-backup.timer`, ежедневно 03:00): kanban.db ×5 досок
  (VACUUM INTO), профили Hermes, данные MemGraphRAG и Qdrant; ротация 7 дней.
- **gitlab-runner-shell/**: compose-определение CI-раннера (см. §0) —
  пересоздание только вручную с хоста (runner не может пересоздать сам себя):
  `cd /home/admin/servers/gitlab-runner-shell && docker rm -f gitlab-runner-shell && docker compose up -d`.

## 2.7 Monitoring (bks/monitoring, выделен из router 2026-07-08)

Grafana + Prometheus + Loki + Promtail — отдельный compose-проект
`monitoring`: Grafana смотрит за всей системой, не только за роутером.
Подключается к сети `router_default` и volumes `router_*` как external
(zero-copy переезд, данные Grafana/Prometheus/Loki унаследованы).

Разделение ролей с watchdog (§2.6) — **взаимный надзор, без дублирующих
алертов** (один инцидент = один канал тревоги):

| Слой | Роль | Кто его страхует |
|---|---|---|
| systemd-watchdog | первичная тревога (Telegram при OK→FAIL), ниже docker | Grafana: алерт `Watchdog Silent` — записи watchdog отсутствуют в Loki > 20 мин |
| bks/monitoring | история, дашборды (BKS Router, BKS Health), тренды | watchdog: проверка `docker_containers` ловит unhealthy у monitoring-* |

Потоки: audit/security_audit.jsonl роутера (volume) и metrics.jsonl
watchdog (bind mount) → promtail → Loki; /metrics router/litellm/vllm-* →
Prometheus (по Docker DNS через external-сеть). Дашборд «BKS Health»:
провалы за 15 мин, dead-man счётчик, разбивка по 9 проверкам, лента fail.

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
tier'а; credential rewrite на egress, чтобы токены не попадали в память
процесса агента; push в git — только у claude-code-агентов (`github-claude-code`/
`gitlab-claude-code`), Hermes — read-only git (upload-pack) + MR/PR через API;
телеметрия Claude Code (statsig/sentry) вырезана в `claude-code-strict`,
оставлен только `api.anthropic.com`; phone-home Hermes (`nousresearch.com`)
осознанно не трогаем — это апстримная база агента.

---

## 4. Качество и CI роутера

```
router/
 ├── tests/
 │     ├── unit/test_classifier.py   (11 тестов)  — classify(), /v1/chat/completions, моки AsyncMock
 │     └── unit/test_render_config.py (14 тестов) — key discovery, deployments, YAML output
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
     → unit-test (pytest tests/unit/, python:3.11-slim, без GPU)
     → build (kaniko → 192.168.2.180:5050/bks/router:latest)
```

---

## Итоговая сводка потоков данных

1. **Telegram → агент**: per-profile gateway (`hermes gateway run --profile X`)
   принимает сообщения, разграничение по chat_id группы.
2. **Агент → LLM**: всегда через `router:4000/30400` (или напрямую cloud/local,
   если профиль переопределён) → classifier выбирает tier → LiteLLM → NVIDIA/Anthropic.
3. **Агент → память/граф**: `structuring`/`research`/`experiment`/
   `market-monitor`/`analytics`/`content` читают/пишут в MemGraphRAG через
   `code_execution` (см. §2, «Дополнительные внешние связи профилей»).
   Отдельно от графа — встроенная Hermes `memory` toolset (файловая, в
   самом sandbox, `memory_enabled: true`): включена у `research`
   (паттерны между сессиями), `experiment` (история экспериментов) и
   `analytics`; выключена у `structuring`, `market-monitor`, `content`.
4. **Агент → веб**: `research`/`market-monitor` — `web_search`/`web_extract`
   через пресет `nous-web`.
5. **CI/CD**: push в GitLab (router/memgraphrag/sandbox-templates) → lint + unit-test
   + kaniko build → образ в registry → `docker compose pull && up -d` обновляет сервис на хосте
   (K3s декомиссирован 2026-07-06, `kubectl rollout restart` больше не используется).
   `bksamotsvety`: push в GitHub → pull mirror → GitLab (только зеркало, CI не запускается).
6. **Управление**: разворачивание sandbox'ов и сетевые политики — через NemoClaw
   CLI (`nemohermes ...`); docker-compose сервисы — через `docker compose` на хосте;
   секреты — в GitLab CI/CD Variables (`bks/bksamotsvety`).
