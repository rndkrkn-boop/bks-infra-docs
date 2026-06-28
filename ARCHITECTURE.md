# Полная схема взаимодействия nemohermes_bks

## Состав репозитория

| Папка | Роль |
|---|---|
| `NemoClaw/` | Апстрим-инструмент NVIDIA: CLI + blueprint для запуска агентов (Hermes/OpenClaw) в изолированных OpenShell-песочницах. Управляет жизненным циклом sandbox (onboard/policy/exec). |
| `sandbox-templates/` | Наш слой enterprise-политик НАД NemoClaw: пресеты `internal-api`, `github-hermes`, `claude-code-strict` и т.д. — шаблон для *любого* клиента/команды. |
| `bksamotsvety/` | Конкретное прод-развёртывание для клиента «БайкалКварцСамоцветы»: один sandbox `bks-production` с 8 агентскими профилями. |
| `router/` | Свой LLM-роутер (classifier + LiteLLM) — единая точка инференса для всех профилей bksamotsvety. |
| `MemGraphRAG/` | Сервис графовой памяти (episodes + retrieval), используется профилем `research`. |

---

## 0. CI/CD и инфраструктура (GitLab CE)

Самохостинг на том же физическом хосте (192.168.2.180).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  GitLab CE 19.1.1   http://192.168.2.180:8929                               │
│  Container Registry  192.168.2.180:5050  (HTTP, insecure mirror в K3s)      │
│                                                                               │
│  Репозитории:                                                                 │
│    bks/router          → CI: lint → eval-config → unit-test(25) → build     │
│    bks/sandbox-templates → CI: validate-presets                               │
│    bks/memgraphrag     → CI: lint → unit-test(12) → build                   │
│                                                                               │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────────┐  │
│  │ gitlab-runner (CPU)      │  │ gitlab-runner-gpu                        │  │
│  │ docker executor          │  │ docker executor + nvidia CDI             │  │
│  │ jobs: lint, unit-test,   │  │ tags: [gpu]                              │  │
│  │       kaniko build       │  │ jobs: vLLM smoke, LoRA eval (Phase 5)   │  │
│  └──────────────────────────┘  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
          │ kaniko push (на каждый push в main)
          ▼
  192.168.2.180:5050/bks/router:latest
  192.168.2.180:5050/bks/memgraphrag:latest
          │
          │ imagePullPolicy: Always
          ▼
  K3s (см. раздел 2.5)
```

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

```
deploy/.env  ──source──▶  setup.sh
                              │
                              ├─ 1. nemohermes onboard --agent hermes  ──▶  создаёт sandbox "bks-production" (NemoClaw/OpenShell)
                              │
                              ├─ 2. openshell policy update            ──▶  white-list egress:
                              │        • api.telegram.org:443
                              │        • host.openshell.internal:4000  (LLM Router)
                              │        • host.openshell.internal:10301 (STT)
                              │        • host.openshell.internal:11434/8000 (local-inference, опц.)
                              │        • api.openai.com / api.anthropic.com (опц.)
                              │
                              ├─ 3. openshell provider create           ──▶  Telegram-токены (production/marketing)
                              │                                              как secrets, не в файлах sandbox
                              │
                              ├─ 4. openshell provider create           ──▶  BKS_MEMGRAPHRAG_API_KEY как secret
                              │
                              ├─ 5. sync-profiles.sh                    ──▶  копирует bksamotsvety/profiles/*
                              │                                              (config.yaml, SOUL.md, skills/) в sandbox
                              │
                              └─ 6. start-gateways.sh                   ──▶  внутри sandbox:
                                       nemohermes sandbox exec bks-production --
                                         hermes gateway run --profile <name>
                                       (по одному процессу на профиль с Telegram)
```

Песочница = K3s-под внутри OpenShell, управляется только NemoClaw (`nemohermes sandbox …`).
Креды провайдеров (NVIDIA/Anthropic/Telegram/MemGraphRAG) живут **на хосте**, агент обращается к
ним через `inference.local` / именованные provider-эндпоинты — сам процесс агента ключи не видит.

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
                         │   │ model: mid    │  │ model: large  │  ── web_search/extract (nous-web) ──▶ интернет                    │
                         │   └───────┬───────┘  └───────┬───────┘                                                                  │
                         │           │                   │            ┌───────────────┐                                            │
                         │           │                   │            │   content     │  model: large                              │
                         │           │                   │            └───────┬───────┘                                            │
                         │           └───────────────────┴────────────────────┘                                                     │
                         │                                │                                                                          │
                         │                     все profile/model.base_url = ${BKS_ROUTER_URL}                                       │
                         └────────────────────────────────┼──────────────────────────────────────────────────────────────────────┘
                                                            │  http (через host.openshell.internal:4000, allowed-ip whitelisted)
                                                            ▼
                                          ┌──────────────────────────────────────────────┐
                                          │   router  (на хосте)                          │
                                          │                                                │
                                          │  classifier.py :4000 (FastAPI)                 │
                                          │   model=="auto":                               │
                                          │    • контекст > 240K симв. → mid сразу         │
                                          │    • иначе — классификация через               │
                                          │      vllm-classifier (Qwen3.5-0.8B, :8000)    │
                                          │      → tier ∈ {cheap, mid, large}              │
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

- **research**: `code_execution` → HTTP к MemGraphRAG API (см. раздел 2.5)
- **director-bot / mkt-bot** (опц.): STT на `host.openshell.internal:10301`
  (speaches/faster-whisper-server, модель `Systran/faster-whisper-large-v3`).
  Требует явной секции `stt:` в `config.yaml` профиля (`provider: openai`,
  `openai.base_url: "${STT_API_URL}/v1"`) — без неё автоопределение даёт «none».
- Любой профиль можно индивидуально переключить на локальный Ollama/vLLM
  (`host.openshell.internal:11434|8088`) или прямой облачный провайдер.

---

## 2.5 K3s: целевой деплой сервисов

> **Статус 2026-06-28:** манифесты написаны, K3s ещё не установлен.
> Установка: `sudo bash /home/admin/servers/k3s/install.sh` (см. `DEPLOY.md`).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  K3s  (однонодовый, на хосте 192.168.2.180)                                 │
│                                                                               │
│  ┌──────────────────── namespace: bks-router ────────────────────────────┐  │
│  │                                                                         │  │
│  │  Deployment: vllm-classifier                                            │  │
│  │    image: vllm/vllm-openai:v0.9.0                                       │  │
│  │    resources: nvidia.com/gpu=1                                           │  │
│  │    hostPath: /root/models → /models (Qwen3.5-0.8B)                      │  │
│  │    args: --enable-auto-tool-choice --tool-call-parser qwen3_xml          │  │
│  │    Service: ClusterIP :8000 (только внутри кластера)                    │  │
│  │                  │                                                        │  │
│  │                  │ http://vllm-classifier.bks-router.svc:8000/v1         │  │
│  │                  ▼                                                        │  │
│  │  Deployment: router                                                       │  │
│  │    image: 192.168.2.180:5050/bks/router:latest  (CI → registry)         │  │
│  │    ConfigMap: litellm_config.base.yaml                                   │  │
│  │    Secret: router-apikeys (NVIDIA_API_KEY_N, ANTHROPIC_API_KEY)          │  │
│  │    supervisord: classifier.py :4000 + litellm :4001                      │  │
│  │    Service: NodePort 30400 → :4000  (внешний, для агентов)              │  │
│  │                                                                           │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌──────────────────── namespace: memgraphrag ───────────────────────────┐  │
│  │                                                                         │  │
│  │  Deployment: qdrant                                                      │  │
│  │    image: qdrant/qdrant:v1.9.7                                           │  │
│  │    PVC: qdrant-data 20Gi                                                 │  │
│  │    Secret: qdrant-apikey                                                 │  │
│  │    Service: ClusterIP :6333/:6334                                        │  │
│  │                  │                                                        │  │
│  │                  │ http://qdrant.memgraphrag.svc:6333                    │  │
│  │                  ▼                                                        │  │
│  │  Deployment: memgraphrag                                                  │  │
│  │    image: 192.168.2.180:5050/bks/memgraphrag:latest  (CI → registry)    │  │
│  │    PVC: memgraphrag-data 10Gi  (SQLite episodes + igraph файлы)          │  │
│  │    Secret: memgraphrag-apikey, qdrant-apikey                             │  │
│  │    env NAMESPACES: prod:bks_prod,mkt:bks_mkt                             │  │
│  │    readiness: GET /health, initialDelay 30s (Contriever load)            │  │
│  │    Service: ClusterIP :8000                                               │  │
│  │      → http://memgraphrag.memgraphrag.svc:8000                           │  │
│  │        (MEMGRAPHRAG_API_URL в Hermes-профилях)                           │  │
│  │                                                                           │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  NVIDIA device plugin DaemonSet (kube-system)                                │
│    → advertises nvidia.com/gpu=1 на ноде                                     │
│                                                                               │
│  registries.yaml: insecure mirror 192.168.2.180:5050                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

**После деплоя K3s:**
- агенты переключаются на `BKS_ROUTER_URL=http://192.168.2.180:30400`
- docker-compose роутер (`router-proxy`, `router-vllm-classifier`) останавливается

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
 └── training/              — LoRA дообучение Qwen3.5-0.8B (Phase 5, opt-in)
       ├── gen_dataset.py   — генерация запросов + judge-разметка через claude-cli
       ├── train_classifier.py  — LoRA SFT (trl/transformers)
       └── eval_classifier.py  — accuracy vs бейзлайн
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
3. **Агент → память/граф**: `research` читает/пишет в MemGraphRAG; остальные
   профили используют встроенную Hermes `memory` toolset (файловая, в самом sandbox).
4. **Агент → веб**: `research`/`market-monitor` — `web_search`/`web_extract`
   через пресет `nous-web`.
5. **CI/CD**: push в GitLab → lint + unit-test + kaniko build → образ в registry
   → `kubectl rollout restart` обновляет K3s pod.
6. **Управление**: разворачивание sandbox'ов и сетевые политики — через NemoClaw
   CLI (`nemohermes ...`); K3s-сервисы — через `kubectl apply -f deploy/`.
