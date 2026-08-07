# Product Context

The platform lets production and marketing users submit work through Telegram.
Hermes profiles route tasks through kanban workers, use a tiered LLM router, and
read or write organizational memory through MemGraphRAG. OpenShell provides the
sandbox and network-policy boundary.

The audit exists because a documented architecture is not sufficient evidence
that the live system works. Acceptance focuses on the complete user path,
visible failures, recoverability, tested backups, controlled secrets, and
traceability from source commit through CI image to runtime.

Primary users are business users in Telegram and operators maintaining the
single-node production host. The acceptance report must distinguish business
functionality from future v2 work such as trajectories, OT equipment, and PII
organization analytics.
