# Claude Code CLI Integration Guide

## Overview
When delegating tasks to Claude Code from automation (cron jobs, scripts), the command-line invocation must match Claude Code v2.1.221+ API exactly. Small flag mismatches cause silent failures.

## ✅ Correct Invocation

```python
subprocess.run([
    "claude",
    "-p",           # Print mode: stream output to stdout
    prompt_text,    # Task specification
], capture_output=True, text=True, timeout=3600)
```

**That's it.** No additional flags.

---

## ❌ Common Mistakes (Debugging History)

### Mistake 1: Non-existent flags
```python
# ❌ WRONG — causes silent failure
["claude", "-p", prompt, "--max-turns", "10", "--allowedTools", "Read,Edit,Bash", "--output-format", "json"]

# ✅ CORRECT
["claude", "-p", prompt]
```

**Why it fails:** Claude Code v2.1.221 CLI does NOT support:
- `--max-turns` (CLI doesn't have a turns limit flag)
- `--allowedTools` (toolset control is not exposed at CLI level)
- `--output-format json` (print mode outputs text; no JSON flag exists)

When subprocess encounters unknown flags, it returns `returncode != 0` with empty stderr (or cryptic message). The automation script sees this as "Claude failed" with no actionable error message.

**How to catch this:** Run `claude --help` to verify flag availability before automation.

---

### Mistake 2: Expecting JSON output from print mode
```python
# ❌ WRONG — output is text, not JSON
output = json.loads(result.stdout)
if output.get("subtype") == "success": ...
```

**Why it fails:** `-p` (print mode) streams text output to stdout. It does NOT return structured JSON. Attempting to parse text as JSON raises `json.JSONDecodeError`, which cascades to a fallback that may mask the real error.

**✅ Correct approach:**
```python
# Check for success indicators in text output
output_text = result.stdout.lower()
if "error" in output_text and "failed" in output_text:
    return False
else:
    return True
```

Or, simpler: check returncode only.
```python
if result.returncode == 0:
    return True
else:
    return False
```

---

### Mistake 3: Not setting adequate timeout
```python
# ❌ Too short — Claude may be thinking
subprocess.run(..., timeout=30)  # 30 seconds fails for real tasks

# ✅ Adequate timeout
subprocess.run(..., timeout=3600)  # 1 hour is reasonable for dev tasks
```

**Why:** Claude's code synthesis and execution can take 5–30 minutes for non-trivial tasks. A 30-second timeout guarantees failure.

---

## Verified Working Invocation (2026-08-04)

```python
prompt = f"""Execute this task:

Task #{task_id}: {title}
Description: {description}

Checkpoints:
{checkpoint_list}

Work in: /home/admin/projects/nemohermes_bks
Success: All checkpoints pass, changes committed to git
"""

result = subprocess.run(
    ["claude", "-p", prompt],
    cwd="/home/admin/projects/nemohermes_bks",
    capture_output=True,
    text=True,
    timeout=3600,
)

if result.returncode == 0:
    # Success — Claude completed without error
    return True
else:
    # Failure — check result.stderr for details
    error = result.stderr.strip() if result.stderr else "Unknown error"
    log(f"Claude Code failed: {error}", "ERROR")
    return False
```

**Testing notes:**
- Tested with: `claude` v2.1.221
- Working directory: `/home/admin/projects/nemohermes_bks`
- Timeout needed: ≥1800 seconds for real execution
- Background execution: Use `subprocess.run(..., capture_output=True)` with appropriate timeout and notify on completion

---

## Integration Pattern for Automation

When calling Claude Code from a cron job or background automation:

### 1. Use explicit, minimal flags
```python
["claude", "-p", prompt]  # Just these three args
```

### 2. Set reasonable timeout
```python
timeout=3600  # 1 hour; adjust based on task complexity
```

### 3. Capture output and check returncode
```python
result = subprocess.run(..., capture_output=True, text=True, timeout=3600)
if result.returncode != 0:
    error_msg = result.stderr.strip() if result.stderr else "Unknown error"
    log(f"Claude failed: {error_msg}", "ERROR")
    return False
return True
```

### 4. Don't try to parse structured output
- Print mode returns text, not JSON
- Parse only for error keywords (`error`, `failed`) or return code
- Assume success if `returncode == 0`

### 5. Run in background with notification
```python
# From terminal() tool in automation context
terminal(
    command="python3 executor.py",
    background=True,
    notify_on_complete=True,
    timeout=1800,
)
```

---

## Error Messages (Reference)

### Correct behavior (success):
```
[2026-08-04T12:54:00] Task #1 delegated to Claude Code...
[2026-08-04T12:54:30] Task #1 execution succeeded
[2026-08-04T12:54:31] Task #1 marked DONE
```

### Correct behavior (timeout):
```
[2026-08-04T13:54:00] Task #1 delegated to Claude Code...
[2026-08-04T14:54:00] Subprocess timeout after 3600 seconds
[2026-08-04T14:54:01] Task #1 moved to BLOCKED (execution failed)
```

### Incorrect behavior (bad flags):
```
[2026-08-04T12:54:00] Task #1 delegated to Claude Code...
[2026-08-04T12:54:49] ERROR: Claude Code failed: 
[2026-08-04T12:54:50] Task #1 moved to BLOCKED (execution failed)
```
^ Empty error message = likely bad flag; check `claude --help` and simplify args.

---

## Debugging Checklist

If Claude Code integration fails:

- [ ] Run `claude --help` — verify flag syntax matches v2.1.221
- [ ] Test invocation manually in shell: `claude -p "test prompt"`
- [ ] Check `result.stderr` for cryptic error messages
- [ ] Verify working directory exists and is writable
- [ ] Ensure timeout is ≥1800 seconds for real tasks
- [ ] Log `result.stdout[:500]` to see partial output even if parsing fails
- [ ] Verify Claude CLI is installed: `which claude`

---

## Related

- `scripts/executor.py` — working implementation using this pattern
- `references/integration-guide.md` — broader Kanban executor setup
- `references/environment-and-credentials.md` — subprocess environment passing for Claude auth
- SKILL.md → "⚠️ Pitfalls" section links to this document
