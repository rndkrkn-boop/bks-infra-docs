# BKS NemoHermes — Deployment Playbook

Операционный справочник по развёртыванию и обслуживанию инфраструктуры.
Архитектура и схема взаимодействия — в [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Состояние на 2026-07-03

> **Аудит 2026-07-03** обнаружил, что предыдущая версия этой таблицы (статус
> на 2026-07-02) была неверна в двух местах: (1) `router-vllm-classifier` не
> имел `--enable-lora`, а `SECURITY_MODEL` был не задан в `router/.env` —
> security-проверка молча пропускалась на каждом запросе, несмотря на пометку
> "✅ в продакшне" ниже в разделе Phase 5; (2) K3s-под `vllm-classifier` с
> LoRA был недостижим — в кластере отсутствовал `Service`
> (`vllm-classifier.bks-router.svc` → NXDOMAIN), хотя манифест его описывал.
> Оба адаптера теперь перенесены целиком на хост в `router/docker-compose.yml`
> (см. §2.5 ARCHITECTURE.md) — единственный экземпляр на этом железе не
> выигрывает от K8s-репликации. Заодно найден и исправлен баг парсинга:
> `security-lora-v1` эхом повторяет заголовок `LEVEL|CATEGORY|REASON` перед
> ответом, `classifier.py` брал `"LEVEL"` вместо реального уровня риска
> (`raw.split("|")` → теперь `_SECURITY_RESULT_RE.search()`).

### Инфраструктура (хост `192.168.2.180`)

| Сервис | Контейнер / процесс | Порт(ы) | Статус |
|---|---|---|---|
| GitLab CE 19.1.1 | `gitlab` | 8929 (web), 2222 (SSH), 5050 (registry) | ✅ работает (Docker healthcheck ложно "unhealthy" — проверяет порт 80, а nginx слушает 8929) |
| GitLab Runner (CPU) | `gitlab-runner` | — | ✅ зарегистрирован |
| GitLab Runner (GPU) | `gitlab-runner-gpu` | — | зарегистрирован (`bks-gpu-runner`), но на 2026-07-03 периодически ловит 502 от GitLab API — перепроверить |
| vLLM classifier | `router-vllm-classifier` | внутренний | ✅ healthy, `--enable-lora` (security-lora-v1, pii-cleaner-lora-v1), `/home/admin/models/adapters` смонтирован |
| Router (docker-compose) | `router-proxy` | 4000, 4001 | ✅ production; `SECURITY_MODEL=security-lora-v1` активен |
| vLLM Qwen3.6-35B | `vllm-Qwen3.6-35B-A3B-NVFP4` | 8088 | ✅ running (дефолтный Hermes-профиль хоста ходит сюда напрямую, не через router) |
| MemGraphRAG (test) | `memgraphrag-test-qdrant` | 16333 | ✅ running |
| Whisper STT | `whisper-openai` | 10301 | ✅ running |
| K3s v1.32.5 | — | — | ✅ установлен, node Ready |
| namespace `bks-router` (K3s) | — | — | пуст с 2026-07-03: vllm-classifier удалён, перенесён на хост (см. выше) |

### GitLab репозитории

| Репо | Источник | CI-пайплайн | Статус |
|---|---|---|---|
| bks/router | GitLab (primary) | lint → eval-config → unit-test(25) → build | ✅ зелёный |
| bks/sandbox-templates | GitLab (primary) | validate-presets | ✅ зелёный |
| bks/memgraphrag | GitLab (primary) | lint → unit-test(12) → build | ✅ зелёный |
| bks/bksamotsvety | GitHub `rndkrkn-boop/bksamotsvety` (pull mirror, PAT) | нет CI; 35 CI/CD Variables | ✅ mirror настроен |

---

## Что уже сделано

- **GitLab CE** + Container Registry + Runner (CPU) — настроен, CI зелёный
- **CI пайплайны** для трёх репо: lint → unit-test → kaniko build → push в registry
- **Unit tests**: router (25), MemGraphRAG (12) — без GPU, без Qdrant
- **Eval gate** (`router/.githooks/pre-push`): блокирует push при регрессии качества
- **Auto DevOps отключён** на всех трёх проектах
- **K3s v1.32.5** — установлен, node Ready, NVIDIA container toolkit настроен
- **K3s манифесты** применены: MemGraphRAG + Qdrant работают, router namespace создан
- **bks/bksamotsvety** добавлен в GitLab как pull-mirror от GitHub (PAT)
- **Classifier улучшен**: fast-path роутинг (rule-based) + умное извлечение контента
  (first\_user[:400] + last\_user[:400] вместо last\_msg[:600])
- **S2L адаптеры обучены и задеплоены**:
  - `security-lora-v1` (eval\_loss=0.024, acc=98.9%) — в vllm-classifier, `SECURITY_MODEL=security-lora-v1` активен
  - `pii-cleaner-lora-v1` (eval\_loss=0.034, acc=99.1%) — в vllm-classifier (`/models/adapters/pii-cleaner-lora-v1`)
- **Prometheus метрики добавлены**: `bks_router_routing_path_total`, `bks_router_security_events_total`
- **GB10 workaround**: privileged pod + hostPath `/dev/nvidia*` + хирургические монты lib (libcuda, libnvidia-ptxjitcompiler)

---

## Остаток: шаги до полного деплоя

### Шаг 1 — Зарегистрировать GPU runner

> Нужен один раз. Позволит CI-джобам с `tags: [gpu]` использовать GPU (LoRA eval, vLLM smoke-тесты).

1. Открыть GitLab: <http://192.168.2.180:8929/admin/runners>
2. **New instance runner** → добавить тег `gpu`, выключить "Run untagged jobs"
3. Скопировать токен `glrt-...`
4. Запустить:
   ```
   ! RUNNER_TOKEN=glrt-... bash /home/admin/servers/gitlab/register-gpu-runner.sh
   ```
5. Проверить: <http://192.168.2.180:8929/admin/runners> — оба runner'а online

---

### Шаг 2 — Обновить сессию claude-cli в eval sandbox

> Нужен для работы pre-push eval gate при изменении classifier.py / litellm_config.

Симптом: gate.py выводит `[INFRA-WARN] claude-cli exit 1: ` и пропускает push (не блокирует).

```bash
# Проверить текущее состояние sandbox
! openshell ps

# Обновить сессию (откроется браузер или QR)
! openshell exec claude-eval claude --dangerously-skip-permissions login
```

После этого eval gate снова будет блокировать регрессии качества.

---

### Шаг 3 — Установить K3s ✅ выполнено

> K3s v1.32.5 установлен и работает.

```
! sudo bash /home/admin/servers/k3s/install.sh
```

#### GB10 Grace Blackwell: NVIDIA device plugin

NVIDIA k8s-device-plugin v0.17.0 **не работает** с GB10 из коробки по двум причинам:

1. `Incompatible strategy detected auto` — нужно настроить NVIDIA container toolkit для k3s containerd:
   ```bash
   ! sudo mkdir -p /var/lib/rancher/k3s/agent/etc/containerd
   ! sudo nvidia-ctk runtime configure --runtime=containerd \
       --config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
   ! sudo systemctl restart k3s
   ```

2. `error getting device memory: Not Supported` — GB10 unified memory не поддерживает
   `nvmlDeviceGetMemoryInfo()`. Device plugin не может зарегистрировать GPU через стандартный путь.

**Рабочий обходной путь для GB10** (однонодовый кластер): не используй `nvidia.com/gpu`
resource request. Вместо этого монтируй `/dev/nvidia*` напрямую в privileged pod.
Пример — см. `router/deploy/01-vllm-classifier.yaml`: `securityContext.privileged: true` +
hostPath volumes для каждого устройства.

#### Импорт образа vllm в k3s containerd

vllm образ скачан в Docker (27GB) — в k3s containerd его нет. Импортируй один раз:
```bash
! docker save vllm/vllm-openai:nightly -o /tmp/vllm-nightly.tar
! sudo k3s ctr images import /tmp/vllm-nightly.tar
! rm /tmp/vllm-nightly.tar
```

---

### Шаг 4 — Настроить kubeconfig для пользователя admin

```bash
! mkdir -p ~/.kube
! sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
! sudo chown admin ~/.kube/config
```

Проверка:
```
! kubectl get nodes
! kubectl get ds -n kube-system nvidia-device-plugin-daemonset
```

Ожидаемый статус nvidia-device-plugin: `DESIRED=1  CURRENT=1  READY=1`.

---

### Шаг 5 — Создать секреты в K3s

Секреты создаются **вне git** (ключи не хранятся в репо).

**MemGraphRAG namespace:**
```bash
! kubectl apply -f ~/projects/nemohermes_bks/MemGraphRAG/deploy/qdrant-k3s.yaml

# Сгенерировать и запатчить реальные ключи
! QDRANT_KEY=$(openssl rand -hex 32)
! MGR_KEY=$(openssl rand -hex 32)

! kubectl patch secret qdrant-apikey -n memgraphrag \
    -p "{\"stringData\":{\"api-key\":\"$QDRANT_KEY\"}}"

! kubectl patch secret memgraphrag-apikey -n memgraphrag \
    -p "{\"stringData\":{\"api-key\":\"$MGR_KEY\"}}"

# Записать ключ MemGraphRAG в deploy/.env для Hermes-профилей
! echo "BKS_MEMGRAPHRAG_API_KEY=$MGR_KEY" >> ~/projects/nemohermes_bks/bksamotsvety/deploy/.env
```

**Router namespace — УСТАРЕЛО (2026-07-03):** router-apikeys secret и весь
`bks-router` namespace были частью первого K3s-эксперимента с router.
vllm-classifier перенесён на хост (docker-compose), namespace `bks-router`
теперь пуст. Секция оставлена для истории — не выполнять на новой установке.
<details><summary>Исторические команды (не актуальны)</summary>

```bash
! kubectl apply -f ~/projects/nemohermes_bks/router/deploy/00-namespace.yaml

! kubectl create secret generic router-apikeys \
    --from-literal=NVIDIA_API_KEY_1=nvapi-... \
    --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
    -n bks-router
```
</details>

---

### Шаг 6 — Применить K3s манифесты (только MemGraphRAG)

```bash
# MemGraphRAG + Qdrant
! kubectl apply -f ~/projects/nemohermes_bks/MemGraphRAG/deploy/memgraphrag-k3s.yaml

# Следить за поднятием
! kubectl rollout status deployment/qdrant -n memgraphrag
! kubectl rollout status deployment/memgraphrag -n memgraphrag
```

> Router (`vllm-classifier` + LoRA) в K3s больше не разворачивается — см.
> примечание к "Шаг 7" ниже, весь роутер живёт в `router/docker-compose.yml`
> на хосте.

Ожидаемое время готовности:
- Qdrant: ~10 с
- MemGraphRAG: ~30 с (загрузка Contriever)
- router: ~20 с

---

### Шаг 7 — router через K3s (УСТАРЕЛО, не выполнять)

Эта секция описывала переключение агентов на K3s router через NodePort
30400. Эксперимент откачен 2026-07-02/03: K8s router Deployment удалён,
K3s vllm-classifier тоже удалён (не имел `Service` в кластере — был
недостижим). Router целиком живёт на хосте в docker-compose (порт 4000),
все профили уже указывают на `BKS_ROUTER_URL=http://host.openshell.internal:4000/v1`
— дополнительных действий не требуется. Причина отказа от K3s для этого
сервиса: единственный экземпляр на однонодовом GB10-кластере не выигрывает
от K8s-репликации, а лишний слой (Service DNS, kubectl apply) добавлял
только точки отказа.

Проверить маршрутизацию (docker-compose, актуально):
```bash
! curl -s http://192.168.2.180:4000/health
! curl -s -X POST http://192.168.2.180:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"auto","messages":[{"role":"user","content":"2+2"}]}'
```

---

## Обычные операции

### Обновить образ роутера после push в main

> **Важно:** с 2026-07-02 роутер работает через docker-compose, НЕ через K8s.
> `router` — локальная сборка (`build: .`), `docker compose pull` его не
> обновит — нужен `--build` (см. предупреждение в "Обновить docker-compose
> роутер" ниже).
```bash
! cd /home/admin/projects/nemohermes_bks/router && docker compose build router && docker compose up -d
```

#### K8s router — удалён 2026-07-02 (см. §2.5 ARCHITECTURE.md)
> `kubectl -n bks-router delete deployment/router` — K8s router больше не используется.
> Docker-compose роутер на порту 4000 — primary production router.

### Обновить образ MemGraphRAG

```bash
! kubectl rollout restart deployment/memgraphrag -n memgraphrag
```

### Посмотреть логи

```bash
# Docker Compose router (production, port 4000)
! cd /home/admin/projects/nemohermes_bks/router && docker compose logs -f --tail=50

# vllm-classifier (LoRA, теперь тоже на хосте)
! docker logs router-vllm-classifier -f --tail=50
! kubectl logs -n memgraphrag deployment/memgraphrag -f --tail=50
```

### Обновить docker-compose роутер

> `router` собирается локально (`build: .`), не тянется из registry —
> `docker compose pull` его НЕ обновит после правки `classifier.py`.
> Нужен явный `--build` (это то, что забыли сделать 2026-07-02/03: контейнер
> месяц работал на образе от 2026-06-29 без свежего кода security-LoRA).

```bash
! cd /home/admin/projects/nemohermes_bks/router && docker compose build router && docker compose up -d
# vllm-classifier (image из registry, не build) можно просто pull:
! docker compose pull vllm-classifier && docker compose up -d vllm-classifier
```

### Перезапустить docker-compose роутер

```bash
! cd /home/admin/projects/nemohermes_bks/router && docker compose restart
```

### Запустить eval gate вручную

```bash
! cd ~/projects/nemohermes_bks/router/eval/sandbox
! ./run.sh /sandbox/.venv/bin/python3 gate.py
```

С обновлением baseline (если изменения намеренные):
```bash
! ./run.sh /sandbox/.venv/bin/python3 gate.py --update-baseline
```

### Перезапустить GitLab

```bash
! cd /home/admin/servers/gitlab && docker compose restart gitlab
```

### Обновить GitLab CE до новой версии

1. Изменить тег в `/home/admin/servers/gitlab/docker-compose.yml`:
   `gitlab/gitlab-ce:19.1.1-ce.0` → `gitlab/gitlab-ce:XX.Y.Z-ce.0`
2. То же для `gitlab-runner-gpu` и `gitlab-runner`
3. Применить:
   ```
   ! cd /home/admin/servers/gitlab && docker compose pull && docker compose up -d
   ```

---

## Endpoints и порты

| Сервис | URL | Заметки |
|---|---|---|
| GitLab web | http://192.168.2.180:8929 | admin панель |
| GitLab SSH | ssh://192.168.2.180:2222 | `git remote set-url origin ssh://git@192.168.2.180:2222/bks/...` |
| Container registry | 192.168.2.180:5050 | insecure HTTP, логин: `docker login 192.168.2.180:5050` |
| Router (docker-compose production) | http://192.168.2.180:4000 | primary router (с 2026-07-02) |
| LiteLLM proxy (прямой) | http://192.168.2.180:4001 | |
| vLLM Qwen3.6-35B | http://192.168.2.180:8088/v1 | large-tier локальный |
| MemGraphRAG (K3s ClusterIP) | http://memgraphrag.memgraphrag.svc:8000 | только внутри K3s |
| Whisper STT | http://192.168.2.180:10301 | |

---

## Пути к ключевым файлам

```
/home/admin/servers/gitlab/
  docker-compose.yml          — GitLab CE + runners (версии запинены)
  register-runner.sh          — регистрация CPU runner
  register-gpu-runner.sh      — регистрация GPU runner (ждёт токена)

/home/admin/servers/k3s/
  install.sh                  — установка K3s (запустить с sudo)
  registries.yaml             — insecure mirror для 192.168.2.180:5050

/home/admin/projects/nemohermes_bks/bksamotsvety/
  deploy/.env                 — локальная копия секретов (gitignored); canonical → GitLab CI/CD Vars
  deploy/setup.sh             — первичный деплой sandbox (source deploy/.env перед запуском)
  deploy/sync-profiles.sh     — синхронизация профилей в sandbox
  deploy/start-gateways.sh    — запуск per-profile Telegram-шлюзов
  profiles/*/config.yaml      — конфиги Hermes-профилей
  git remote origin           → GitHub rndkrkn-boop/bksamotsvety (source-of-truth)
  GitLab mirror               → http://192.168.2.180:8929/bks/bksamotsvety (pull, PAT)

/home/admin/projects/nemohermes_bks/router/
  docker-compose.yml          — текущий прод (docker); vllm-classifier с --enable-lora
                                 + volume /home/admin/models/adapters:/adapters:ro
  .env                        — NVIDIA_API_KEY + ANTHROPIC_API_KEY + SECURITY_MODEL
  classifier.py                — SECURITY_MODEL, security_check(), _SECURITY_RESULT_RE
                                 (парсит эхо-заголовок LEVEL|CATEGORY|REASON от LoRA)
  .githooks/pre-push          — eval gate (git config core.hooksPath .githooks)
  eval/gate.py                — качественный регрессионный гейт
  deploy/                     — ⚠ ВСЁ УДАЛЕНО из K8s (сохранено как документация GB10-обхода)
    00-namespace.yaml         — K3s namespace bks-router (пуст с 2026-07-03)
    01-vllm-classifier.yaml   — ⚠ УДАЛЁН из K8s 2026-07-03, перенесён в docker-compose
    02-router.yaml            — ⚠ УДАЛЁН из K8s 2026-07-02 (сохранён как документация)

/home/admin/projects/nemohermes_bks/MemGraphRAG/deploy/
  qdrant-k3s.yaml             — namespace memgraphrag + Qdrant PVC + Deployment
  memgraphrag-k3s.yaml        — MemGraphRAG Deployment (registry URL уже проставлен)
```

---

## Phase 5 — S2L адаптеры

### Security адаптер — ✅ реально в продакшне с 2026-07-03

`security-lora-v1` (eval\_loss=0.024, token\_acc=98.9%) загружен в
docker-compose `vllm-classifier` (хост, не K3s — см. §2.5 ARCHITECTURE.md).
`SECURITY_MODEL=security-lora-v1` задан в `router/.env`, `security_check()`
реально выполняется на каждом `model=auto` запросе (проверено сквозным
тестом через `localhost:4000` — PII-запрос корректно получил `3|pii`).

До 2026-07-03 адаптер числился "в продакшне" в этом файле, но фактически не
выполнялся: `SECURITY_MODEL` был пуст в реальном `.env` (переменная была
задана только в удалённом K8s Deployment), а K3s-инстанс с загруженным
адаптером не имел `Service` в кластере и был недостижим. Заодно найден и
исправлен баг: `security-lora-v1` эхом повторяет заголовок
`LEVEL|CATEGORY|REASON` перед реальным ответом — `classifier.py` парсил
`raw.split("|")[0]` и получал буквально `"LEVEL"` вместо цифры уровня риска
→ всегда `parse_error`. Заменено на `_SECURITY_RESULT_RE.search()`
(regex-поиск первого `[0-3]|category|reason` в тексте, а не парсинг с начала
строки).

Риск-события → `log.warning` + метрика `bks_router_security_events_total{level, category}`.

### PII cleaner адаптер ✅ в продакшне (оффлайн-инструмент)

`pii-cleaner-lora-v1` (eval\_loss=0.034, token\_acc=99.1%) загружен в
docker-compose `vllm-classifier` вместе с security-lora-v1. В горячий путь
`classifier.py` не встроен (нет вызовов в коде) — используется только как
оффлайн-инструмент редакции PII через прямой запрос к vLLM.
Адаптер: `/home/admin/models/adapters/pii-cleaner-lora-v1/`

Использование (офлайн-редакция PII):
```bash
# Модели теперь на хосте, не в K3s:
docker exec router-proxy curl -s http://vllm-classifier:8000/v1/models
# видны Qwen/Qwen3.5-0.8B, security-lora-v1, pii-cleaner-lora-v1
```

### Classifier LoRA (будущее)

После накопления `query_log.jsonl` трафика — переобучить классификатор tier на реальных данных:
```bash
cd router/training
python3 train_adapter.py --adapter-name classifier-lora-v2 \
    --train train.jsonl --val val.jsonl --epochs 3 --lora-rank 16
```

Откат: `cd router && docker compose up -d --force-recreate vllm-classifier`
(вернуть прежний `docker-compose.yml`/адаптер перед пересозданием — K3s
`kubectl rollout undo` больше не применим, сервис не в K3s).
