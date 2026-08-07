# CI/CD Pipeline Notes — Supply-Chain и Daily Audit Cycle

> Этот файл заменяет бывший `ARCHITECTURE-RUNTIME.md`. Тот файл утверждал о
> себе «**Drift Detection Enabled: ✓ Yes** (Run verify-runtime.sh in CI/CD
> pre-deploy)» и описывал единый `docker-compose.yml` в корне репозитория —
> ни то, ни другое никогда не было правдой: `ci/verify-runtime.sh` не
> вызывался ни одним job'ом `.gitlab-ci.yml`, а корневого `docker-compose.yml`
> не существует (каждый сервис — свой compose-стек в своём репозитории, см.
> `ARCHITECTURE.md`). Та часть была шаблоном, который так и не подключили к
> реальности, и удалена вместе с `ci/verify-runtime.sh`. Ниже — две секции
> старого файла с уникальным и проверенным содержанием, которого нет в
> `ARCHITECTURE.md`.

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
