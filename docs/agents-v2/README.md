# Агентная платформа BKS v2 — пакет проектной документации

Дата: 2026-07-06. Цель — безотказная, масштабируемая прод-система агентов
Hermes для «БайкалКварцСамоцветы» с накоплением опыта (память + траектории)
и новыми доменами (оборудование, оргструктура).

| # | Документ | Одной строкой |
|---|---|---|
| 00 | [Видение и требования](00-vision-and-requirements.md) | цели G1–G8, FR/NFR, принципы, ограничения |
| 01 | [Аудит текущего состояния](01-current-state-audit.md) | что сломано и почему (kanban не запущен, MemGraphRAG в CrashLoop 8 дней, нет supervision) |
| 02 | [Целевая архитектура](02-target-architecture.md) | 4 плоскости, топология sandbox'ов, матрица ролей, обоснование решений |
| 03 | [Оркестрация](03-orchestration-spec.md) | доски, диспетчер, контракты карточек, субагенты, review-required |
| 04 | [Память](04-memory-spec.md) | 4 слоя: контекст → MEMORY.md → граф → Trajectory Memory (MATM) |
| 05 | [Надёжность и масштабирование](05-reliability-and-scaling.md) | supervision, watchdog, бэкапы, шаблон «новый домен за день» |
| 06 | [Домены](06-domain-equipment-and-org.md) | оборудование (паспортизация/интеграция/настройка, OT-контур) и оргструктура (PII-контур) |
| 07 | [Roadmap](07-roadmap.md) | Phase 0–4 с gate-критериями, открытые вопросы, риски |

Связанные документы текущего состояния: [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`../../ARCHITECTURE_MERMAID.md`](../../ARCHITECTURE_MERMAID.md),
[`../../bksamotsvety/README.md`](../../bksamotsvety/README.md).
