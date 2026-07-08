# Целевая архитектура агентной платформы BKS v2

## 1. Общий вид

Система = **4 плоскости**, каждая с собственным контрактом:

```
┌─ Плоскость входа ────────────────────────────────────────────────────┐
│  Telegram (прод-группа, mkt-группа) · hermes cron · hermes webhook   │
│  → разговорные агенты (director-bot, mkt-bot) и планировщик          │
│  → всё превращается в КАРТОЧКИ kanban                                │
└──────────────────────────────┬───────────────────────────────────────┘
┌─ Плоскость оркестрации ──────▼───────────────────────────────────────┐
│  kanban.db (per-sandbox SQLite) · boards по потокам работ            │
│  kanban-диспетчер (встроен в gateway с v2026.5.16, под supervision)  │
│  → атомарный claim → спавн воркера нужного профиля                  │
│  → heartbeat / failure-limit / review-required / links parent→child  │
│  Внутри воркера: Hermes subagents для декомпозиции                  │
└──────────────────────────────┬───────────────────────────────────────┘
┌─ Плоскость знаний (память) ──▼───────────────────────────────────────┐
│  L1 контекст+checkpoints · L2 MEMORY.md/шаблоны/skills (per-profile) │
│  L3 MemGraphRAG (граф: prod/mkt/equipment/org-pii namespaces)        │
│  L4 Trajectory Memory (банк траекторий, MATM-паттерн)                │
└──────────────────────────────┬───────────────────────────────────────┘
┌─ Плоскость инференса и инфраструктуры ───────────────────────────────┐
│  LLM-роутер :4000 (classifier→LiteLLM, tiers, security/pii LoRA,     │
│  whisper, OCR) · memgraphrag+qdrant :8010 (docker-compose) · GitLab  │
│  Supervision + watchdog + алерты + бэкапы (см. 05)                   │
└───────────────────────────────────────────────────────────────────────┘
```

Ключевой сдвиг относительно текущего состояния — **не новые компоненты,
а включение и обвязка существующих**: диспетчер kanban, supervision,
watchdog, плюс два новых элемента — Trajectory Memory (L4) и доменные
контуры (equipment/org) в отдельных sandbox'ах.

## 2. Топология sandbox'ов

Этапность: начинаем с одного sandbox, выделяем контуры по мере роста и по
требованиям безопасности (OT-сеть и PII не должны жить в общем контуре).

| Sandbox | Профили | Особые политики | Фаза |
|---|---|---|---|
| `bks-production` (есть) | director-bot, mkt-bot, experiment, report-processor, structuring, research, analytics, market-monitor, content | telegram, nous-web, internal-api (router, memgraphrag) | сейчас |
| `bks-equipment` (новый) | equipment-research, equipment-integrator | + explicit allowlist OT-хостов `IP:port` (Modbus/OPC UA/HTTP вендорского ПО), read-only git | Phase 3 |
| `bks-org` (новый) | org-analytics | + доступ к org-pii namespace графа; БЕЗ web; выход только в роутер | Phase 3 |
| `claude-code` (шаблон есть) | инженерные задачи по самой платформе | claude-code-strict + git push | по мере надобности |

Правило: **новый домен = новый профиль (или sandbox) + доска + пресет
политики** в sandbox-templates. Ядро не меняется.

### Ограничение kanban: SQLite per-sandbox

`kanban.db` шарится между профилями **внутри** одного sandbox. Между
sandbox'ами общей доски нет. Мосты между контурами:

- v1 (Phase 3): «карточка-посол» — cron-джоба в sandbox-источнике
  формирует задачу, доставка через `hermes webhook` цели (webhook
  subscriptions есть в Hermes) либо через общий каталог `share mount`.
- v2 (Phase 4, если понадобится): вынести kanban в сетевой сервис
  (у Hermes kanban — плагинная архитектура + dashboard; оценить upstream
  возможности к тому моменту, не писать своё раньше времени).

## 3. Роли агентов (уточнённая матрица)

| Профиль | Роль в конвейере | Модель (tier) | Пишет в память | Читает из памяти |
|---|---|---|---|---|
| director-bot | вход: приём отчётов/описаний от директора → карточки | auto | L2 (user profile) | L2 |
| mkt-bot | вход: задачи маркетинг-команды → карточки | auto | L2 | L2, L3(mkt) |
| experiment | планирование экспериментов, координация | auto | L3(prod), L4 | L3(prod), L4 |
| report-processor | обработка отчётов по шаблонам | mid | шаблоны(L2), L4 | шаблоны, L4 |
| structuring | карточки → сущности/связи в граф | mid | L3(prod) | — |
| research | синтез по графу + веб → гипотезы | large | L3(prod), L4 | L3(prod), L4 |
| market-monitor | мониторинг B2B-площадок | mid | L3(mkt) | L3(mkt) |
| analytics | дайджесты и аналитика | large | L4 | L3(prod+mkt), L4 |
| content | тексты для B2B/Китай | large | — | L3(prod+mkt) |
| **equipment-research** | паспортизация оборудования, поиск спецификаций | large | L3(equipment), L4 | L3(equipment), L4 |
| **equipment-integrator** | скрипты съёма данных с оборудования | mid/large | L3(equipment), L4, код в git | L3(equipment), L4 |
| **org-analytics** | анализ подотделов/сотрудников | large | L3(org-pii) | L3(org-pii), L3(prod) |
| **platform-watchdog** | health-check всей платформы → алерты | cheap | — | — |

`platform-watchdog` — намеренно самый дешёвый и простой контур: cron-джоба
(вне sandbox, на хосте — чтобы видеть смерть самого sandbox) + опционально
профиль для «умной» диагностики по алерту. Подробно в 05.

## 4. Жизненный цикл задачи (сквозной пример: отчёт директора)

```
1. Директор шлёт фото отчёта в прод-группу
2. director-bot (/report-intake): vision_analyze → уточнение → kanban_create(
     board=production, assignee=report-processor, status=triage,
     body: файл, тип, что извлечь)                      ← вход зафиксирован
3. kanban-диспетчер в gateway (tick ≤60с): claim → спавнит воркера report-processor
     с workspace dir:.../reports, skill kanban-worker, KANBAN_GUIDANCE
4. Воркер: retrieval L4 (похожие обработки) + шаблоны L2 → обработка
     → heartbeat в процессе → kanban_complete(summary, metadata={результаты})
     → комментарий с результатом → publish траектории в L4
5. Пост-хук: карточка-потомок для structuring → сущности в граф (L3 prod)
6. director-bot нотификация в Telegram: «отчёт обработан: <сводка>»
     (kanban notify-subscribe)
Сбои: воркер упал → failure-limit=2 повтора → block + алерт в Telegram;
      нет heartbeat → reclaim; sandbox перезапущен → daemon поднят
      supervisor'ом, незавершённые задачи переclaim'иваются.
```

## 5. Потоки данных между компонентами

- **Агент → LLM**: всегда роутер `:4000` (auto/явный tier). Fallback
  cloud уже в LiteLLM. Vision/OCR/STT — тоже через роутер.
- **Агент → граф**: HTTP к MemGraphRAG API (`code_execution`), с
  обязательной мягкой деградацией (04 §6). Рассмотреть оформление в
  skill `memgraph` (общий для всех профилей), чтобы код запросов не
  дублировался в каждом SOUL/SKILL.
- **Агент → траектории**: та же точка (MemGraphRAG API, новые эндпоинты
  /api/trajectories) — см. 04 §5.
- **Оркестрация → человек**: Telegram. Три класса сообщений:
  результат (в исходную группу), review-required (ссылка на карточку),
  алерт платформы (watchdog).
- **Конфигурация**: git (bksamotsvety/profiles) → sync-profiles.sh →
  sandbox. Никаких ручных правок внутри sandbox (кроме накопляемых
  агентом артефактов: templates, MEMORY.md, skills).

## 6. Технологические решения и их обоснование

| Решение | Альтернатива | Почему так |
|---|---|---|
| Оркестрация на Hermes kanban | самописная очередь / Temporal / Celery | ядро уже в рантайме агента: claim, heartbeat, спавн воркеров с инъекцией guidance; внешний оркестратор не умеет спавнить Hermes-профили без обвязки |
| Trajectory Memory поверх MemGraphRAG+Qdrant | отдельный новый сервис | инфраструктура эмбеддингов/хранения уже есть; MATM — паттерн, а не продукт (arXiv:2606.19911) |
| Supervision: systemd на хосте + supervisord в sandbox | K8s для всего | sandbox — это OpenShell/NemoClaw-объект, его процессы не видны K3s; systemd-юниты хоста рулят «нянькой», которая чинит внутренности sandbox через `nemohermes exec` |
| Watchdog как cron на хосте | Prometheus+Alertmanager сразу | сначала минимальный контур (скрипт+Telegram), Prometheus — Phase 4; важно закрыть дыру за день, а не за месяц |
| Отдельные sandbox для OT и PII | политики в общем sandbox | blast radius: компрометация web-серфящего research-агента не должна давать путь в промышленную сеть или к HR-данным |
| Продолжаем NVIDIA NIM tiers + Anthropic fallback | единая большая модель | стоимость: 80% задач конвейера — mid; large только research/analytics/content |

## 7. Что сознательно НЕ делаем сейчас

- Не выносим kanban в сетевой сервис (SQLite достаточно на одну «фабрику
  задач»; переоценка в Phase 4).
- Не пишем свой memory-провайдер Hermes для MemGraphRAG (интеграция через
  skill+HTTP проще и уже наполовину есть; official-провайдеры Hermes —
  honcho/mem0/… — не заменяют доменный граф).
- Не оставляем K3s: после переноса memgraphrag+qdrant в docker-compose
  (Phase 0) кластер пуст и декомиссируется целиком — single-node K8s без
  репликации не добавлял надёжности, только слой, прячущий отказы.
- Не строим RAG по документам как отдельную систему — документы попадают
  в граф эпизодами через structuring.
