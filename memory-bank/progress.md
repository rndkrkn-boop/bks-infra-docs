# Progress

## Completed

- Defined audit scope: repository plus live production.
- Chosen acceptance baseline: current production up to Gate 1.
- Approved controlled production tests in a maintenance window.
- Researched repository structure, production contours, requirements, and
  known gaps.
- Created the Memory Bank.
- Ran router checks: Ruff, config validation, Compose validation, 64 tests.
- Ran MemGraphRAG checks: Ruff, Compose validation, 56 tests.
- Captured current watchdog/host/timer/backup baseline.
- Audited documentation and requirement drift.
- Published `docs/audit/full-project-audit-2026-07-21.md`.
- Updated README, text/Mermaid architecture, deployment playbook, and active
  agents-v2 specifications to the audited runtime contract.
- Marked pre-audit state and Phase 0 execution logs as historical snapshots.
- Extended documentation anti-drift checks for profiles, K3s, MCP, ports,
  quality/test markers, gateway status, and backup completeness.
- Removed direct `LITELLM_MASTER_KEY` injection from Hermes profile configs and
  env files; added OpenShell provider attachment, credential placeholders,
  endpoint guards, and aligned inline skills. Static shell/YAML/security checks
  pass; production deployment and E2E are not yet verified.
- Restored Router-to-NVIDIA connectivity by pinning the deployed
  `router-proxy` Compose service to the reachable `77.88.8.8` resolver;
  verified `model=auto` through OpenShell returns HTTP 200.
- Diagnosed Telegram gateways that remained `RUNNING` while their adapters
  were `paused`; stabilized host DNS, refreshed sandbox DNS, restarted
  `director-bot`/`mkt-bot`, and deployed tokenless Telegram egress monitoring
  plus bounded supervisor auto-recovery.
- Updated deployment logic to restart unchanged mandatory gateways, clear
  stale logs, reject missing mandatory tokens, pin CI to `bks-production`,
  and enable the missing `mkt-bot` platform configuration. Shell syntax,
  ShellCheck, YAML parsing, and documentation checks pass. Live watchdog now
  reports Telegram egress independently; the underlying XKeen TLS path remains
  unstable and prevents production acceptance.

## Decision

No-Go for unconditional production acceptance.

## Required before retest

- Fix the current incomplete backup and restore a new complete set.
- Restore the expected third supervised gateway.
- Repair or replace the XKeen/Xray outbound: direct LAN/ICMP/TCP are healthy,
  but only about 45% of sampled TLS requests complete through the proxy.
- Prove sandbox restart autorecovery; full rebuild still needs a secure
  host-side credential source because the current deploy `.env` lacks the
  Telegram tokens available only in CI/supervisord runtime.
- Obtain read-only GitLab evidence for pipelines, runners, and image digests.
- Run the real Telegram business E2E.
- Run controlled fault tests only after backup and rollback are proven.

## Known constraints

- Production is single-node; hardware HA is outside current acceptance.
- Phase 2 trajectories/server-side memory ACL and Phase 3 OT/PII domains are
  readiness gaps, not automatic blockers for current production.
- Shell execution became unavailable during the audit; unexecuted checks are
  explicitly marked blocked rather than passed.
