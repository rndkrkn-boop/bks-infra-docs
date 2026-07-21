# nemohermes_bks

> [!CAUTION]
> **Статус на 2026-07-21: NO-GO** для безусловной приёмки production до
> Gate 1. Актуальные наблюдения, риски и критерии повторной проверки:
> [`docs/audit/full-project-audit-2026-07-21.md`](./docs/audit/full-project-audit-2026-07-21.md).
> Описанная ниже архитектура — проектный контракт; фактически подтверждённое
> состояние и отклонения фиксирует аудит.

Документационный meta-repo и workspace enterprise-развёртывания Hermes-агентов
на базе NVIDIA NemoClaw / OpenShell для клиента «БайкалКварцСамоцветы».
Компоненты `NemoClaw`, `router`, `MemGraphRAG`, `bksamotsvety`,
`sandbox-templates`, `host-infra` и `monitoring` — отдельные git-репозитории.

Полная схема взаимодействия всех частей: [`ARCHITECTURE.md`](./ARCHITECTURE.md)
(ASCII) / [`ARCHITECTURE_MERMAID.md`](./ARCHITECTURE_MERMAID.md) (блок-диаграммы).

## Состав

| Папка | Что это | Документация |
|---|---|---|
| [`bksamotsvety/`](./bksamotsvety/) | Прод-деплой: 1 sandbox `bks-production`, 9 профилей Hermes; контракт — 3 Telegram gateway, аудит наблюдал 2 supervised-программы | [README](./bksamotsvety/README.md) |
| [`router/`](./router/) | Свой LLM-роутер: classifier (Qwen3.5-0.8B) + LiteLLM, выбор tier по сложности задачи | [README](./router/README.md) |
| [`MemGraphRAG/`](./MemGraphRAG/) | Сервис графовой памяти (episodes + retrieval) | [README](./MemGraphRAG/README.md) |
| [`sandbox-templates/`](./sandbox-templates/) | Enterprise-шаблон сетевых политик OpenShell/NemoClaw — база для любого клиента, не только bksamotsvety | [README](./sandbox-templates/README.md) |
| [`NemoClaw/`](./NemoClaw/) | Апстрим NVIDIA: CLI + blueprint для запуска агентов в изолированных OpenShell-песочницах | [README](./NemoClaw/README.md) |

## С чего начать

- **Понять архитектуру целиком** → [`ARCHITECTURE.md`](./ARCHITECTURE.md) /
  [`ARCHITECTURE_MERMAID.md`](./ARCHITECTURE_MERMAID.md).
- **Деплой, обновления, ключи, eval gate** → [`DEPLOY.md`](./DEPLOY.md).
  K3s декомиссирован и не относится к активным операциям; при расхождении
  playbook с аудитом руководствоваться аудитом от 2026-07-21.
- **Развернуть/обновить прод bksamotsvety** → [`bksamotsvety/README.md`](./bksamotsvety/README.md)
  (`deploy/setup.sh`, `deploy/sync-profiles.sh`).
- **Поднять/проверить LLM-роутер** → [`router/README.md`](./router/README.md)
  (`docker compose up -d`, `test_quality.py`).
- **Добавить сетевую политику для нового внутреннего сервиса или нового
  клиента** → [`sandbox-templates/README.md`](./sandbox-templates/README.md).

## Принципы (сквозные для всех частей)

- Production работает в одном OpenShell sandbox `bks-production`, управляемом
  через NemoClaw CLI (`nemohermes ...`); K3s полностью декомиссирован,
  активных K3s-операций нет.
- Telegram-токены инжектируются через именованные OpenShell provider-
  эндпоинты/`inference.local`. Ключи роутера и MemGraphRAG сейчас подставляются
  на хосте литералами в `.env` профилей — это фактическое исключение из
  общего принципа credential rewrite и отдельный риск аудита.
- SSRF-guard блокирует приватные сети по умолчанию — каждый внутренний
  сервис (роутер, MemGraphRAG, STT) открывается точечным
  `allowed-ip`/`endpoint` правилом, не общим послаблением tier'а.
- Диспетчер kanban встроен в gateway (`dispatch_in_gateway: true`);
  отдельного daemon-процесса в рабочем контракте нет.
- Доступ к графовой памяти идёт через MCP-инструменты MemGraphRAG; контракт
  не требует `code_execution`.
- Push в git — только у claude-code-агентов; Hermes-агенты — read-only git
  (clone/fetch) + создание MR/PR через API.
- GitLab и GitHub для `bksamotsvety` обновляются двумя независимыми
  `git push`; автоматического зеркалирования между ними нет.
