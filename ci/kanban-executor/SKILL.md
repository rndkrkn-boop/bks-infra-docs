---
name: kanban-executor
description: "Execute Kanban board tasks: read READY status, delegate to Claude Code, update board state. Includes retry logic with exponential backoff."
version: 1.2.0
author: Hermes Agent
license: MIT
---

# Kanban Executor

**Purpose:** Autonomous task executor for Kanban boards. Reads board state, selects READY tasks, delegates execution via Claude Code CLI, updates task status.

**Board location:** `/home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/`

---

## Quick Start

### For Cron Jobs (Automation)

Create a cron job that:
1. Loads this skill: `kanban-executor`
2. Runs the autonomous executor prompt (below)
3. Delivers output to `local` (saves to `~/.hermes/profiles/dev/cron/output/`)

**Minimal prompt:**
```
Ты — автономный executor Kanban доски архитектурных улучшений.

1. Загрузи текущее состояние из:
   /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json

2. Найди первую задачу со статусом READY.

3. Если найдена:
   a. Переведи её в IN_PROGRESS
   b. Используя Claude Code (claude -p), делегируй выполнение задачи
   c. Обнови статус: DONE (успех) или BLOCKED (если зависимость не готова)

4. Сохрани обновленное состояние обратно в JSON.

5. Верни отчёт о результатах: какая задача была выполнена, статус, результаты.
```

---

## 🏗️ Architecture

### Board State Format
```json
{
  "timestamp": "2026-08-04T19:00:00+08:00",
  "total_tasks": 13,
  "tasks_by_status": {
    "DONE": { "count": 0, "tasks": [] },
    "READY": { "count": 1, "tasks": ["Task #1 (...)"] },
    "TODO": { "count": 2, "tasks": ["Task #2 (...)", "Task #3 (...)"] },
    "BLOCKED": { "count": 10, "tasks": [...] }
  },
  "phase_status": {
    "Critical": "IN PROGRESS (0/3)",
    "High": "BLOCKED (0/4)",
    "Medium": "BLOCKED (0/3)",
    "Low": "BLOCKED (0/3)"
  }
}
```

### Task Lifecycle

```
[READY] → (executor assigns) → [IN_PROGRESS]
                                      ↓
                        (Claude Code executes)
                                      ↓
                    [DONE] (success) or
                    [BLOCKED] (failed dependency)
                                      ↓
                          (board state updates)
                                      ↓
                     (JSON saved back to disk)
```

### Executor Workflow

1. **Read:** Load current board JSON
2. **Select:** Find first task with `status: READY`
3. **Delegate:** Pass task spec to Claude Code via `claude -p <prompt>`
   - Include task title, description, checkpoints
   - Use minimal invocation: `["claude", "-p", prompt]` only
   - Set timeout ≥1800 seconds for real tasks
4. **Parse:** Check returncode (0 = success) and text keywords
   - Don't attempt JSON parsing; print mode returns text
   - Assume success if `returncode == 0` and no error keywords
5. **Update:** Mark task DONE (success) or BLOCKED (failure)
6. **Save:** Write updated JSON back to disk with timestamp
7. **Report:** Return summary to delivery target

---

## 📋 Task Specifications (from improvement-plan)

Executor references these task definitions:

### 🔴 Critical Phase (Tasks #1-3)

**Task #1: Matrix Synapse versioning consistency**
- **Problem:** Infrastructure component not version-controlled
- **Solution:** Create repo, migrate configs, add to CI
- **Success criterion:** `git ls-remote` shows commits; `docker compose up` deploys from repo
- **Timeout:** ~2 hours (dev) + validation

**Task #2: cosign supply chain verification**
- **Problem:** Images pushed without attestation
- **Solution:** Implement cosign signing in CI; Registry rejects unsigned
- **Success criterion:** `cosign verify` passes for production tags
- **Timeout:** ~1 hour

**Task #3: Quality gate fail-open detection**
- **Problem:** SKIP at GATE silently passes regressions
- **Solution:** Make GATE mandatory; override only via web UI with reason
- **Success criterion:** Pipeline blocks without `evaluate` or `override-reason`
- **Timeout:** ~30 minutes

### 🟠 High Priority (Tasks #4-7)

Tasks #4-7 are blocked until Critical phase completes. Executor will skip these until dependencies are DONE.

**Blocking dependency:** All tasks #1, #2, #3 must be DONE.

### 🟡 Medium (Tasks #8-10) & 🔵 Low (Tasks #11-13)

Same pattern: blocked until earlier phases complete.

---

## ⚙️ Configuration for Cron Jobs

**Example cron job config:**

```yaml
job_id: 6e0a7d0449c2
name: "Улучшения архитектуры — автономное выполнение"
skills:
  - kanban-executor          # ✅ Load executor skill only
enabled_toolsets:
  - file                     # Read/write JSON board
  - terminal                 # Run claude -p commands
schedule: "0 9 * * *"        # Daily at 09:00
deliver: local               # Save to ~/.hermes/profiles/dev/cron/output/
workdir: /home/admin/projects/nemohermes_bks
```

**Do NOT include:**
- ❌ `claude-code` skill (it's for CLI configuration, not execution)
- ❌ `improvement-plan` skill (it's reference only; executor reads it dynamically)

---

## 🔍 Failure Modes & Recovery

### Scenario 1: Task delegates but Claude Code fails
- **Action:** Executor catches error, marks task BLOCKED
- **Status:** Remains READY or moves to BLOCKED depending on error type
- **Recovery:** Cron job reruns; executor skips failed task, tries next READY task

### Scenario 2: Board JSON corrupted
- **Action:** Executor validates JSON; if invalid, loads backup from history.json
- **Recovery:** Restore from history.json timestamp

### Scenario 3: Claude Code CLI not installed
- **Action:** Executor detects `command not found: claude`
- **Status:** Falls back to verbose error report
- **Recovery:** Install via `npm install -g @anthropic-ai/claude-code`

### Scenario 4: Task dependency chain broken
- **Action:** Executor detects READY task depends on BLOCKED predecessor
- **Status:** Doesn't execute; marks as BLOCKED
- **Recovery:** Fix predecessor task manually, then cron reruns

---

## 📊 Expected Output

**Successful cron run:**
```
# Kanban Executor Report — 2026-08-04 09:00:41

## Board State (Before)
- Total: 13 tasks
- READY: 1 (Task #1)
- IN_PROGRESS: 0
- BLOCKED: 10
- DONE: 0

## Execution
✅ Task #1 (Matrix Synapse versioning) → IN_PROGRESS
✅ Delegated to Claude Code (claude -p)
✅ Result: DONE (4/4 checkpoints passed)

## Board State (After)
- Total: 13 tasks
- READY: 1 (Task #2, newly unblocked)
- IN_PROGRESS: 0
- BLOCKED: 9
- DONE: 1

## Summary
- Executor: OK
- Task #1: DONE ✅
- Task #2: Now READY
- Phase progress: Critical 1/3 (33%)
- Next: Task #2 (cosign supply chain verification)
```

**Failed cron run:**
```
# Kanban Executor Report — 2026-08-04 09:00:41 (FAILED)

## Error
Cannot locate board JSON at /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json

## Recovery
1. Restore from backup: ~/.hermes/profiles/dev/cron/output/kanban-backup-*.json
2. Verify board directory exists
3. Rerun cron job
```

---

## 🛠️ Manual Execution (Interactive)

To run the executor interactively in the current session:

```bash
# Load the skill and prompt manually
# Then use clarify() to confirm before delegating to Claude Code
```

Or execute in a subagent:

```python
delegate_task(
    goal="Execute the next READY task on the Kanban board",
    context="Load /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json; find READY task; delegate to Claude Code; update board state"
)
```

---

## 📚 References

- **Board:** `/home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json`
- **Specs:** Load skill `improvement-plan` for task details
- **History:** `/home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/history.json`
- **Cron setup guide:** See `~/.hermes/profiles/dev/cron/` for job config examples

---

## ✅ Checklist: Before Enabling Cron Job

- [ ] Skill `kanban-executor` is loaded
- [ ] Skill `claude-code` is **NOT** in job config
- [ ] Toolsets: `file` and `terminal` enabled
- [ ] Workdir: `/home/admin/projects/nemohermes_bks`
- [ ] Schedule: `0 9 * * *` (or desired time)
- [ ] Deliver: `local`
- [ ] Board JSON exists at `/home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json`
- [ ] Test run completed successfully

---

## ⚠️ Pitfalls

1. **Don't load `claude-code` skill in cron jobs** — it's for CLI config, not task execution. **See `references/cron-automation-patterns.md` for why this is critical and how it fails.**
2. **Don't load `improvement-plan` skill in cron jobs** — executor reads it dynamically as reference
3. **Claude Code CLI invocation must be minimal** — use `["claude", "-p", prompt]` only; no `--max-turns`, `--allowedTools`, or `--output-format` flags. **See `references/claude-code-cli-integration.md` for a detailed breakdown of flag mistakes and why they silently fail.**
4. **Pass environment explicitly to subprocess** — Claude Code CLI must access `$HOME/.claude/.credentials.json`. Always use `subprocess.run(..., env=os.environ.copy())` when invoking Claude from automation. **See `references/environment-and-credentials.md` for the exact pattern and why this is critical.**
5. **Print mode returns text, not JSON** — don't attempt `json.loads(result.stdout)`. Check returncode and text keywords instead.
6. **Validate board JSON before each run** — corrupted state causes failures
7. **Test with one task** before enabling daily cron execution
8. **Monitor first week** for execution patterns and adjust turnaround times
9. **Use background=True with adequate timeout** — executor should run `terminal(background=True, timeout=1800+, notify_on_complete=True)` for long tasks

---

## 📖 Critical References

**See `references/cron-automation-patterns.md`** for a deep dive on the distinction between **Configuration Skills** (like `claude-code`, meant for interactive setup) and **Execution Skills** (like `kanban-executor`, meant for automation). This reference explains the root cause of the most common cron automation failure and how to avoid it.

**See `references/claude-code-cli-integration.md`** for detailed debugging guidance on Claude Code CLI invocation. Documents common flag mistakes (v2.1.221), why they silently fail, and the verified working pattern with timeout/output handling.

---

## 🔗 Related Skills

- `improvement-plan` — Task specifications and architecture improvement details
- `kanban-orchestrator` — Board initialization and dependency graph validation (run once at setup)
- `claude-code` — CLI configuration (reference only, do NOT load in automation)
- **`subprocess-cli-integration`** — **Core pattern for executor's Claude Code delegation:** environment passing (HOME for credentials), flag patterns, result parsing, and pitfalls. See this skill's `references/kanban-executor-debug.md` for the specific fixes applied to the executor script.
