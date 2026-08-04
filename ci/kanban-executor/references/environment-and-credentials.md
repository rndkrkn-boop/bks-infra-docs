# Environment Variables & Credentials for Claude Code Subprocess Invocation

## The Problem (2026-08-04 Test Run)

When `executor.py` invoked Claude Code via `subprocess.run()` without explicitly passing environment variables, the subprocess inherited a **clean/minimal environment**. Claude Code CLI could not locate user credentials stored in `~/.claude/.credentials.json`, resulting in:

```
ERROR: ⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY 
or another auth source is set and takes precedence over your claude.ai login · 
Unset it to load your organization's connectors
```

**Root Cause:** Claude Code looks for credentials in the user's home directory (`$HOME/.claude/`). Without `HOME` explicitly set, subprocess was using a default or missing home path.

---

## ✅ Solution: Explicit Environment Passing

### Correct Pattern

```python
import os
import subprocess

# Copy parent's environment
env = os.environ.copy()

# Ensure HOME points to the actual user directory
env['HOME'] = '/home/admin'

# Pass environment to subprocess
result = subprocess.run(
    ["claude", "-p", prompt],
    cwd="/home/admin/projects/nemohermes_bks",
    capture_output=True,
    text=True,
    timeout=3600,
    env=env,  # ← Critical: explicit environment
)
```

---

## Why This Happens

### Without `env=env` Parameter

```python
# ❌ Default behavior: subprocess inherits minimal environment
result = subprocess.run(
    ["claude", "-p", prompt],
    capture_output=True,  # stderr/stdout captured
    text=True,
    timeout=3600,
    # NO env= parameter
)

# Claude subprocess runs with:
# - Limited PATH (may not find claude CLI)
# - HOME may be undefined or set to /
# - No user-specific config directories
# → Claude can't access ~/.claude/.credentials.json
# → Fails with "auth source not found"
```

### With `env=env` Parameter

```python
# ✅ Correct: pass parent's environment, ensuring HOME is set
env = os.environ.copy()
env['HOME'] = '/home/admin'

result = subprocess.run(
    ["claude", "-p", prompt],
    capture_output=True,
    text=True,
    timeout=3600,
    env=env,  # Subprocess inherits HOME, PATH, USER, etc.
)

# Claude subprocess runs with:
# - Full PATH (finds claude CLI)
# - HOME=/home/admin (accesses ~/.claude/.credentials.json)
# - User config loaded (credentials found)
# → Works correctly
```

---

## Impact on Automation Scripts

### Cron Jobs

If running an automation script via cron that invokes Claude Code:

```python
# In executor.py or similar automation script
import os
import subprocess

# ALWAYS pass environment when invoking Claude
env = os.environ.copy()
env['HOME'] = os.path.expanduser('~')  # or hardcode known home: '/home/admin'

result = subprocess.run(
    ["claude", "-p", prompt],
    capture_output=True,
    text=True,
    timeout=3600,
    env=env,
)
```

### Background Terminal Processes

When using Hermes `terminal(background=True)` to invoke automation:

```python
terminal(
    command="python3 executor.py",  # Which internally does subprocess.run(..., env=env)
    background=True,
    notify_on_complete=True,
    timeout=1800,
)
```

The executor script must handle environment passing internally.

---

## Debugging Checklist

If Claude Code fails in subprocess/automation context:

1. **Check if env is passed:**
   ```python
   # Do this:
   result = subprocess.run([...], env=os.environ.copy())
   # NOT this:
   result = subprocess.run([...])  # ← Missing env parameter
   ```

2. **Verify HOME is set:**
   ```python
   print(f"HOME={os.environ.get('HOME')}")  # Should be '/home/admin' or similar
   ```

3. **Check if Claude credentials exist:**
   ```bash
   ls -la ~/.claude/.credentials.json
   # Should exist and be readable by the running user
   ```

4. **Test Claude manually:**
   ```bash
   HOME=/home/admin claude --version
   # Should work; if it fails, credentials issue is broader
   ```

5. **Log the error output:**
   ```python
   if result.returncode != 0:
       print(f"stderr: {result.stderr}")
       print(f"stdout: {result.stdout[:500]}")
   ```

---

## Related Error Messages

### ✅ Success (with env=env)
```
[2026-08-04T12:51:45] Task #1 delegated to Claude Code...
[2026-08-04T12:55:36] Task #1 execution succeeded
```

### ❌ Auth Failure (without env=env)
```
ERROR: ⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY 
or another auth source is set and takes precedence over your claude.ai login
```

### ❌ Command Not Found (missing PATH in env)
```
FileNotFoundError: [Errno 2] No such file or directory: 'claude'
```

---

## Key Takeaway

**Always pass environment to subprocess when invoking CLI tools that rely on user config.**

```python
# Template for automation scripts
result = subprocess.run(
    command,
    env=os.environ.copy(),  # ← Don't forget this
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
)
```

This ensures:
- CLI tools can find executables (PATH)
- CLI tools can access user config (HOME, XDG dirs)
- Shell variables and aliases are available (if needed)

---

## References

- `references/claude-code-cli-integration.md` — Claude Code invocation flags and parsing
- `scripts/executor.py` — working implementation with environment passing
- SKILL.md → "⚠️ Pitfalls" section, item 4 (timeout and output handling)
