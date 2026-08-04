# Kanban Executor Integration Guide

## Overview

The Kanban Executor is an autonomous agent that:
1. Reads the board state (`current.json`)
2. Finds the first READY task
3. Delegates execution to Claude Code CLI
4. Updates board state (READY → IN_PROGRESS → DONE or BLOCKED)
5. Saves results back to disk

---

## Setup: Replacing the Broken Cron Job

### Step 1: Remove the Old (Broken) Job

The existing job `6e0a7d0449c2` incorrectly loaded the `claude-code` skill, causing failures.

```bash
hermes cronjob remove 6e0a7d0449c2
```

### Step 2: Create New Cron Job

```bash
hermes cronjob create \
  --name "Улучшения архитектуры — Kanban Executor" \
  --schedule "0 9 * * *" \
  --skill kanban-executor \
  --prompt "Ты — автономный executor Kanban доски. Загрузи текущее состояние из /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json, найди первую READY задачу, делегируй её выполнение Claude Code (claude -p), обнови статус доски, верни отчёт." \
  --deliver local \
  --enabled-toolsets file,terminal \
  --workdir /home/admin/projects/nemohermes_bks
```

Or use the script directly:

```bash
hermes cronjob create \
  --name "Kanban Executor — Direct Script" \
  --schedule "0 9 * * *" \
  --script ~/.hermes/profiles/dev/skills/devops/kanban-executor/scripts/executor.py \
  --no-agent \
  --deliver local
```

### Step 3: Verify Configuration

```bash
hermes cronjob list | grep -A 10 "Kanban Executor"
```

Check these fields:
- ✅ `skills`: should include `kanban-executor` only
- ✅ `enabled_toolsets`: `file,terminal`
- ✅ `workdir`: `/home/admin/projects/nemohermes_bks`
- ✅ `deliver`: `local`
- ❌ `skills` should NOT include `claude-code`

---

## Example: Full Job Configuration

**File:** `~/.hermes/profiles/dev/cron/jobs.yaml` (or equivalent)

```yaml
jobs:
  - job_id: kanban-exec-v2
    name: "Улучшения архитектуры — Kanban Executor v2"
    skill: kanban-executor
    skills:
      - kanban-executor
    prompt: |
      Ты — автономный executor Kanban доски архитектурных улучшений проекта nemohermes_bks.

      1. Загрузи текущее состояние доски:
         /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json

      2. Найди первую задачу со статусом READY.

      3. Если найдена:
         a. Переведи её в IN_PROGRESS
         b. Используя Claude Code CLI (claude -p), делегируй выполнение
         c. Обнови статус: DONE (если успех) или BLOCKED (если ошибка)

      4. Сохрани обновленное состояние обратно в JSON.

      5. Верни отчёт: какая задача выполнена, статус, результаты.
    
    schedule: "0 9 * * *"           # Daily at 09:00
    repeat: forever
    deliver: local                   # Save to ~/.hermes/profiles/dev/cron/output/
    enabled_toolsets:
      - file
      - terminal
    workdir: /home/admin/projects/nemohermes_bks
    max_turns: 15                    # Prevent runaway loops
```

---

## How It Works: Step-by-Step

### 1. Cron Trigger (09:00)
```
System Clock: 09:00 UTC
    ↓
Hermes Scheduler: Check jobs.yaml
    ↓
Found: kanban-exec-v2 (enabled, schedule matches)
    ↓
Load skill: kanban-executor
    ↓
Execute prompt with LLM agent
```

### 2. Agent Execution
```
Agent reads:
  - Board JSON from disk
  - Task specs (from improvement-plan skill)
  - Current statuses

Agent selects:
  - First task with status: READY
  - Example: Task #1 (Matrix Synapse versioning)

Agent delegates:
  - Runs: claude -p "Execute Task #1: ..."
  - Waits for completion (timeout: 1 hour)
  - Captures result JSON

Agent updates board:
  - Task #1: READY → IN_PROGRESS → DONE
  - Task #2: BLOCKED → READY (dependency satisfied)
  - Recalculates phase progress

Agent saves:
  - Updated current.json
  - Backup to backups/backup-<timestamp>.json
  - Report to ~/.hermes/profiles/dev/cron/output/kanban-exec-v2-<timestamp>.md
```

### 3. Results

**If successful:**
```json
{
  "timestamp": "2026-08-04T19:00:00+08:00",
  "tasks_by_status": {
    "DONE": { "count": 1, "tasks": ["Task #1 (...)"] },
    "READY": { "count": 1, "tasks": ["Task #2 (...) newly unblocked"] },
    "IN_PROGRESS": { "count": 0, "tasks": [] },
    "BLOCKED": { "count": 9, "tasks": [...] }
  },
  "phase_status": {
    "Critical": "IN PROGRESS (1/3)",
    "High": "BLOCKED (0/4)",
    ...
  }
}
```

**If failed:**
- Task remains IN_PROGRESS or reverts to BLOCKED
- Board saved with error status
- Report includes error details
- Cron job scheduled to retry next day

---

## Manual Testing

### Test 1: Check Board State
```bash
cat /home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json | jq .tasks_by_status
```

Expected output:
```json
{
  "DONE": { "count": 0, "tasks": [] },
  "READY": { "count": 1, "tasks": ["Task #1 (Matrix Synapse versioning)"] },
  "TODO": { "count": 2, "tasks": [...] },
  "BLOCKED": { "count": 10, "tasks": [...] }
}
```

### Test 2: Run Executor Directly
```bash
# Using the Python script
python3 ~/.hermes/profiles/dev/skills/devops/kanban-executor/scripts/executor.py

# Or via Hermes in current session
cd /home/admin/projects/nemohermes_bks
# (prompt user to load kanban-executor skill and run executor)
```

### Test 3: Verify Claude Code CLI
```bash
which claude
claude --version
claude auth status --text
```

Expected:
```
/usr/local/bin/claude (or similar)
claude 2.x.x
Logged in as: <user@example.com>
```

---

## Troubleshooting

### Issue: Cron job says "No READY tasks found"

**Cause:** All tasks are either DONE, IN_PROGRESS, or BLOCKED with unsatisfied dependencies.

**Solution:**
```bash
# Check current board state
cat ~/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json | jq .tasks_by_status.READY

# If empty, verify dependencies:
cat ~/.hermes/profiles/dev/kanban/boards/architecture-improvements/dependencies.yaml
```

### Issue: Claude Code times out

**Cause:** Task execution takes longer than 1 hour, or Claude Code is slow.

**Solution:**
1. Increase timeout in executor config (change `timeout=3600` to `timeout=7200`)
2. Check Claude Code auth: `claude auth status`
3. Try simpler task first (Task #1 rather than Task #4)

### Issue: Board JSON corrupted

**Cause:** Executor crashed mid-update, left JSON invalid.

**Solution:**
```bash
# Restore from backup
ls ~/.hermes/profiles/dev/kanban/boards/architecture-improvements/backups/
# Restore latest valid backup
cp backups/backup-<latest>.json current.json
```

### Issue: Claude Code returns error "command not found"

**Cause:** Claude Code CLI not installed.

**Solution:**
```bash
npm install -g @anthropic-ai/claude-code
claude doctor  # Health check
```

---

## Monitoring

### Daily Checks

After each cron run, check:

```bash
# Latest report
ls -lah ~/.hermes/profiles/dev/cron/output/kanban-* | tail -5

# View report
cat ~/.hermes/profiles/dev/cron/output/kanban-exec-v2-<timestamp>.md

# Check board progress
cat ~/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json | jq '.phase_status'
```

### Expected Output (First Week)

**Day 1:** Task #1 DONE, Task #2 READY (1/3 Critical)
**Day 2:** Task #2 DONE, Task #3 READY (2/3 Critical)
**Day 3:** Task #3 DONE, Tasks #4-7 READY (3/3 Critical, High phase starts)
**Day 7:** ~4-5 tasks completed, first High priority task in progress

---

## Advanced: Custom Prompts

Instead of the standard prompt, you can customize based on urgency or context:

### Aggressive (Run as many tasks as possible)
```
Ты — агрессивный executor. Выполняй как можно больше задач за один день:
1. Найди первую READY задачу
2. Выполни её (максимум 2 часа)
3. Если задача завершена за < 1 часа, найди следующую READY и выполни её
4. Повтори до timeout (6 часов) или до конца доски
```

### Conservative (One task per day, with validation)
```
Ты — консервативный executor. Одна задача в день, полная валидация:
1. Найди первую READY задачу
2. Выполни её с максимальной тщательностью
3. Валидируй все checkpoints вручную
4. Обнови доску и создай detailed report
5. Верни отчёт для manual review перед следующей задачей
```

---

## Integration with Other Skills

### Combining with `improvement-plan`
```
Load skills: kanban-executor, improvement-plan
Prompt: "Используя skill improvement-plan, выполни следующую READY задачу из Kanban доски"
```

### Combining with `systematic-debugging`
```
Load skills: kanban-executor, systematic-debugging
Prompt: "Если задача упадёт, используй systematic-debugging для root cause analysis"
```

---

## References

- **Executor Skill:** `/home/admin/.hermes/profiles/dev/skills/devops/kanban-executor/`
- **Board:** `/home/admin/.hermes/profiles/dev/kanban/boards/architecture-improvements/current.json`
- **Task Specs:** Load skill `improvement-plan`
- **Cron Docs:** https://claude-code.nousresearch.com/docs/cronjob
