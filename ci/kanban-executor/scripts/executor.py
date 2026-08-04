#!/usr/bin/env python3
"""
Kanban Executor: Autonomous task runner for architecture improvements.
Reads board state → finds READY task → delegates to Claude Code → updates status.
"""

import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Configuration
BOARD_DIR = Path.home() / ".hermes/profiles/dev/kanban/boards/architecture-improvements"
BOARD_FILE = BOARD_DIR / "current.json"
HISTORY_FILE = BOARD_DIR / "history.json"
BACKUP_DIR = BOARD_DIR / "backups"

# Task specs (subset; full specs in improvement-plan skill)
TASK_SPECS = {
    1: {
        "title": "Matrix Synapse versioning consistency",
        "priority": "Critical",
        "phase": 1,
        "description": "Create git repo for Synapse, migrate configs, add to CI pipeline",
        "checkpoints": [
            "Audit current Synapse version in deployment",
            "Create git repo with ansible/docker-compose configs",
            "Add CI job for versioning checks",
            "Validate docker compose up deploys from repo",
        ],
        "timeout_hours": 2,
    },
    2: {
        "title": "cosign supply chain verification",
        "priority": "Critical",
        "phase": 1,
        "description": "Implement cosign signing for production images; Registry rejects unsigned",
        "checkpoints": [
            "Setup cosign keys in CI environment",
            "Add signing step to build pipeline",
            "Configure Registry to require signatures",
            "Test: cosign verify passes for production tags",
        ],
        "timeout_hours": 1,
    },
    3: {
        "title": "Quality gate fail-open detection",
        "priority": "Critical",
        "phase": 1,
        "description": "Make GATE mandatory; override only via web UI with reason",
        "checkpoints": [
            "Identify all SKIP conditions in GATE",
            "Make GATE blocking by default",
            "Implement web UI override mechanism",
            "Verify pipeline blocks without override-reason",
        ],
        "timeout_hours": 0.5,
    },
}


class KanbanExecutor:
    """Manages Kanban board state and task execution."""

    def __init__(self):
        self.board_file = BOARD_FILE
        self.board_state: Dict[str, Any] = {}
        self.report = []

    def log(self, msg: str, level: str = "INFO"):
        """Log message to report."""
        timestamp = datetime.now().isoformat()
        formatted = f"[{timestamp}] {level}: {msg}"
        self.report.append(formatted)
        print(formatted)

    def load_board(self) -> bool:
        """Load board state from JSON."""
        if not self.board_file.exists():
            self.log(f"Board file not found: {self.board_file}", "ERROR")
            return False
        try:
            with open(self.board_file) as f:
                self.board_state = json.load(f)
            self.log(f"Board loaded: {self.board_file}")
            return True
        except json.JSONDecodeError as e:
            self.log(f"Board JSON invalid: {e}", "ERROR")
            return False

    def save_board(self) -> bool:
        """Save board state to JSON."""
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_file = BACKUP_DIR / f"backup-{datetime.now().isoformat()}.json"
            with open(backup_file, "w") as f:
                json.dump(self.board_state, f, indent=2)
            
            with open(self.board_file, "w") as f:
                json.dump(self.board_state, f, indent=2)
            self.log(f"Board saved: {self.board_file}")
            return True
        except Exception as e:
            self.log(f"Failed to save board: {e}", "ERROR")
            return False

    def find_ready_task(self) -> Optional[int]:
        """Find first task with READY status. Returns task ID (1-13)."""
        ready_tasks = self.board_state.get("tasks_by_status", {}).get("READY", {}).get("tasks", [])
        if not ready_tasks:
            self.log("No READY tasks found", "WARNING")
            return None
        
        # Extract task number from string like "Task #1 (Matrix Synapse versioning)"
        first_ready = ready_tasks[0]
        try:
            task_id = int(first_ready.split("#")[1].split(" ")[0])
            self.log(f"Found READY task: #{task_id} ({first_ready})")
            return task_id
        except (IndexError, ValueError):
            self.log(f"Could not parse task ID from: {first_ready}", "ERROR")
            return None

    def mark_in_progress(self, task_id: int) -> bool:
        """Move task from READY to IN_PROGRESS."""
        tasks_by_status = self.board_state.get("tasks_by_status", {})
        ready_tasks = tasks_by_status.get("READY", {}).get("tasks", [])
        
        # Find and remove from READY
        task_name = None
        for task in ready_tasks:
            if f"#{task_id}" in task:
                task_name = task
                ready_tasks.remove(task)
                break
        
        if not task_name:
            self.log(f"Task #{task_id} not found in READY", "ERROR")
            return False
        
        # Add to IN_PROGRESS
        in_progress = tasks_by_status.get("IN_PROGRESS", {})
        if "tasks" not in in_progress:
            in_progress["tasks"] = []
        in_progress["tasks"].append(task_name)
        in_progress["count"] = len(in_progress["tasks"])
        
        # Update counts
        tasks_by_status["READY"]["count"] = len(ready_tasks)
        tasks_by_status["IN_PROGRESS"] = in_progress
        self.board_state["tasks_by_status"] = tasks_by_status
        
        self.log(f"Task #{task_id} moved to IN_PROGRESS")
        return True

    def delegate_to_claude(self, task_id: int, retry_count: int = 0, max_retries: int = 2) -> bool:
        """Delegate task execution to Claude Code with retry logic."""
        import os
        import time
        
        spec = TASK_SPECS.get(task_id)
        if not spec:
            self.log(f"No spec found for task #{task_id}", "ERROR")
            return False
        
        # Build prompt for Claude Code
        prompt = f"""
Execute this architecture improvement task:

**Task #{task_id}: {spec['title']}**
Priority: {spec['priority']} (Phase {spec['phase']})

**Description:**
{spec['description']}

**Checkpoints to complete:**
{chr(10).join(f"  {i+1}. {cp}" for i, cp in enumerate(spec['checkpoints']))}

**Requirements:**
- Work in /home/admin/projects/nemohermes_bks directory
- Complete all checkpoints
- Provide evidence (git commits, config files, test results)
- Return JSON with status: success/failure and checkpoint results

**Success criteria:**
- All checkpoints PASS
- Changes committed to git
- No regressions in existing tests
"""

        retry_msg = f" (attempt {retry_count+1}/{max_retries+1})" if retry_count > 0 else ""
        self.log(f"Delegating task #{task_id} to Claude Code{retry_msg}...")
        
        try:
            # Run Claude Code in print mode with correct flags
            # Pass environment to ensure Claude can access credentials
            env = os.environ.copy()
            env['HOME'] = '/home/admin'
            
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    prompt,
                ],
                cwd="/home/admin/projects/nemohermes_bks",
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
                env=env,  # Pass environment with HOME
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.log(f"Claude Code failed: {error_msg}", "ERROR")
                
                # Retry on transient errors (connection issues, timeouts)
                if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                    if retry_count < max_retries:
                        wait_time = 5 * (retry_count + 1)  # Exponential backoff: 5s, 10s, 15s
                        self.log(f"Retrying after {wait_time}s (transient error)...")
                        time.sleep(wait_time)
                        return self.delegate_to_claude(task_id, retry_count + 1, max_retries)
                
                return False
            
            # Claude in -p mode returns text output, not JSON
            # Check if output contains success indicators
            output_text = result.stdout.lower()
            if "error" in output_text and "failed" in output_text:
                self.log(f"Task #{task_id} execution failed (Claude reported error)", "WARNING")
                return False
            else:
                # Assume success if Claude completed without error
                self.log(f"Task #{task_id} execution succeeded")
                return True
        
        except subprocess.TimeoutExpired:
            self.log(f"Claude Code timeout after {spec['timeout_hours']} hour(s)", "ERROR")
            # Retry on timeout
            if retry_count < max_retries:
                wait_time = 5 * (retry_count + 1)
                self.log(f"Retrying after {wait_time}s (timeout)...")
                time.sleep(wait_time)
                return self.delegate_to_claude(task_id, retry_count + 1, max_retries)
            return False
        except FileNotFoundError:
            self.log("Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code", "ERROR")
            return False
        except Exception as e:
            self.log(f"Delegation failed: {e}", "ERROR")
            # Retry on unknown errors
            if retry_count < max_retries:
                wait_time = 5 * (retry_count + 1)
                self.log(f"Retrying after {wait_time}s (unknown error)...")
                time.sleep(wait_time)
                return self.delegate_to_claude(task_id, retry_count + 1, max_retries)
            return False

    def mark_done(self, task_id: int) -> bool:
        """Move task from IN_PROGRESS to DONE."""
        tasks_by_status = self.board_state.get("tasks_by_status", {})
        in_progress = tasks_by_status.get("IN_PROGRESS", {}).get("tasks", [])
        
        task_name = None
        for task in in_progress:
            if f"#{task_id}" in task:
                task_name = task
                in_progress.remove(task)
                break
        
        if not task_name:
            self.log(f"Task #{task_id} not found in IN_PROGRESS", "ERROR")
            return False
        
        # Add to DONE
        done = tasks_by_status.get("DONE", {})
        if "tasks" not in done:
            done["tasks"] = []
        done["tasks"].append(task_name)
        done["count"] = len(done["tasks"])
        
        # Update counts
        tasks_by_status["IN_PROGRESS"]["count"] = len(in_progress)
        tasks_by_status["DONE"] = done
        self.board_state["tasks_by_status"] = tasks_by_status
        
        self.log(f"Task #{task_id} marked DONE")
        return True

    def mark_blocked(self, task_id: int) -> bool:
        """Move task from IN_PROGRESS to BLOCKED."""
        tasks_by_status = self.board_state.get("tasks_by_status", {})
        in_progress = tasks_by_status.get("IN_PROGRESS", {}).get("tasks", [])
        
        task_name = None
        for task in in_progress:
            if f"#{task_id}" in task:
                task_name = task
                in_progress.remove(task)
                break
        
        if not task_name:
            self.log(f"Task #{task_id} not found in IN_PROGRESS", "ERROR")
            return False
        
        # Add to BLOCKED
        blocked = tasks_by_status.get("BLOCKED", {}).get("tasks", [])
        blocked.append(task_name)
        
        tasks_by_status["IN_PROGRESS"]["count"] = len(in_progress)
        tasks_by_status["BLOCKED"]["count"] = len(blocked)
        self.board_state["tasks_by_status"] = tasks_by_status
        
        self.log(f"Task #{task_id} moved to BLOCKED (execution failed)")
        return True

    def execute(self) -> bool:
        """Main execution loop."""
        self.log("=" * 60)
        self.log("Kanban Executor Started")
        self.log("=" * 60)
        
        # Load board
        if not self.load_board():
            return False
        
        # Find READY task
        task_id = self.find_ready_task()
        if task_id is None:
            self.log("No tasks to execute", "INFO")
            return True
        
        # Mark as IN_PROGRESS
        if not self.mark_in_progress(task_id):
            return False
        
        # Save intermediate state
        if not self.save_board():
            return False
        
        # Delegate to Claude Code
        success = self.delegate_to_claude(task_id)
        
        # Update task status
        if success:
            self.mark_done(task_id)
            self.log(f"✅ Task #{task_id} completed successfully")
        else:
            self.mark_blocked(task_id)
            self.log(f"❌ Task #{task_id} execution failed")
        
        # Save final state
        if not self.save_board():
            return False
        
        self.log("=" * 60)
        self.log("Kanban Executor Completed")
        self.log("=" * 60)
        
        return success


def main():
    executor = KanbanExecutor()
    success = executor.execute()
    
    # Print report
    print("\n" + "=" * 60)
    print("EXECUTION REPORT")
    print("=" * 60)
    for line in executor.report:
        print(line)
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
