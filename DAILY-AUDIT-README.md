# Daily Audit & Improvement Cycle

Automated daily workflow that audits `nemohermes_bks` project, proposes improvements, waits for user approval, implements changes via Claude, and auto-commits if tests pass.

## 📅 Schedule

| Time | Phase | Action | Output |
|------|-------|--------|--------|
| **10:00 AM** | Audit | Claude analyzes code, security, tests, docs | `audits/YYYY-MM-DD-audit-report.json` |
| **10:00-17:00** | Approval | You review and approve improvements | `audits/YYYY-MM-DD-approval.json` |
| **18:00 PM** | Implement | Sequential Claude loop fixes issues | `audits/YYYY-MM-DD-implementation.log` |
| **20:00 PM** | Verify | Tests + security checks + auto-commit | Git commit or failure alert |

## 🚀 Getting Started

### Step 1: Verify Scripts Exist
```bash
ls -la /home/admin/projects/nemohermes_bks/scripts/
# Should see:
# - daily-audit.sh
# - daily-implement.sh
# - daily-verify.sh
# - daily-cycle-orchestrator.sh
```

### Step 2: Verify Cron Jobs Are Active
```bash
hermes cron list | grep daily
# Should see:
# daily-audit-10am      ← 10:00 AM every day
# daily-implement-6pm   ← 18:00 PM every day (after approval)
# daily-verify-8pm      ← 20:00 PM every day (after implementation)
```

### Step 3: Wait for First Audit (10:00 AM)
At 10:00 AM, you'll receive a message with the audit report:
```
🔍 Daily audit complete. Review report and approve improvements.
Report: /home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-audit-report.json
```

## 👤 User Workflow

### Phase 1: Receive Audit Report (10:00 AM)
Hermes sends you the audit report with all findings:
```json
{
  "audit_date": "2026-08-05",
  "total_issues": 12,
  "by_priority": {
    "CRITICAL": 3,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2
  },
  "issues": [
    {
      "id": "AUDIT-001",
      "priority": "CRITICAL",
      "title": "Missing connection pooling",
      "description": "...",
      "effort": "MEDIUM"
    },
    ...
  ]
}
```

### Phase 2: Review & Approve (10:00 AM - 17:00 PM)

**Option A: Approve Selected Issues**

Create this file to approve specific issues:
```bash
cat > /home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-approval.json << 'EOF'
{
  "approved_issue_ids": ["AUDIT-001", "AUDIT-003", "AUDIT-005"],
  "approved_issues": [
    {
      "id": "AUDIT-001",
      "priority": "CRITICAL",
      "title": "Add connection pooling",
      "implementation": "Setup pgbouncer with 50 connection pool. Create ci/pgbouncer.conf..."
    },
    {
      "id": "AUDIT-003",
      "priority": "HIGH",
      "title": "Add missing unit tests",
      "implementation": "Create tests/auth.test.js with 80%+ coverage..."
    }
  ],
  "notes": "Focus on CRITICAL issues this week"
}
EOF
```

**Option B: Approve Nothing**

If you don't create an approval file, Phase 3 (implementation) will be skipped. Next day starts fresh with new audit.

### Phase 3: Watch Implementation (18:00 PM)
Hermes sends progress updates:
```
🚀 Improvements implemented. Running final verification...
Log: /home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-implementation.log
```

Each approved issue is implemented sequentially via Claude Code (one at a time).

### Phase 4: Auto-Commit or Alert (20:00 PM)

**If ALL tests pass:**
```
✅ All tests passed. Changes committed to git.
Commit: abc1234 "Daily audit improvements: 2026-08-05"
```

**If tests fail:**
```
❌ Verification failed. Review logs.
Issues: ...
Manual fix required before committing.
```

## 📁 File Locations

```
/home/admin/projects/nemohermes_bks/
├── scripts/
│   ├── daily-audit.sh              ← Phase 1 (runs at 10:00)
│   ├── daily-implement.sh           ← Phase 3 (runs at 18:00)
│   ├── daily-verify.sh              ← Phase 4 (runs at 20:00)
│   └── daily-cycle-orchestrator.sh  ← Optional full cycle
│
├── audits/                          ← All audit data
│   ├── YYYY-MM-DD-audit-report.json     (generated)
│   ├── YYYY-MM-DD-approval.json         (you create this)
│   ├── YYYY-MM-DD-implementation.log    (generated)
│   └── cycle.log                       (all phases)
│
└── kanban/
    └── daily-improvements.json      (tasks for Phase 3)
```

## 🔍 What Does the Audit Check?

Claude analyzes:
- ✅ **Code Quality:** Lint, complexity, duplication, best practices
- ✅ **Security:** CVEs, dependencies, secrets, RBAC, auth
- ✅ **Tests:** Coverage %, missing test files
- ✅ **Documentation:** Gaps, outdated, inconsistencies
- ✅ **Architecture:** SPOF, coupling, scaling issues
- ✅ **Performance:** Bottlenecks, inefficiencies
- ✅ **Deployment:** CI/CD, monitoring, health checks
- ✅ **Processes:** Runbooks, SLOs, documentation

## 🧪 What Does Verification Check?

Phase 4 runs:
1. **Unit Tests:** `npm test` (if exists)
2. **Integration Tests:** `pytest` (if exists)
3. **Linting:** `eslint` (JavaScript), `pylint` (Python)
4. **Security:** `trivy` (container images), `pip-audit` (dependencies)
5. **Health:** `docker-compose ps`, health endpoints
6. **Claude Verification:** Final health check before commit

Only if ALL pass → auto-commit. Otherwise → alert user.

## 🛠️ Manual Execution

You can run phases manually anytime:

```bash
# Run entire cycle
bash /home/admin/projects/nemohermes_bks/scripts/daily-cycle-orchestrator.sh

# Or individual phases
bash /home/admin/projects/nemohermes_bks/scripts/daily-audit.sh
bash /home/admin/projects/nemohermes_bks/scripts/daily-implement.sh
bash /home/admin/projects/nemohermes_bks/scripts/daily-verify.sh
```

## 📊 Monitoring & Logs

```bash
# Watch cycle progress in real-time
tail -f /home/admin/projects/nemohermes_bks/audits/cycle.log

# View today's audit report
cat /home/admin/projects/nemohermes_bks/audits/$(date +%Y-%m-%d)-audit-report.json | jq '.issues'

# View implementation log
tail -f /home/admin/projects/nemohermes_bks/audits/$(date +%Y-%m-%d)-implementation.log

# See recent commits
cd /home/admin/projects/nemohermes_bks && git log --oneline -n 10
```

## ❓ FAQ

### Q: What if I don't approve any issues?
A: Phase 3 (implement) is skipped. Next day starts fresh with new audit.

### Q: Can I approve issues after 17:00?
A: No, Phase 3 starts at 18:00 sharp. You must approve before then.

### Q: What if Claude reaches max-turns during implementation?
A: Partial implementation. Phase 4 verification might fail. Manual review required.

### Q: Can I stop a running phase?
A: Yes, cancel the cron job: `hermes cron pause <job_id>`

### Q: What if tests fail in Phase 4?
A: Changes are NOT committed. You must fix issues manually, then retry.

### Q: Can I rerun the cycle today?
A: Yes, manually: `bash /home/admin/projects/nemohermes_bks/scripts/daily-cycle-orchestrator.sh`

## 🚨 Troubleshooting

### Phase 1: "Audit didn't run at 10:00"
Check if cron is enabled:
```bash
hermes cron list | grep daily-audit-10am
# If "enabled: false", run:
hermes cron resume daily-audit-10am
```

### Phase 2: "I created approval.json but Phase 3 won't start"
Check file path and format:
```bash
# Must be exactly this:
/home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-approval.json

# Must be valid JSON:
jq '.' /home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-approval.json
```

### Phase 3: "Implementation incomplete"
Claude might have hit max-turns. Check log:
```bash
grep "reached max turns" /home/admin/projects/nemohermes_bks/audits/YYYY-MM-DD-implementation.log
```
Fix manually, then run Phase 4: `bash daily-verify.sh`

### Phase 4: "Tests failed, not committing"
Fix the code, then retry:
```bash
cd /home/admin/projects/nemohermes_bks
# Fix code...
bash scripts/daily-verify.sh
```

## 📞 Support

Hermes skill: `daily-audit-cycle`
Load skill: `skill_view name=daily-audit-cycle`

All scripts: `/home/admin/projects/nemohermes_bks/scripts/`

---

**Next audit runs:** Tomorrow at 10:00 AM ⏰
