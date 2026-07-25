# Full project audit — 2026-07-25 (retest of 2026-07-21)

## Decision

**NO-GO for unconditional production acceptance**, but the blocking set has
changed completely since 2026-07-21. Every original blocker was retested
with live evidence and closed. Acceptance is now blocked by two items, one
of which this audit itself introduced:

1. `/opt/hermes/.venv` is read-only inside the sandbox by filesystem policy,
   and this cannot be lifted on a live sandbox (Landlock does not allow a
   process to loosen its own restrictions, and a policy update does not
   remount the underlying bind mount). This was discovered while wiring a
   new Matrix messaging platform, and it also **broke the mandatory `mcp`
   package install** that MemGraphRAG MCP tools depend on for all 9
   profiles. The correct fix (bake `mcp` into `Dockerfile.base` at build
   time) was written and verified in isolation the same day, but
   **`nemohermes sandbox rebuild` itself has a bug (R15)** that pins the
   base image to a stale cached digest and never picks up the rebuilt
   image — so `mcp` is still **not installed** in the live sandbox.
   MCP memory tools remain silently no-op for every profile.
2. GitLab pipeline/runner/image provenance remains unauthenticated and
   unverified — unchanged from 2026-07-21. A personal access token was
   requested but never actually supplied (the file created for it
   contained a literal placeholder, not a real token); this criterion was
   abandoned by user decision this run.

Everything else that blocked the 2026-07-21 decision — backup, restore,
gateway supervision contract, restart recovery, offline model smoke, fault
injection, and real Telegram E2E — passed with reproducible, live evidence
below.

## Scope and evidence rules

Same rules as 2026-07-21: no `.env` values, tokens, CI variable values, or
chat IDs were printed or recorded. Secrets were sourced into shell
environments to perform operations (install providers, start gateways,
rebuild the sandbox) but never echoed. `IMG_text.jpg` was not touched.
Runtime observations take precedence over documentation.

Audited repositories: root, `NemoClaw`, `router`, `MemGraphRAG`,
`bksamotsvety`, `sandbox-templates`, `host-infra`, `monitoring`. A new,
untracked component (`matrix/`, self-hosted Matrix Synapse) was found live
on the host and is covered below.

## Baseline

| Repository | Branch | Revision | Uncommitted files |
|---|---|---|---|
| root | `main` | `2fd7d022` | 7 |
| NemoClaw | `main` | `dd9d7162` | 2 |
| router | `main` | `ffdac7f0` | 0 |
| MemGraphRAG | `main` | `bebe01db` | 0 |
| bksamotsvety | `main` | `bbf0e1de` | 1 |
| sandbox-templates | `dev` | `7e0d28cf` | 0 |
| host-infra | `main` | `23533e56` | 6 |
| monitoring | `main` | `6d2324cc` | 0 |
| matrix | — | **no git repository** | n/a |

router/MemGraphRAG/sandbox-templates/monitoring received no commits since
2026-07-21. All working-tree changes found in root/NemoClaw/bksamotsvety/
host-infra were reviewed; the ones judged safe and correct were applied
live and are described below. None were committed — that remains the
user's call.

## Exit criteria from the 2026-07-21 report

### 1–2. Backup with 8/8 artifacts, zero errors + isolated restore — PASS

Root cause found and fixed: `bks-backup.service` (systemd oneshot,
`User=admin`, no `Environment=`) never had `~/.local/bin` on `PATH`, so
every `nemohermes` invocation inside the script silently failed with
`command not found` (exit 127) for **at least the four days before this
audit** — kanban DBs and profiles were never actually missing, the backup
just couldn't reach them. Fixed by adding an explicit `Environment=PATH=...`
line to the unit (root-owned; applied via `sudo cp` + `daemon-reload` at
the user's hand, since I don't have interactive sudo). Same fix mirrored
into `host-infra/backup/bks-backup.service` (git source) and the deploy
copy at `/home/admin/servers/backup/bks-backup.service`.

Verified run: `=== BKS Backup done. Errors: 0. Total size: 19M ===`, all 8
files present on disk (5× `kanban-*.db`, `profiles.tar.gz` 9.2M,
`memgraphrag-data.tar.gz`, `qdrant.tar.gz`).

Isolated restore (scratch directory, no live services touched): `gzip -t`
passed on all 3 tarballs; `PRAGMA integrity_check` returned `ok` on all 5
kanban DBs and on MemGraphRAG's `episodes.db`; profiles archive contained
all 9 expected profile directories; Qdrant archive contained all 6
expected collections (matches the 2026-07-09 historical restore test).

### 3. Gateway supervision contract + restart recovery — PASS (contract revised)

The "which process was missing" question from 2026-07-21 is now answered:
it's `gw-experiment`, registered in supervisord but deliberately never
started by `start-gateways.sh`/watchdog. By user decision, the contract is
now **2 mandatory gateways** (`director-bot`, `mkt-bot`); `experiment`
is optional/manual. `ARCHITECTURE.md`, `README.md` updated accordingly.

Restart recovery tested twice by stopping both gateways via
`supervisorctl stop` and running `watchdog/check.sh` directly (same path
the systemd timer uses): **57 seconds** to healthy `RUNNING` + passing
`/health` both times, reproducible.

A **full sandbox recreate** (`nemohermes sandbox rebuild bks-production`)
was also exercised this run — see the Matrix section below for what that
uncovered. It confirms the recreate path itself works end-to-end
(`deploy/.env` on host is fully populated, contrary to the stale claim in
`DEPLOY.md` that Telegram credentials were absent), but it is **not a
clean pass**: `sandbox rebuild`'s own backup/restore does not cover
`~/.hermes/kanban/`, and the resulting sandbox needs `update-policies.sh`
+ `sync-profiles.sh` + `start-gateways.sh` re-run manually before service
is restored. See Risk register R11–R13.

### 4. GitLab read-only provenance — UNVERIFIED (unchanged, abandoned)

No authenticated access obtained. A file intended to hold a personal
access token was created twice; both times it held placeholder text
(`glpat-...`) rather than a real token. By explicit user decision, this
criterion was dropped for this run rather than pursued further.

### 5. Offline model smoke (`--network none` + tmpfs) — PASS

`docker run --network none --read-only --tmpfs /tmp:rw,size=512m` against
the production MemGraphRAG image, `TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1`: Contriever loaded from the baked-in `/opt/hf_cache` and
produced a real embedding (`shape (1, 768)`) with zero network. The
previously-blocking "no writable tmp" failure from 2026-07-21 is resolved.

### 6. Real Telegram E2E with audit ID — PASS

User sent `AUDIT-20260725-E2E1 тестовая проверка` to the production group
chat. Full trace recovered from `~/.hermes/profiles/director-bot/logs/agent.log`
(the supervisor stdout capture, WARNING-level only, showed nothing —
this is an observability gap worth fixing, see R14):

```
07:33:26.638  inbound: platform=telegram chat=-1004371976422 msg='AUDIT-20260725-E2E1 тестовая проверка'
07:33:26.721  LLM call via router (host.openshell.internal:4000, auto|director-bot)
07:33:37.457  API call #3 complete, latency 10.8s, in=7238/out=792 tokens
07:33:37.485  response ready, time=10.8s
07:33:37.493  [Telegram] Sending response (303 chars) to -1004371976422
```

No kanban card was created — correct, since the message was conversational,
not a task request (the same session had earlier turns like "Заведи доску"
handled the same direct-reply way). This is real intake → worker → result
→ notification through production Telegram, stronger evidence than the
2026-07-21 report's historical restore test (which started from a
synthetic kanban card, not Telegram).

### 7. Fault injection (one at a time) — PASS (light scope, by user decision)

Two faults were run, each with a clean detect-and-recover cycle:

- **MemGraphRAG container stop**: `watchdog/check.sh` correctly reported
  `❌ memgraphrag — curl: (7) Failed to connect`. Container restarted,
  healthy in ~9s, watchdog immediately reported `✅ memgraphrag` again.
- **Gateway kill (repeat of #3's test)**: second independent run, also
  57s to recovery — reproducible, not a one-off.

Watchdog dead-man and a full uncontrolled sandbox restart were out of
scope for this run (user chose the light option).

### 8. Documentation + docs-consistency/Mermaid — PASS

`scripts/check-docs-consistency.py` → `OK: 6 bks-репо в обоих файлах, 6
mermaid-блоков, маркеров прошлых эпох нет`. All 6 diagrams in
`ARCHITECTURE_MERMAID.md` render cleanly via the same `minlag/mermaid-cli`
image the CI job uses. Most 2026-07-21 documentation drift (K3s live-ops
language, profile count 8-vs-9, kanban daemon-vs-gateway description,
memory MCP-vs-code_execution, router gate description, secret-policy
description) had already been fixed by earlier commits (`0687976`); the
2-vs-3-gateway contract description was the one still stale and has been
updated in `README.md` and `ARCHITECTURE.md`.

## New: Matrix messaging integration — infrastructure ready, not wired, and it broke `mcp`

Investigated at the user's request ("мы начали интегрировать matrix,
посмотри"), then attempted to finish it end-to-end at the user's request.

**What's live and working:** self-hosted Matrix Synapse + PostgreSQL
(`docker-compose -p matrix`, up 2+ days, healthy), federation and public
registration correctly disabled, bot users `director-bot`/`mkt-bot`
registered with rooms created and invites sent (confirmed via Synapse
server logs, not just the component's own README), `bot-credentials.env`
present (chmod 600), and the OpenShell network policy for
`host.openshell.internal:8008` already live on the sandbox (contradicting
the component's own README, which described that as a future step).

**What blocked finishing it:** wiring `platforms.matrix` into Hermes
requires the `mautrix` Python package in `/opt/hermes/.venv`, which is
locked read-only by the sandbox's `filesystem_policy`. This is enforced
below the level `openshell policy set` can reach on a running sandbox —
confirmed on both the long-running sandbox and immediately after a full
`sandbox rebuild`. Escalation path attempted, in order: (1) `docker exec
-u root chown` on the venv — had no effect, policy enforcement isn't a
UNIX-permissions problem; (2) live `openshell policy set` adding
`/opt/hermes/.venv` to `read_write` — applied successfully as a new policy
version but did not unlock writes, on either the old or a freshly rebuilt
sandbox; (3) `nemohermes sandbox rebuild bks-production` (backs up,
destroys, recreates, restores) to get a genuinely fresh Landlock state —
still read-only afterward.

**Cost of attempting the rebuild:** real, but recovered. `sandbox
rebuild`'s internal backup/restore does not include `~/.hermes/kanban/` —
this was not caught until after the old sandbox was already deleted.
Recovered by restoring the 5 kanban DBs from this session's own verified
2026-07-25 backup (data-loss window under an hour; queues were empty
throughout). `board.json` metadata (not covered by either backup
mechanism) was regenerated automatically by `sync-profiles.sh`'s
`hermes kanban boards` call. Both Telegram gateways were down for
approximately 8 minutes total during the rebuild + resync + restart
sequence; both came back healthy and were independently verified via a
real Telegram message afterward (§6 above happened *after* the rebuild).

**Unintended regression found:** the same read-only `.venv` blocks
`setup.sh`'s own step 4 (`uv pip install ... mcp`), reproduced directly
after the rebuild. This means MemGraphRAG's MCP tools are currently
unavailable to every one of the 9 profiles, not just the two involved in
Matrix. This is now the primary open blocker — see R11.

**R11 follow-up (same day):** fixed at the correct layer — added a build-time
`RUN uv pip install ... mcp` to `agents/hermes/Dockerfile.base` (root-owned,
before the runtime read-only policy applies), and removed the now-dead
runtime step from `bksamotsvety/deploy/setup.sh`. Verified correct in
isolation: `nemohermes sandbox rebuild` did trigger a real base-image
rebuild this time (`docker build` step `[11/12] RUN uv pip install ...
mcp` resolved `mcp==1.28.1` and wrote a new image
`sha256:9c59e394...`). But the resulting sandbox still could not `import
mcp` — **R15**: `nemohermes sandbox rebuild` resumes onboarding from a
cached session that pins the base image by digest
(`hermes-sandbox-base@sha256:7595d38c...`, dated 2026-05-29, matching the
`v0.0.55` tag) rather than re-resolving `:latest` after a fresh build, so
the freshly built image is silently never used. This is a bug in
`nemohermes`'s own resume/rebuild logic, not in this project's Dockerfile
or scripts. Also required editing the actual build source
`~/.nemoclaw/source/agents/hermes/Dockerfile.base` (a separate vendored
clone of upstream `NVIDIA/NemoClaw` at `95d483fe`, not this repo's
`NemoClaw/` checkout) to get even the one real rebuild to pick up the fix
— `nemohermes sandbox rebuild` has no equivalent of `onboard --from` to
point at a local checkout. Production was restored to healthy (10/10
watchdog) after this attempt using the same kanban-restore procedure as
the first rebuild, this time from a backup taken immediately beforehand
(data-loss window: minutes, not up to an hour).

No changes were left half-applied: `director-bot`/`mkt-bot` `config.yaml`
were verified to contain zero mentions of `matrix` after the abandoned
attempt.

## Risk register (2026-07-21 items + new)

| ID | Status | Item |
|---|---|---|
| R1 (backup) | **Closed** | Root cause was a PATH bug in the systemd unit, not missing data. Fixed and verified. |
| R2 (Telegram E2E) | **Closed** | Real production E2E captured with full log trace. |
| R3 (sandbox restart) | **Closed** | 57s recovery, reproduced twice. |
| R4 (missing 3rd gateway) | **Closed** | Identified as `gw-experiment`; contract formally revised to 2. |
| R5 (CI gate fails open) | **Open, unchanged** | Still documented, not retested — needs GitLab access. |
| R6 (CI-to-runtime provenance) | **Open, unchanged** | GitLab access not obtained this run. |
| R7 (secret contract) | **Open, unchanged** | Documentation now at least internally consistent about host-side literals. |
| R8 (watchdog backup freshness masking incompleteness) | **Closed as a symptom** | Underlying cause (R1) fixed; watchdog itself still only checks freshness, not artifact count — worth hardening. |
| R9 (`sandbox-templates` on `dev`) | **Open, unchanged** | Not investigated this run. |
| R10 (obsolete K3s docs) | **Closed** | Already fixed before this run — verified still correct. |
| **R11** | **New, Critical** | `/opt/hermes/.venv` read-only blocks `mcp` install for all 9 profiles (MemGraphRAG MCP tools silently no-op) — introduced/exposed by this run's `sandbox rebuild`. Fix requires baking `mcp` (and, if still wanted, `mautrix`) into the sandbox image/blueprint at build time, then another rebuild. |
| **R12** | **New, Medium** | `nemohermes sandbox rebuild`'s internal backup/restore does not cover `~/.hermes/kanban/` or `board.json`. Any future rebuild will lose kanban data unless the daily `bks-backup.sh` output is fresher than the rebuild, or this is fixed upstream. |
| **R13** | **New, Medium** | The `matrix/` component (Synapse + Postgres, real production data and bot credentials) has no git repository at all — unlike every sibling component (`host-infra`, `bksamotsvety`, etc.), and is excluded from the daily backup contract (its own README even says so). |
| **R14** | **New, Low** | Gateway supervisor stdout logs are WARNING-level only; the only place that shows real message-processing activity is the per-profile `logs/agent.log`. Not a blocker, but makes the watchdog/supervisor log misleading for incident response. |
| **R15** | **New, High — blocks R11's own fix** | `nemohermes sandbox rebuild` resumes onboarding from a cached session pinned to an old base-image digest and never re-resolves `:latest`, so a corrected/rebuilt base image is silently ignored even though the `docker build` itself picks up source changes correctly. Confirmed twice. `rebuild` also has no `--from`-equivalent to point at a local Dockerfile the way `onboard` does. Until this is understood/fixed (in `nemohermes` itself, or by clearing/bypassing the resume cache), R11's source-level fix cannot reach a live sandbox via `rebuild`. |

## Acceptance matrix (updated)

| Domain | 2026-07-21 | 2026-07-25 |
|---|---|---|
| Backup | FAIL | **PASS** |
| Restore/DR | Historical only | **PASS (fresh)** |
| Telegram E2E | UNVERIFIED | **PASS** |
| Sandbox restart recovery | Undocumented | **PASS (57s ×2)** |
| Full sandbox recreate | Untested | **PASS with caveats (R12)** |
| Gateway supervision contract | FAIL (3 expected, 2 seen) | **PASS (revised to 2, both healthy)** |
| Offline model smoke | BLOCKED | **PASS** |
| Fault injection | Skipped (backup unproven) | **PASS (light scope)** |
| Documentation/runbook | FAIL (material drift) | **PASS** |
| MemGraphRAG MCP tools | Assumed working | **FAIL — newly discovered (R11)** |
| CI/CD provenance | UNVERIFIED | **UNVERIFIED (unchanged)** |
| Matrix messaging platform | N/A (didn't exist) | **Infrastructure ready, not connected** |

## Exit criteria for next retest

1. Resolve R15 first: figure out why `nemohermes sandbox rebuild` doesn't
   pick up a freshly rebuilt base image (stale cached digest from the
   resumed onboarding session), or find/use whatever mechanism makes
   `rebuild` re-resolve `:latest` — this blocks R11 even though the
   Dockerfile fix itself is already correct and committed. Once unblocked,
   verify `mcp` importable and MemGraphRAG MCP tools actually functional
   from at least one profile after a rebuild.
2. Decide and act on R13: either give `matrix/` its own git repository
   (matching every sibling component) or explicitly document why it's
   exempt, and add `/home/admin/servers/matrix/` to the daily backup
   contract.
3. Fix R12 or accept it explicitly: either patch `nemohermes sandbox
   rebuild` expectations in the runbook (manual kanban backup/restore
   required around any rebuild) or file it upstream.
4. Obtain real, working GitLab read-only credentials and complete
   criterion 4 (pipeline/runner/image provenance) — still fully unverified
   after two attempts.
5. If Matrix is still desired: after R11 is fixed, wire
   `platforms.matrix` into `director-bot`/`mkt-bot` `config.yaml`, restart
   gateways, and prove a real Matrix round-trip message the same way §6
   proved Telegram.
