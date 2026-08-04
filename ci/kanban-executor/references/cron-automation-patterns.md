# Cron Automation Patterns: Configuration vs. Execution Skills

## The Problem That Led to This Skill

**Original Issue (2026-08-04):**
Cron job `6e0a7d0449c2` was configured to load the `claude-code` skill, expecting it to run automation. Instead:

1. Agent loaded `claude-code` SKILL.md (full text: ~500 lines of documentation)
2. Entire documentation was inserted into the prompt as "context"
3. Prompt became malformed with mixed instructions + reference material
4. Agent confused; execution failed
5. Cron job status: ❌ FAILED

**Root Cause:** Conflating two different skill types:
- **Configuration skills** — meant for humans to read and CLI setup
- **Execution skills** — meant for agents to load and act on

---

## Design Principle: Skill Categories

### Configuration Skills
**Purpose:** Teach a user how to set up, configure, or troubleshoot a tool.

**Characteristics:**
- Rich documentation (examples, troubleshooting, reference)
- Text-heavy, designed for human reading
- Instructions are often descriptive ("here's why X", "when X fails, try Y")
- Meant to be called as `/skill-name` in interactive mode

**Examples:**
- `claude-code` — how to set up Claude Code CLI
- `github-auth` — how to configure GitHub credentials
- Any "setup", "configure", "install" skill

**Problem in Cron:** When loaded, the ENTIRE text gets inserted into the prompt, bloating it and confusing the agent.

### Execution Skills
**Purpose:** Provide an agent with a checklist, workflow, or task spec to execute.

**Characteristics:**
- Actionable content: task definitions, checkpoints, decision trees
- Concise, structured for agent consumption
- Instructions are procedural ("do X then Y", "if Z then Q")
- Meant to be loaded in automation via cron/delegation

**Examples:**
- `improvement-plan` — task specs for architecture improvements
- `kanban-executor` — workflow for Kanban board automation
- Any "workflow", "pipeline", "executor" skill

**Works in Cron:** Agent extracts what it needs, skill is used as reference handbook.

---

## The Rule for Cron Jobs

```
IF setting up a cron job:
  Load ONLY execution skills (describe WHAT to do)
  NEVER load configuration skills (describe HOW TO CONFIGURE tools)
ENDIF
```

**Violated rule (❌):**
```yaml
skills:
  - claude-code        # ← Configuration skill, NO
  - improvement-plan   # ← Execution skill, OK
```

Result: Cron job FAILED. Entire `claude-code` SKILL.md inserted into prompt.

**Correct pattern (✅):**
```yaml
skills:
  - improvement-plan   # ← Execution skill, OK
  - kanban-executor    # ← Execution skill, OK
  # claude-code deliberately omitted
```

Result: Agent loads task specs, executes correctly.

---

## Distinguishing the Two in Practice

### When to Load `claude-code` Skill
- Interactive session where user wants to learn/configure Claude Code CLI
- Troubleshooting Claude auth, settings, or installation
- Setting up Claude Code for the first time

**Command:**
```bash
/skill claude-code
# Then ask questions about setup/config
```

### When to Load `kanban-executor` Skill
- Automating task delegation to Claude Code (via cron)
- Running autonomous Kanban board workflows
- Scheduling recurring task execution

**In cron config:**
```yaml
skills:
  - kanban-executor
prompt: |
  Ты — executor. Загрузи board, найди READY задачу, делегируй Claude...
```

---

## How Skill Text Insertion Works (Technical Detail)

### For Configuration Skills (Text-Heavy):
```
LLM Agent loads skill → 
  Entire SKILL.md (documentation + examples + troubleshooting) 
    ↓
  Inserted into system prompt as-is
    ↓
  Prompt becomes: [original instructions] + [500 lines of docs]
    ↓
  Agent confused by mixed content → Execution fails
```

### For Execution Skills (Task-Focused):
```
LLM Agent loads skill → 
  Extracts actionable content (task specs, checkpoints, workflows)
    ↓
  Uses as reference handbook
    ↓
  Prompt remains focused: [instruction] + [task spec]
    ↓
  Agent executes clearly → Success
```

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Loading Too Many Docs
```yaml
skills:
  - improvement-plan
  - kanban-executor
  - claude-code           # ← Don't load this for automation
  - github-pr-workflow    # ← Too detailed for cron
```

**Better:** Load only the 2-3 skills needed for the specific automation task.

### ❌ Anti-Pattern 2: Mixing Setup and Automation
```yaml
# Initial setup (interactive):
/skill claude-code
# Configure auth, install, verify

# Later: Automation in cron:
skills:
  - claude-code  # ← Still wrong; now it's loaded for automation
  - kanban-executor
```

**Better:** Do setup interactively. For automation, load ONLY execution skills.

### ❌ Anti-Pattern 3: Using a Configuration Skill as a Handbook
```
Cron job prompt:
"Using the claude-code skill as a reference, delegate Task #1 to Claude Code"
```

The skill IS text-inserted; you can't selectively reference it as a handbook.

**Better:** Create a dedicated execution skill (like `kanban-executor`) that references configuration skills by name, not by loading them.

---

## Debugging Failed Cron Jobs

### Symptom 1: Cron Output Contains Full Skill Text

```
[CRON OUTPUT]
...
---
name: claude-code
description: "Configure, extend, or contribute to Claude Code"
...
[ENTIRE SKILL.md TEXT]
...
[END OF CRON OUTPUT]
```

**Diagnosis:** A configuration skill was loaded. Check cron job config.

**Fix:**
```bash
hermes cronjob list | grep -A 5 <job_id>
# Check 'skills' field: if it includes claude-code or other setup skills, remove it
hermes cronjob update <job_id> --skills kanban-executor
```

### Symptom 2: Cron Says "FAILED" Without Clear Error

**Possible cause:** Configuration skill loaded, agent got confused.

**Debug:**
```bash
cat ~/.hermes/profiles/dev/cron/output/<job_id>/<latest>.md | grep -i "error\|failed\|claude-code"
```

If you see the `claude-code` SKILL.md text in the output, you've found the problem.

**Fix:** Remove configuration skills from cron config, reload with execution skills only.

---

## Creating New Execution Skills

When designing a new skill for cron automation:

1. **Name it clearly:** Use action verbs or workflow names (`executor`, `pipeline`, `runner`)
   - ✅ `kanban-executor`
   - ✅ `github-pr-workflow`
   - ❌ `claude-code-guide` (sounds like configuration)

2. **Minimize documentation:** Keep SKILL.md focused on the actionable task
   - Include task specs, checkpoints, decision trees
   - Link to external docs via references/, don't repeat them

3. **Add `references/`:** For session-specific details, error transcripts, external knowledge
   - `references/design-patterns.md` (like this file)
   - `references/debugging-guide.md`
   - `references/api-reference.md`

4. **Use `scripts/`:** For re-runnable verification or fixture generators
   - `scripts/executor.py` (like in kanban-executor)
   - `scripts/validate.sh`

5. **Never embed full setup guides:** Link to external skills or references instead
   - ❌ "To set up Claude Code, follow these 20 steps..."
   - ✅ "Install Claude Code (see `claude-code` skill), then..."

---

## Related: Configuration Skill Best Practices

If you're updating a **configuration skill** (like `claude-code`):

1. Keep documentation rich and example-heavy
2. Include troubleshooting section
3. Add validation/health-check commands (`claude doctor`, `hermes doctor`, etc.)
4. Make it self-contained — shouldn't rely on loading other skills
5. Use `references/` for deep dives (API docs, external standards, etc.)
6. Add a warning section: "This skill is for interactive setup, not for cron automation"

Example header for config skills:
```markdown
## ⚠️ Not for Cron Jobs

This skill is designed for interactive setup and troubleshooting.
**Do NOT load this skill in cron jobs.** 
See the `kanban-executor` or `improvement-plan` skill for automation patterns.
```

---

## Summary

| Aspect | Configuration Skill | Execution Skill |
|--------|-------------------|-----------------|
| **Purpose** | Setup, learn, troubleshoot | Automate, workflow, task execution |
| **Use in interactive mode** | ✅ Yes (`/skill-name`) | ✅ Yes |
| **Use in cron jobs** | ❌ No (text bloat) | ✅ Yes |
| **Content** | Documentation, examples, troubleshooting | Task specs, checkpoints, decision trees |
| **Length** | Often 300+ lines | Concise, structured |
| **Example** | `claude-code`, `github-auth` | `kanban-executor`, `improvement-plan` |

---

## Lessons Learned

1. **Text insertion is literal:** When a skill is loaded in cron, the agent sees the entire SKILL.md as text. Don't conflate "skill that's useful for an interactive user" with "skill suitable for agent automation."

2. **Naming signals intent:** A skill with "config", "setup", "install", "guide" in the name is likely a configuration skill. A skill with "executor", "workflow", "pipeline", "runner" is likely an execution skill.

3. **Execution skills should reference, not embed:** If an automation skill needs to know how to set up a tool, it should instruct the user or provide a link — not embed the setup docs.

4. **Test before deploying:** If creating a new cron job that loads a skill, test it in a single-run mode first (not on a schedule). Check the output for skill text leakage.

5. **Subprocess environment matters:** When automation scripts invoke CLIs (like Claude Code) via subprocess, they must explicitly pass environment variables. CLI tools need `HOME`, `PATH`, and user config directories. See `references/environment-and-credentials.md`.

