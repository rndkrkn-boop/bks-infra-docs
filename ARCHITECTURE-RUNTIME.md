# ARCHITECTURE-RUNTIME.md — Live Verification

This document describes the actual runtime architecture and includes built-in verification to detect drift between documentation and deployed services.

## Current Architecture

**Base Host:** 192.168.2.180 (Single-host Docker Compose deployment)

### Service Topology

| Service | Port | Container | Health Check | Status |
|---------|------|-----------|--------------|--------|
| **router** | 4000 | router-proxy | GET /health | ? |
| **memgraphrag** | 8010 | memgraphrag-memgraphrag-1 | GET /health | ? |
| **monitoring** | 3000 | monitoring-grafana / monitoring-prometheus | GET / | ? |
| **gitlab** | 8929 | gitlab | GET /health | ? |
| **registry** | 5050 | (не подтверждено на этом хосте — возможно вне docker/на другом хосте) | GET /v2/ | ? |

*Status markers are updated by verify-runtime.sh. Каждый сервис — независимый
docker-compose стек в СВОЁМ репозитории (router/, monitoring/, MemGraphRAG/ —
см. .gitignore), а не один общий docker-compose.yml в корне этого репозитория,
как утверждалось ниже до 2026-08-06.*

## Component Descriptions

### 1. Router (nemohermes-router:latest)
- **Purpose:** Request routing, load balancing, request filtering
- **Port:** 4000 (HTTP)
- **Dependencies:** memgraphrag (for query routing)
- **Expected uptime:** 99.9%+
- **Logs:** `docker-compose logs router`

### 2. MemGraphRAG (memgraphrag:latest)
- **Purpose:** Memory graph storage and retrieval (RAG system)
- **Port:** 8010 (HTTP API)
- **Database:** MemGraph (in-memory)
- **Health Endpoint:** GET http://memgraphrag:8010/health
- **Expected uptime:** 99.9%+
- **Logs:** `docker-compose logs memgraphrag`

### 3. Monitoring (prometheus/grafana:latest)
- **Purpose:** Metrics collection and visualization
- **Port:** 3000 (Grafana), 9090 (Prometheus)
- **Scrapers:** docker-compose services
- **Expected uptime:** 95%+ (non-critical)
- **Logs:** `docker-compose logs monitoring`

### 4. GitLab / GitLab Runner
- **Purpose:** CI/CD orchestration, build jobs, artifact storage
- **Port:** 8929 (Web), 8988 (Runner API)
- **Volumes:** /var/gitlab/data (persistent)
- **Expected uptime:** 99%+
- **Logs:** `docker-compose logs gitlab`

### 5. Docker Registry
- **Purpose:** Private image repository (HTTP only, insecure)
- **Port:** 5050 (HTTP registry API)
- **Storage:** /var/registry/data (persistent)
- **SSL:** None (intentional — internal only)
- **Expected uptime:** 99%+
- **Logs:** `docker-compose logs registry`

## Supply-Chain: Image Build, Push, and Signing (cosign)

**Build/push of production images does NOT happen in this repository.**
`.gitlab-ci.yml` here has exactly five stages — `tests`, `docs`,
`compliance`, `backup`, `metrics` — and none of them run `docker build` or
`docker push`. There is no `build` or `deploy` stage to attach an image gate
to.

Cosign gate tooling lives in this repo nonetheless, fully implemented and
tested:
- `ci/cosign-lib.sh`, `ci/verify-image-sig.sh` — the actual verification gate
  (key-based `cosign verify` for the internal registry, keyless + narrow
  certificate-identity policy for external/base images; fails closed on any
  missing/invalid signature).
- `scripts/cosign-selftest.sh` — proves the gate fails closed, using a
  throwaway `ci/cosign-test-image/` fixture pushed to an *ephemeral local*
  registry. This is a self-test, not a production signing path.

Both are committed and self-tested, but **nothing in this repository invokes
them** — because this repository has no image pipeline to invoke them from.

*Реальная сборка + push + деплой образов router и memgraphrag происходит в
отдельных git-репозиториях `router/` и `MemGraphRAG/` (см. .gitignore — оба
заигнорены этим родительским репозиторием, у каждого свой remote и свой
`.gitlab-ci.yml` со стадией `build` на kaniko → push в GitLab Registry →
`deploy` на gb10-shell-раннере). Эти репозитории вне периметра данного
аудита. По состоянию на 2026-08-06 ни `router/.gitlab-ci.yml`, ни
`MemGraphRAG/.gitlab-ci.yml` не вызывают `ci/verify-image-sig.sh` — гейт
существует как готовый инструмент, но подключать его нужно в тех
пайплайнах, не в этом.*

**Bottom line:** the gate cannot be "connected" from within `nemohermes_bks`
— there is no build step here to gate against. Wiring
`bash ci/verify-image-sig.sh` into the deploy path is an action item for
`router/`'s and `MemGraphRAG/`'s own `.gitlab-ci.yml` pipelines, tracked in
those repositories, not here. See
`kanban/boards/architecture-improvements/current.json` item #2 for the
corrected status.

## Daily Audit-Implement Cycle: Multi-Turn Session Pattern

### Overview

The daily cycle executes approved audit findings through autonomous Claude sessions, using a **session-id + --resume** pattern to handle complex multi-file tasks that exceed single-call turn budgets.

**Script flow:** `daily-audit.sh` → (user approval) → `daily-implement-now.sh` → `scripts/claude-task-orchestrator-lib.sh` (multi-turn worker) → `daily-verify-and-commit.sh`

### Motivation: Why Session-ID / --Resume Instead of Fixed --max-turns?

Prior to 2026-08-06 (commit ea93e9f), `daily-implement-now.sh` issued a single `claude -p --max-turns 20` call per task. Complex multi-file tasks regularly hit **"Reached max turns"** and were marked as failed, despite partial edits already committed to disk (Edit/Write are real filesystem operations, not part of the conversation).

**Solution:** Start a worker session with `--session-id` (first call), continue it with `--resume` (subsequent calls). Each resume pass gets a fresh turn budget, so "hit turn limit" is no longer fatal — the session persists and can be nudged to resume.

**Key commits:**
- **ea93e9f** (🚀 Многоходовая реализация): Introduced `scripts/claude-task-orchestrator-lib.sh` with session persistence and hybrid decision logic (deterministic bash for progress checks, cheap orchestrator question-answering for blockers).
- **70ed818** (🔐 Закрыть обход прав): Fixed permission isolation — `--allowedTools` does NOT override `project settings.local.json` allow-patterns; explicit `--permission-mode dontAsk` + `--disallowedTools` required on **every call** (including --resume).

### Critical Invariants (AUDIT-206)

These rules must be maintained on **every** worker call — `--session-id` (first) and all `--resume` (subsequent) calls:

#### 1. Permission Mode: Always Deny Dangerous Operations

```bash
--permission-mode dontAsk
--disallowedTools "Bash(git commit*),Bash(git push*),Bash(curl*),Bash(wget*),Bash(sudo*)"
```

**Why separately?** Empirically verified 2026-08-06: `--allowedTools` on a single call does NOT prevent `.claude/settings.local.json` project-level allow-patterns from being stacked on top. Deny-lists override both, irrespective of where allow-patterns were declared.

**Consequence of forgetting:** git commits bypass `daily-verify-and-commit.sh` verification gate (AUDIT-005). Found and fixed in 70ed818.

#### 2. Setting Sources: Project-Only (No User Overrides)

```bash
--setting-sources project
```

This removes `.claude/settings.local.json` (accumulated interactive session allow-patterns) from the worker's context. Combined with explicit `--disallowedTools`, it closes the gap from 70ed818.

#### 3. Allowed Tools: Minimal, Tool-Specific

```bash
--allowedTools "Read,Edit,Write,Bash(ls:*),Bash(bash -n:*),Bash(python -m py_compile:*),Bash(pytest:*),Bash(npm test:*),Bash(yamllint:*),Bash(yq:*),Bash(shellcheck:*),Bash(git add:*),Bash(git rm:*),Bash(git status:*),Bash(git diff:*)"
```

- `Read,Edit,Write`: basic file ops (source of truth for implementation progress)
- `Bash(git add/rm/status/diff)`: non-mutating git inspection + staging (needed for feature work)
- **NOT included:** git commit/push, curl/wget (external comms), sudo (privilege escalation)
- Syntax checks (bash -n, python -m py_compile, yamllint, shellcheck): inline task validation
- Test runners (pytest, npm test): embedded task testing

#### 4. Must Not Inherit Flags Across Resume

**Empirical check required:** flags passed to `--session-id` call are NOT automatically inherited by subsequent `--resume` calls. They must be re-passed.

Code example (from `scripts/claude-task-orchestrator-lib.sh`, `_worker_call` function):

```bash
resume_flag=(--resume "$session_id")
[ "$is_first" = "1" ] && resume_flag=(--session-id "$session_id")

timeout "$CLAUDE_TASK_INNER_TIMEOUT" claude -p "$message" \
    "${resume_flag[@]}" \
    --permission-mode dontAsk \
    --setting-sources project \
    --allowedTools "$WORKER_ALLOWED_TOOLS" \
    --disallowedTools "$WORKER_DISALLOWED_TOOLS" \
    --output-format json \
    --json-schema "$WORKER_JSON_SCHEMA" \
    --max-turns "$CLAUDE_TASK_INNER_MAX_TURNS" \
    < /dev/null > "$out_json_file" 2>&1
```

**Every call** — whether `--session-id` or `--resume` — repeats the full permission setup.

### Worker Decision Logic (Hybrid Approach)

After each turn, the orchestrator decides:

| Status | Diff Changed | Stagnant Count | Action |
|--------|--------------|----------------|--------|
| `done` | — | — | **complete** (success, regardless of attempt count) |
| `in_progress` / `call_failed` | ✓ | < threshold (2) | **nudge** (send same prompt again with encouragement) |
| `in_progress` / `call_failed` | ✗ | ≥ threshold | **abandon** (no progress for 2+ consecutive turns) |
| `blocked` | — | — | **ask_orchestrator** (cheap question-answering, no tools, no --resume) |
| Exceeded max attempts (5) | — | — | **abandon** |

**Source of truth for progress:** `git status --porcelain` (actual filesystem state), not worker self-report.

**Orchestrator question-answering** (`ask_orchestrator_question`):
- Issued as separate, cheap `claude -p` call (no tools, no --session-id/--resume)
- **Hard rule for headless cycles:** never approve git push, PR open, Telegram/email sends, or irreversible external actions
- For reversible trade-offs: choose most conservative option

### Verification Gates (Prevent Bypass)

After **every** worker turn:

1. **Path security check** (`ci/check-changed-paths.sh`):
   - Rejects modifications to `ci/`, `k8s/`, `.gitlab-ci.yml`, `.gitignore`
   - Compares baseline (state before task start) with post-turn state
   - If baseline already violated, logs warning but only aborts on **new** violations (prevents false positives from prior dirty state)

2. **Commit security check**:
   - Worker must not create any `git commit` (only git add/rm/status/diff allowed)
   - Verified by checking `git log --oneline -1` before and after each turn
   - Abort immediately if HEAD moved

### Example: Multi-Turn Task Walkthrough

Task: "Refactor error handling in 5 Python files"

```
Turn 1 (--session-id):
  - Worker opens files, reads implementation plan
  - status=in_progress, edits 2 files, adds tests
  - orchestrator: diff_changed=1, nudge

Turn 2 (--resume):
  - Worker sees nudge, continues with remaining 3 files
  - status=in_progress, edits 3 files
  - orchestrator: diff_changed=1, nudge

Turn 3 (--resume):
  - Worker verifies all tests pass (pytest)
  - status=done, summary="All 5 files refactored, 87 tests pass"
  - orchestrator: complete ✅
```

No "Reached max turns" — task completes in 3 passes with natural stop points.

### Implementation References

- **scripts/claude-task-orchestrator-lib.sh** — All logic: `orchestrator_decide()`, `_worker_call()`, `ask_orchestrator_question()`, `run_task_via_worker_session()`
- **scripts/daily-implement-now.sh** — Calls `run_task_via_worker_session()` per approved issue
- **tests/test_task_orchestrator_logic.py** — Unit tests for pure `orchestrator_decide()` function (9 test cases covering edge cases like "done on last attempt = success, not abandon")
- **ci/check-changed-paths.sh** — Path security gate

See also: **DAILY-AUDIT-README.md** (cycle walkthrough for operators)


## Drift Detection

### What is "drift"?
Drift occurs when:
1. **Documentation says X, but runtime runs Y** (e.g., docs say port 8000, reality is 8010)
2. **Service is down but docs list it as running**
3. **Dependencies changed (e.g., memgraphrag now in pod 192.168.2.181) but docs are stale**
4. **Configuration parameters changed** (e.g., memory limits increased)

### Automated Detection
Run the verification script to detect drift:

```bash
bash ci/verify-runtime.sh
```

**What it checks:**
- All services listed in docker-compose.yml are running
- All documented ports are accessible
- Health endpoints respond
- Service versions match documentation
- No unexpected services are running

**Output:**
- ✓ All checks pass → Architecture is in sync
- ✗ Checks fail → Drift detected, investigate with `docker-compose ps`

## Maintenance

### When Documentation Should Update
1. **Service Added:** Add to docker-compose.yml AND update this file
2. **Port Changed:** Update both the YAML and this document
3. **Service Removed:** Update both files
4. **Health Endpoint Changed:** Update health check column
5. **Dependency Changed:** Update Dependencies section

### Update Workflow
```bash
# 1. Update docker-compose.yml
vi docker-compose.yml

# 2. Run verification (should fail)
bash ci/verify-runtime.sh

# 3. Update this document to match reality
vi ARCHITECTURE-RUNTIME.md

# 4. Run verification again (should pass)
bash ci/verify-runtime.sh

# 5. Commit both changes together
git add docker-compose.yml ARCHITECTURE-RUNTIME.md
git commit -m "Update: service XYZ configuration"
```

## Compliance

- **SOC 2:** Documentation matches runtime (Drift Detection requirement)
- **OWASP:** All services documented for security scanning
- **CIS Benchmark:** Asset inventory maintained in this document
- **NIST:** Configuration baseline for audit trails

## References

- **ARCHITECTURE.md** — Conceptual design and rationale
- **docker-compose.yml** — Service definitions (source of truth)
- **ci/verify-runtime.sh** — Automated drift detection
- **ci/gates.yaml** — Quality gates (deployment requirements)

---

**Last Verified:** Run `bash ci/verify-runtime.sh` to check current status.

**Maintenance Owner:** DevOps team

**Drift Detection Enabled:** ✓ Yes (Run verify-runtime.sh in CI/CD pre-deploy)
