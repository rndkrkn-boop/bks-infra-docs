# System Patterns

- The root repository is a documentation/meta repository. `NemoClaw`, `router`,
  `MemGraphRAG`, `bksamotsvety`, `sandbox-templates`, `host-infra`, and
  `monitoring` are independent nested repositories.
- Production is single-node. Host services run under Docker Compose; K3s is
  documented as decommissioned.
- Hermes profiles run inside the OpenShell sandbox `bks-production`.
- LLM requests flow through the classifier/router and LiteLLM. Memory requests
  flow through an MCP proxy to MemGraphRAG and Qdrant.
- GitLab pipelines build and deploy component repositories. Router and
  MemGraphRAG deployments trigger a `SYNC_ONLY` bksamotsvety pipeline.
- Host-level systemd timers supervise Docker/OpenShell and create backups.
  Grafana, Prometheus, Loki, and Promtail provide history and dead-man checks.
- Security uses deny-by-default sandbox egress, specific endpoint allowlists,
  credential rewriting, and read-only git access for Hermes.
- Router authentication is represented inside Hermes only by
  `openshell:resolve:env:LITELLM_MASTER_KEY`; the real key stays in an attached
  OpenShell provider and is resolved at the egress boundary.
- Audit precedence is runtime evidence, deployed files, CI artifacts, source
  repositories, specifications/execution logs, then architecture summaries.
