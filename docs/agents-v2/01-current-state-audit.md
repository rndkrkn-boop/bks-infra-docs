# Аудит текущего состояния (2026-07-06)

Проверялась живая система, не документация. Методика: `nemohermes list/status`,
`exec` внутрь `bks-production`, `kubectl` по K3s, чтение `kanban.db`,
логи падающих подов.

## 1. Сводка: что работает, что нет

| Контур | Состояние | Детали |
|---|---|---|
| Sandbox `bks-production` | ✅ Ready | OpenShell 0.0.44, Hermes v2026.5.16, NemoHermes v0.0.55 |
| LLM-роутер (docker-compose :4000) | ✅ работает | classifier + LiteLLM + vllm-classifier (security/pii LoRA) + whisper + OCR |
| Telegram-шлюзы | ⚠️ частично | запущены только `director-bot` и `mkt-bot`; `experiment` (тоже должен слушать прод-группу) — нет |
| **Kanban-конвейер** | ❌ **никогда не работал** | `kanban.db`: 0 задач, 0 runs за всё время; `hermes kanban daemon` не запущен |
| Фоновые воркеры (7 профилей) | ❌ не запускаются | dispatcher, который должен их спавнить, не работает |
| **MemGraphRAG (K3s)** | ❌ **CrashLoopBackOff 7д20ч** | 1192 рестарта; никто не заметил |
| Qdrant (K3s) | ✅ Running | но потребитель (memgraphrag) лежит |
| nvidia-device-plugin (K3s) | ❌ CrashLoopBackOff (1502) | известная GB10-проблема; в K3s GPU больше никому не нужен — **удалить daemonset**, не чинить |
| Мониторинг / алертинг | ❌ отсутствует | отказы обнаруживаются вручную (случайно) |
| Supervision процессов sandbox | ❌ отсутствует | шлюзы стартуют `nohup ... &` из start-gateways.sh, рестартов нет |
| Бэкапы | ❌ не найдены | kanban.db, эпизоды, Qdrant, ~/.hermes — без регулярного бэкапа |
| CI/CD (GitLab) | ✅ работает | lint → test → kaniko build → registry; pre-push eval gate роутера |
| Секреты | ✅ по схеме | GitLab Variables → deploy/.env → openshell provider |

## 2. Root cause #1: kanban-конвейер не запущен как система процессов

Задумка (README bksamotsvety): director-bot создаёт карточку → «встроенный
kanban-dispatcher шлюза» запускает воркера. Фактическое устройство Hermes:

- Диспетчер — это **отдельный процесс**: `hermes kanban daemon`
  (`--interval 60 --max N --failure-limit 2 --pidfile ...`), либо разовый
  `hermes kanban dispatch`. В апстриме для него есть даже unit
  `/opt/hermes/plugins/kanban/systemd/hermes-kanban-dispatcher.service`.
- Диспетчер сам спавнит воркер-процесс на claim'нутую карточку
  (профиль = assignee), автоматически подмешивая skill `kanban-worker`
  и `KANBAN_GUIDANCE` в системный промпт. **Постоянные gateway-процессы
  фоновым воркерам не нужны** — нужен один живой daemon.
- `start-gateways.sh` поднимает только 2 Telegram-шлюза. Ни daemon, ни
  cron-джобы (`report-processor-sweep` из README — команда в документации,
  в sandbox не создана: `~/.hermes/cron` существует, но джобы не проверены
  как работающие) не входят в деплой-скрипты.

Дополнительно: **0 карточек в базе** означает, что и входная сторона
(`/report-intake` у director-bot → `kanban_create`) ни разу не доходила до
создания карточки. При включении диспетчера первым делом нужен e2e-тест
входа (реальный отчёт в Telegram) — возможно, там второй дефект
(toolset kanban в telegram-платформе, права, board по умолчанию — база
`/sandbox/.hermes/kanban.db` создана, но boards не инициализированы).

## 3. Root cause #2: MemGraphRAG не стартует — Contriever не грузится

Лог пода (повторяется каждый рестарт):

```
OSError: Can't load the configuration of 'facebook/contriever' ...
File "/app/code/src/embedding_model/Contriever.py", line 37 → AutoTokenizer.from_pretrained
RuntimeError: Cannot send a request, as the client has been closed.  (httpx — попытка сходить в сеть)
ERROR: Application startup failed. Exiting.
```

Замысел: Dockerfile запекает веса в `/opt/hf_cache` (`HF_HOME`), манифест
ставит `HF_HOME=/opt/hf_cache`, «no download at runtime». Реальность:
на старте под пытается идти в HuggingFace и падает. Гипотезы для проверки
(в порядке вероятности):

1. Итоговый образ в registry собран **без** слоя с весами (kaniko-джоба в
   CI не имеет egress к huggingface.co → bake-шаг упал/пропущен, либо
   multi-stage не копирует /opt/hf_cache в финальный stage).
2. Слой есть, но кэш неполный (нет config.json токенизатора) —
   `from_pretrained` всё равно идёт в сеть.
3. Регрессия в requirements (transformers обновился, изменился формат кэша).

Фикс и защита от регрессии — см. roadmap Phase 0: проверять образ в CI
шагом `TRANSFORMERS_OFFLINE=1 python -c "AutoTokenizer.from_pretrained(...)"`
внутри собранного образа, чтобы «offline-способность» была gate'ом сборки.

Следствие: профили structuring/research/experiment/market-monitor/analytics/
content, даже будучи запущенными, не смогут писать/читать граф — их
интеграционный код должен деградировать мягко (сейчас поведение не
определено — тоже пункт спецификации, см. 04 §6).

## 4. Root cause #3: отсутствие supervision и наблюдаемости

- Процессы в sandbox запускаются `nohup` и умирают навсегда (известный
  случай — stale `gateway.lock` после пересоздания sandbox).
- Падение K3s-пода 8 дней не замечено — probes есть, алертов нет.
- Нет единого health-скрипта «вся система за один взгляд».

Это не отдельный баг, а системное свойство: **любой** будущий контур будет
так же тихо умирать, пока нет watchdog + алертинга (см. 05).

## 5. Что уже хорошо (сохраняем как есть)

- **Слой безопасности**: OpenShell tiers + SSRF-guard + точечные allowlist
  через sandbox-templates; credential rewrite; секреты вне файлов sandbox.
- **Роутер**: tier'ы cheap/mid/large, security_check (LoRA) на каждом
  запросе, fallback на Anthropic, pre-push eval gate, unit-тесты, CI.
- **Kanban-ядро Hermes** — зрелое: atomic claim, идемпотентность
  (idx_tasks_idempotency), heartbeat, runs-журнал, failure-limit,
  workspaces (scratch/dir/worktree), tenant isolation ($HERMES_TENANT),
  review-required паттерн, notify-subscribe, gc, stats. Строить на нём.
- **Профили**: 9 штук с продуманными ролями, SOUL.md, skills; известные
  подводные камни задокументированы в README (envsubst, max_tokens,
  STT, vision через роутер).
- **Hermes-возможности, которые пока не используются**: subagents,
  checkpoints, curator (background skill maintenance), webhook,
  mcp (Hermes как MCP-сервер и клиент), внешние memory-провайдеры.

## 6. Зарегистрированные инциденты/грабли (из истории проекта)

Учтены в спецификациях, чтобы не наступать повторно:

- `read_raw_config()` не expand-ит `${VAR}` → sync-profiles.sh делает
  envsubst host-side; несовпадение имени переменной = молчаливая пустая строка.
- Reasoning-тиры Nemotron требуют щедрого `max_tokens`, иначе `content: null`.
- Cheap-tier vLLM без `--enable-auto-tool-choice --tool-call-parser` ломает
  любой диалог с tools (Hermes маскирует 400 как «Empty response»).
- Единый Telegram-бридж (`channels add`) конфликтует с per-profile шлюзами.
- Группа Telegram при апгрейде до supergroup меняет chat_id.
- Картинки не идут через текстовые тиры роутера — vision/OCR отдельными
  deployment'ами в LiteLLM.
- Старый STT-контур (:10301) декоммисирован — STT теперь через роутер.
