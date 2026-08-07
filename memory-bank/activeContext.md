# Active Context

Current focus: current-state architecture and operations documentation has been
aligned with `docs/audit/full-project-audit-2026-07-21.md`. The decision remains
No-Go for unconditional production acceptance.

Audit sequence:
1. Record repository/runtime baseline and evidence rules.
2. Verify documentation contracts and all repository checks.
3. Audit GitLab CI/CD, registry provenance, runners, and secret handling.
4. Capture the live production baseline.
5. Run controlled functional, resilience, and isolated restore tests.
6. Publish evidence, findings, risk register, and Go/No-Go decision.

Primary blockers are an incomplete current backup, only two supervised
programs against the three-gateway contract, no current Telegram business E2E,
and no sandbox restart autorecovery. Live CI provenance and controlled fault
tests remain unverified because GitLab authentication was unavailable and the
shell backend stopped returning exit statuses.

Canonical documentation now states: nine profiles (three gateway contracts and
six workers), gateway-integrated kanban dispatch, MCP memory access, Compose and
OpenShell runtime with K3s decommissioned, router-based STT, manual GitLab
quality gate, and an eight-artifact backup completeness contract.

The bksamotsvety source now keeps `LITELLM_MASTER_KEY` only in an attached
OpenShell provider. Profile configs and inline HTTP skills use
`openshell:resolve:env:LITELLM_MASTER_KEY`; sync rejects secret material and
non-Router inference endpoints. Deployment and live E2E remain pending.

On 2026-07-22, Router `model=auto` failures were traced to Docker DNS:
`8.8.8.8` was unreachable and `router-proxy` could not resolve NVIDIA API
hosts. The deployed Compose checkout now pins `router.dns` to `77.88.8.8`;
the container is healthy and an OpenShell `auto` request returns HTTP 200.

On 2026-07-22, intermittent `director-bot`/`mkt-bot` silence was traced to
Hermes keeping gateway PIDs `RUNNING` after Telegram retries entered
`paused`. Host DNS was changed persistently to `192.168.2.1,1.1.1.1`, and
the live sandbox resolver was refreshed without recreating the sandbox.
The remaining network fault was localized to XKeen/Xray on the Keenetic
router: LAN, ICMP, raw TCP, host conntrack, and NIC counters were healthy, but
roughly half of TLS handshakes through the transparent proxy timed out across
Telegram, NVIDIA, and Google. Restarting XKeen did not improve a 20-request
sample. A direct-route control test was proposed but not run. Host-infra
watchdog now has a bounded tokenless `telegram_egress` check and gateway
recovery, but it cannot make an unstable proxy path reliable.

Deployment changes restart mandatory gateways even when supervisor config is
unchanged, clear stale logs before restart, pin the CI sandbox name to
`bks-production`, and explicitly enable Telegram/API server platforms for
`mkt-bot`. Live watchdog and deployment checkouts were updated; both mandatory
gateway processes remain supervised, but Telegram service is not accepted as
stable while XKeen TLS success is about 45%. Full recovery after sandbox
recreate also remains unproven because the host-side deploy `.env` does not
contain the Telegram credentials required to rebuild supervisord from scratch.

Do not expose `.env` values, tokens, chat IDs, or CI variable values in logs or
reports. Do not modify `IMG_text.jpg`.
