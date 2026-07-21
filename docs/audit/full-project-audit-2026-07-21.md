# Full project audit — 2026-07-21

## Decision

**NO-GO for unconditional production acceptance up to Gate 1.**

Core services are currently reported healthy by the host watchdog, and the
router and MemGraphRAG repository test suites pass. Acceptance is blocked by:

1. the latest backup completed with an error, did not archive profiles, and
   found none of the five expected kanban databases;
2. the live sandbox reports only two supervised programs while the deployment
   contract expects three Telegram gateways;
3. the real Telegram intake → kanban → worker → result → notification path has
   no current acceptance evidence;
4. sandbox restart autorecovery is documented as absent;
5. live CI provenance, image digests, runner health, alert delivery, and
   controlled failure recovery could not be verified during this run.

This decision concerns the current production contour. Phase 2 trajectories,
server-side memory ACL, and Phase 3 OT/PII domains are readiness gaps rather
than additional blockers for the current decision.

## Scope and evidence rules

Audited repositories:

- root documentation/meta repository;
- `NemoClaw`;
- `router`;
- `MemGraphRAG`;
- `bksamotsvety`;
- `sandbox-templates`;
- `host-infra`;
- `monitoring`.

Live scope: host services, Docker/OpenShell/Hermes, watchdog, backup,
observability, GitLab/registry/runners, router, and memory.

No `.env` values, tokens, CI variable values, or chat IDs were collected.
`IMG_text.jpg` was not touched. Runtime observations take precedence over
execution logs and design documents.

## Baseline

| Repository | Branch | Revision |
|---|---|---|
| root | `main` | `caf0ea5e` |
| NemoClaw | `main` | `dd9d7162` |
| router | `main` | `ffdac7f0` |
| MemGraphRAG | `main` | `bebe01db` |
| bksamotsvety | `main` | `3b12b488` |
| sandbox-templates | `dev` | `7e0d28cf` |
| host-infra | `main` | `23533e56` |
| monitoring | `main` | `6d2324cc` |

Root status before the audit contained only untracked `IMG_text.jpg`.
The audit added this report and the required Memory Bank. Nested repository
cleanliness could not be independently rechecked because the shell execution
backend stopped returning exit statuses.

## Executed repository checks

### Router

| Check | Result |
|---|---|
| Ruff lint | PASS — 0 violations |
| Ruff format check | PASS |
| Config render/check | PASS — 6 models, all 3 tiers |
| Unit tests | PASS — 64/64 |
| Compose validation with placeholders | PASS |
| Quality-gate review | RISK — manual job; infrastructure failure yields `GATE: SKIP`, exit 0 |

### MemGraphRAG

| Check | Result |
|---|---|
| Ruff lint | PASS — 0 violations |
| Ruff format check | PASS |
| Tests | PASS — 56/56 |
| Compose validation with placeholders | PASS |
| Offline Contriever smoke | BLOCKED — strict read-only container lacked a writable PyTorch temporary directory |

The blocked smoke is not a product failure, but the production image still
needs a repeat with a writable `tmpfs` and `--network none`.

### Other repositories

Execution was blocked by the shell backend. Static CI review established:

- `bksamotsvety` CI only ShellChecks deploy scripts; there is no automated
  profile/YAML/schema/env-placeholder or E2E validation.
- `sandbox-templates` validates preset structure, but its validator succeeds
  when no presets exist.
- `host-infra` ShellChecks four scripts.
- `monitoring` validates dashboard JSON and YAML; Compose validation is only
  part of deploy, not lint.
- NemoClaw is production-relevant through `nemohermes`; config validation,
  typecheck/build, and targeted CLI/plugin Vitest checks were identified but
  could not be rerun.

## Live baseline

Observation time: 2026-07-21 13:17–13:21 UTC.

| Area | Observation | Status |
|---|---|---|
| Host | uptime 9d 6h; load 0.66/0.58/0.51 | PASS |
| Memory | 127.6 GB total; 70.5 GB available; swap unused | PASS |
| Disk | watchdog reports 15% used | PASS |
| GPU | NVIDIA GB10, driver 580.159.03 | PASS |
| Router | watchdog `router=OK`; listener on 4000 | PASS with limited evidence |
| LiteLLM | listener on loopback 4001 | UNVERIFIED HTTP |
| MemGraphRAG | watchdog `memgraphrag=OK`; listener on 8010 | PASS with limited evidence |
| Docker | watchdog `docker_containers=OK` | PASS with limited evidence |
| Sandbox | watchdog `sandbox_ready=OK` | PASS |
| Supervision | watchdog OK, but only 2 programs reported running | FAIL against 3-gateway contract |
| Kanban | watchdog liveness OK; queue totals zero | PASS liveness, no usage evidence |
| Monitoring | listeners on Grafana 3000 and Prometheus 9090 | UNVERIFIED targets/alerts |
| GitLab | listener on 8929; browser reached GitLab CE sign-in | AUTH BLOCKED |
| Registry | listener on 5050 | UNVERIFIED images/digests |
| Watchdog | metrics ~3 minutes fresh; 32,679 records; all 9 checks OK | PASS |
| Timers | watchdog and backup timers enabled | PASS |
| K3s | no 6443 listener or enablement symlink; unit still installed | PASS with systemctl unverified |

Listener presence is not treated as equivalent to authenticated HTTP health.
The watchdog provides stronger but indirect evidence for router and
MemGraphRAG.

## Backup and restore

### Current state

- `.last_backup` reports `20260721`; watchdog freshness is 18 hours.
- The latest backup log completed with **one error**.
- Profiles archive failed.
- All five expected kanban databases were absent.
- MemGraphRAG and Qdrant archives were logged as created.
- Archive inventory could not be independently corroborated.

**Result: FAIL.** Backup freshness currently masks backup completeness: a
recent but incomplete run is considered healthy.

### Historical evidence

The isolated restore test on 2026-07-09 passed for five kanban databases, nine
profiles, MemGraphRAG data, and six Qdrant collections. This proves that an
older backup was readable; it does not prove that the current backup set can
restore production.

## Documentation and contract drift

| Finding | Evidence | Severity |
|---|---|---|
| Profile count is both 8 and 9 | root README/ARCHITECTURE vs bksamotsvety list and restore log | Medium |
| DEPLOY still instructs active K3s operations | `DEPLOY.md` vs Compose-only `ARCHITECTURE.md` | High |
| Gate 0 declared reached before its own criteria | execution log shows partial E2E, no sandbox autorestart, only 2h of a 48h gate | High |
| Kanban described as daemon and gateway-integrated | roadmap vs current Hermes execution log/spec | Medium |
| Memory path described as MCP and `code_execution` | two sections of ARCHITECTURE | Medium |
| Router gate described as pre-push but implemented as manual CI | DEPLOY/ARCHITECTURE vs router CI | High |
| Secret policy says no sandbox files, but memory/router keys are host-side literals in profile env | vision vs bksamotsvety runtime contract | High |
| Watchdog spec includes stale/blocked checks removed from implementation | reliability spec vs host-infra follow-up | Medium |

Canonical current runtime architecture should be Compose + OpenShell, integrated
gateway dispatch, MCP memory access, and cron-owned stale/blocked detection.

## Acceptance matrix

| Domain | Result | Rationale |
|---|---|---|
| Core service liveness | PASS with limits | watchdog currently reports all nine checks OK |
| Repository unit quality | PASS for router/memory | 64 router and 56 memory tests pass |
| Telegram business E2E | UNVERIFIED | historical test began at a synthetic kanban card, not Telegram |
| Kanban orchestration | PARTIAL | liveness OK; queue empty; reclaim/failure/notify not retested |
| Sandbox supervision | FAIL | only two programs; restart autorecovery documented absent |
| Memory ACL | PARTIAL | MCP soft ACL; server-side per-profile ACL is Phase 2 |
| Network/security policy | UNVERIFIED LIVE | static design exists; live policy could not be inspected |
| CI/CD provenance | UNVERIFIED | GitLab requires authentication; image digests/runners not observed |
| Observability | PARTIAL PASS | fresh watchdog metrics; Prometheus/Loki/dead-man unverified |
| Backup | FAIL | current backup incomplete |
| Restore/DR | HISTORICAL PASS | 2026-07-09 isolated restore; current set not restored |
| Documentation/runbook | FAIL | material K3s, gate, and secret-contract drift |

## Risk register

| ID | Severity | Risk | Required remediation |
|---|---|---|---|
| R1 | Critical | Current backup cannot demonstrate recovery of profiles or kanban | Fix source paths and make completeness part of backup exit status/watchdog; create and restore a new complete set |
| R2 | High | Main Telegram workflow may silently fail | Run production acceptance with audit ID through intake, worker, notification, and idempotent replay |
| R3 | High | Sandbox restart leaves gateways down | Add/test startup hook or formally approved automated recovery mechanism |
| R4 | High | Expected third gateway is not supervised | Identify missing program and restore three-program contract |
| R5 | High | CI quality gate can skip on infrastructure failure | Make protected production changes fail closed or require explicit waiver approval |
| R6 | High | CI-to-runtime provenance is unknown | Record successful pipeline, commit, image digest, and running container digest |
| R7 | High | Secret requirement and implementation disagree | Define allowed host-side secret injection, minimize exposure, and test file/process permissions |
| R8 | Medium | Watchdog accepts incomplete backup as fresh | Add artifact-count and per-artifact integrity checks |
| R9 | Medium | `sandbox-templates` production repo is on `dev` | Define release branch and prove deployed policy source |
| R10 | Medium | Operational docs contain executable obsolete K3s steps | Move historical procedures out of the active playbook |

## Blocked tests

The following were not executed and must not be represented as passed:

- GitLab pipelines, variables metadata, runner status, and registry provenance:
  GitLab reached the sign-in page, but no authenticated session was available.
- Remaining repository commands and live CLI/HTTP checks: the shell backend
  returned no exit status even for `true`.
- Telegram E2E: requires an authorized business chat and test identity.
- Fault injection: prerequisites were not met because the current backup is
  incomplete and rollback could not be proven.
- Current restore: no complete current backup set was available.

Skipping fault injection was required by the approved stop criteria; stopping
MemGraphRAG or restarting the sandbox without a verified rollback would have
increased production risk rather than tested it.

## Exit criteria for retest

1. Produce a backup with all eight expected artifacts and zero errors.
2. Restore that backup in isolated paths/ports and record integrity results.
3. Re-establish three supervised gateways and prove sandbox restart recovery
   within two minutes.
4. Authenticate read-only GitLab access and capture pipeline/runner/image
   provenance without variable values.
5. Repeat the offline model smoke with `--network none` plus writable `tmpfs`.
6. Run Telegram production and marketing E2E with an audit ID and idempotent
   replay.
7. Only then run one fault at a time: gateway kill, MemGraphRAG stop, watchdog
   dead-man, and sandbox restart.
8. Update active deployment documentation and rerun docs consistency/Mermaid
   validation.

After these items, rerun the affected checks plus router/memory regression
smoke and issue a new Go/No-Go decision.
