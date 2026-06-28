# nemohermes_bks

Монорепо enterprise-развёртывания Hermes-агентов на базе NVIDIA NemoClaw /
OpenShell для клиента «БайкалКварцСамоцветы», плюс инфраструктура, которая
это обслуживает (свой LLM-роутер, графовая память, шаблоны политик безопасности).

Полная схема взаимодействия всех частей: [`ARCHITECTURE.md`](./ARCHITECTURE.md)
(ASCII) / [`ARCHITECTURE_MERMAID.md`](./ARCHITECTURE_MERMAID.md) (блок-диаграммы).

## Состав

| Папка | Что это | Документация |
|---|---|---|
| [`bksamotsvety/`](./bksamotsvety/) | Прод-деплой: 1 sandbox `bks-production`, 8 профилей Hermes-агентов | [README](./bksamotsvety/README.md) |
| [`router/`](./router/) | Свой LLM-роутер: classifier (Qwen3.5-0.8B) + LiteLLM, выбор tier по сложности задачи | [README](./router/README.md) |
| [`MemGraphRAG/`](./MemGraphRAG/) | Сервис графовой памяти (episodes + retrieval) | [README](./MemGraphRAG/README.md) |
| [`sandbox-templates/`](./sandbox-templates/) | Enterprise-шаблон сетевых политик OpenShell/NemoClaw — база для любого клиента, не только bksamotsvety | [README](./sandbox-templates/README.md) |
| [`NemoClaw/`](./NemoClaw/) | Апстрим NVIDIA: CLI + blueprint для запуска агентов в изолированных OpenShell-песочницах | [README](./NemoClaw/README.md) |

## С чего начать

- **Понять архитектуру целиком** → [`ARCHITECTURE.md`](./ARCHITECTURE.md) /
  [`ARCHITECTURE_MERMAID.md`](./ARCHITECTURE_MERMAID.md).
- **Деплой, K3s, обновления, ключи, eval gate** → [`DEPLOY.md`](./DEPLOY.md) ← текущее состояние + оставшиеся шаги.
- **Развернуть/обновить прод bksamotsvety** → [`bksamotsvety/README.md`](./bksamotsvety/README.md)
  (`deploy/setup.sh`, `deploy/sync-profiles.sh`).
- **Поднять/проверить LLM-роутер** → [`router/README.md`](./router/README.md)
  (`docker compose up -d`, `test_quality.py`).
- **Добавить сетевую политику для нового внутреннего сервиса или нового
  клиента** → [`sandbox-templates/README.md`](./sandbox-templates/README.md).

## Принципы (сквозные для всех частей)

- Один агент = один sandbox-под (K3s внутри OpenShell), управляется только
  через NemoClaw CLI (`nemohermes ...`).
- Креды провайдеров живут на хосте; sandbox обращается к ним через
  именованные provider-эндпоинты/`inference.local` — процесс агента ключи
  не видит (credential rewrite на egress).
- SSRF-guard блокирует приватные сети по умолчанию — каждый внутренний
  сервис (роутер, MemGraphRAG, STT) открывается точечным
  `allowed-ip`/`endpoint` правилом, не общим послаблением tier'а.
- Push в git — только у claude-code-агентов; Hermes-агенты — read-only git
  (clone/fetch) + создание MR/PR через API.
