# Агентная платформа BKS v2 — пакет проектной документации

Дата исходного пакета: 2026-07-06; актуализация: 2026-07-21. Цель —
безотказная, масштабируемая прод-система агентов
Hermes для «БайкалКварцСамоцветы» с накоплением опыта (память + траектории)
и новыми доменами (оборудование, оргструктура).

Текущая приёмочная база — [полный аудит 2026-07-21](../audit/full-project-audit-2026-07-21.md):
**Gate 0 NOT MET / NO-GO**. Production-контур: 9 профилей, Compose +
OpenShell, integrated gateway dispatch, MCP memory и уже развёрнутый
monitoring. Главные gaps: 2 gateway при контракте 3, отсутствие sandbox
autorecovery и Telegram E2E, current backup FAIL (требуется 8 артефактов).
Phase 2 trajectories/server-side ACL и Phase 3 OT/PII — будущая готовность,
не дополнительные blockers текущего решения.

| # | Документ | Одной строкой |
|---|---|---|
| 00 | [Видение и требования](00-vision-and-requirements.md) | цели G1–G8, FR/NFR, принципы, ограничения |
| 01 | [Аудит текущего состояния](01-current-state-audit.md) | исторический snapshot 2026-07-06; superseded текущим полным аудитом |
| 02 | [Целевая архитектура](02-target-architecture.md) | 4 плоскости, топология sandbox'ов, матрица ролей, обоснование решений |
| 03 | [Оркестрация](03-orchestration-spec.md) | доски, диспетчер, контракты карточек, субагенты, review-required |
| 04 | [Память](04-memory-spec.md) | 4 слоя: контекст → MEMORY.md → граф → Trajectory Memory (MATM) |
| 05 | [Надёжность и масштабирование](05-reliability-and-scaling.md) | supervision, monitoring, watchdog/cron ownership, backup set из 8 артефактов |
| 06 | [Домены](06-domain-equipment-and-org.md) | оборудование (паспортизация/интеграция/настройка, OT-контур) и оргструктура (PII-контур) |
| 07 | [Roadmap](07-roadmap.md) | Gate 0 remediation и будущие gaps Phase 2–4 |

Связанные документы текущего состояния: [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`../../ARCHITECTURE_MERMAID.md`](../../ARCHITECTURE_MERMAID.md),
[`../../bksamotsvety/README.md`](../../bksamotsvety/README.md) и
[аудит 2026-07-21](../audit/full-project-audit-2026-07-21.md).
