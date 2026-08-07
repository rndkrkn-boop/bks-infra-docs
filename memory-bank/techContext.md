# Technical Context

- Host: Linux on NVIDIA GB10/Grace Blackwell, production address documented as
  `192.168.2.180`.
- Sandbox/runtime: NVIDIA NemoClaw, OpenShell, Hermes.
- Router: Python/FastAPI, LiteLLM, vLLM, Docker Compose.
- Memory: Python/FastAPI MemGraphRAG, Contriever, Qdrant, Docker Compose.
- Agent deployment: YAML profiles, Bash scripts, Python MCP proxy.
- Operations: GitLab CE, container registry, GitLab runners, systemd,
  Docker Compose.
- Observability: Prometheus, Grafana, Loki, Promtail, JSONL watchdog metrics.
- Repository checks include Ruff, pytest, ShellCheck, YAML/JSON validation,
  Mermaid rendering, Node/TypeScript builds, Vitest, and offline model-loading
  smoke tests.

Production testing must separate read-only observations from controlled writes
and fault injection. Fault tests require a current backup, one fault at a time,
explicit rollback, and immediate stop on data damage or failed recovery.
