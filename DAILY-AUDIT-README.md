# Daily Audit & Improvement Cycle

Automated daily workflow that audits `nemohermes_bks`, proposes improvements,
waits for your signed approval, implements changes via Claude, and
auto-commits if tests pass.

> Ранее существовало два независимых README (`DAILY-AUDIT-README.md` для
> расписания 10:00/18:00/20:00 и `DAILY-AUDIT-README-NEW.md` для немедленного
> флоу) — они разошлись и оба описывали approval-файл по устаревшему пути без
> подписи. Сведены в один документ 2026-08-06.

## 🚀 Two entrypoints, one approval format

Обе точки входа читают один и тот же подписанный approval-файл
(`schemas/approval.schema.json` + HMAC, см. ниже) и вызывают один и тот же
canonical `daily-implement-now.sh` — отличается только то, как запускается
реализация после одобрения.

| | Immediate (по умолчанию) | Fixed-schedule (legacy) |
|---|---|---|
| Cron | 1 джоба: `daily-audit-and-wait.sh` в 10:00 | 3 джобы: `daily-cycle-orchestrator.sh` на audit/implement/verify |
| После approve | Реализация стартует **сразу** | Ждёт следующей фазы оркестратора |
| Entry script | `scripts/daily-audit-and-wait.sh` → `scripts/daily-implement-now.sh` | `scripts/daily-cycle-orchestrator.sh` → `scripts/daily-implement.sh` (тонкая обёртка над `daily-implement-now.sh --no-auto-verify`) → `scripts/daily-verify.sh` |

Если не уверены, какой у вас настроен — immediate проще и это то, что
реально тестировалось сегодня; fixed-schedule сохранён для Telegram-уведомлений
по фазам через `daily-cycle-orchestrator.sh`.

## 📅 Schedule (immediate flow)

| Time | Phase | Action | Output |
|------|-------|--------|--------|
| **10:00** | Audit | Claude анализирует код, безопасность, тесты, доки | `audits/YYYY-MM-DD-audit-report.json` |
| **anytime after** | Approval | Вы создаёте и подписываете approval-файл | `/home/admin/approvals/YYYY-MM-DD-approval.json` |
| **immediately after approval** | Implement | Sequential Claude loop реализует задачи, тестирует каждую | `audits/YYYY-MM-DD-implementation.log` |
| **immediately after implement** | Verify | Тесты + ruff + security + auto-commit | Git commit или отказ с причиной |

## 👤 User Workflow

### Phase 1: Audit report
```bash
cat audits/$(date +%Y-%m-%d)-audit-report.json | jq '.issues'
```

### Phase 2: Approve — schema + HMAC signature required

Файл одобрения **не в репозитории** (AUDIT-005: агент не может быть тем
единственным, кто и предлагает, и одобряет изменение в своей собственной
истории). `daily-implement-now.sh` вызывает `ci/verify-approval.sh` ДО
единой строчки реализации и откажет, если:
- файл не проходит `schemas/approval.schema.json`,
- HMAC-подпись не совпадает (ключ `APPROVAL_HMAC_KEY` — GitLab CI Variable,
  локально тот же ключ в `~/.bashrc`),
- любой `approved_issue_ids` отсутствует в отчёте аудита того же дня.

```bash
mkdir -p /home/admin/approvals
cat > /home/admin/approvals/$(date +%Y-%m-%d)-approval.json << 'EOF'
{
  "approval_date": "2026-08-06",
  "approved_issue_ids": ["AUDIT-001", "AUDIT-003"],
  "approved_issues": [
    {
      "id": "AUDIT-001",
      "priority": "CRITICAL",
      "title": "Add connection pooling",
      "implementation": "Setup pgbouncer with 50 connection pool..."
    },
    {
      "id": "AUDIT-003",
      "priority": "HIGH",
      "title": "Add missing unit tests",
      "implementation": "Create tests/auth_test.py with 80%+ coverage..."
    }
  ]
}
EOF

# Подписать (без этого шага verify-approval.py откажет с "signature is a required property"):
APPROVAL_HMAC_KEY="$(grep '^export APPROVAL_HMAC_KEY=' ~/.bashrc | tail -1 | sed -E 's/^export APPROVAL_HMAC_KEY="(.*)"$/\1/')" \
  python3 scripts/verify-approval.py sign /home/admin/approvals/$(date +%Y-%m-%d)-approval.json
```

Если не создать approval-файл — реализация не начнётся, следующий день
стартует со свежего аудита.

### Phase 3: Implementation (сразу после подписанного approval)
```bash
tail -f audits/$(date +%Y-%m-%d)-implementation.log
```
Каждая одобренная задача реализуется последовательно через Claude Code
(`--allowedTools Read,Edit,Write,Bash(...)`, `--max-turns 20`), с
тестированием внутри каждой задачи (`bash -n`, `python -m py_compile`,
`pytest`, `yamllint`, `shellcheck`, где применимо).

### Phase 4: Auto-Commit or Alert

Гейты перед коммитом, в порядке: `pytest` (блокирующий, не опция) → `ruff
check scripts/` → `ci/check-changed-paths.sh` (агент не может править
собственные `ci/`, `k8s/`, `.gitlab-ci.yml`, `.gitignore` — правится вручную
человеком) → белый список `git add -- scripts compliance metrics tests docs
*.md` → `ci/guard-staged-secrets.sh` (`.env`/`.pem`/`.key`/`id_rsa` не
попадают в индекс) → финальная claude-верификация (`ready_to_commit`,
fail-closed на любой невалидный JSON или ненулевой exit).

```
✅ ALL CHECKS PASSED → COMMITTING
```
или
```
❌ VERIFICATION FAILED → NOT COMMITTING
```

## 📁 File Locations

```
/home/admin/projects/nemohermes_bks/
├── scripts/
│   ├── daily-audit.sh                ← генерация отчёта (+ --wait для immediate-флоу)
│   ├── daily-audit-and-wait.sh       ← тонкая обёртка: daily-audit.sh --wait
│   ├── daily-implement-now.sh        ← canonical реализация (approval-гейт + sequential loop)
│   ├── daily-implement.sh            ← тонкая обёртка: daily-implement-now.sh --no-auto-verify (для оркестратора)
│   ├── daily-verify.sh               ← standalone verify+commit (fixed-schedule флоу)
│   ├── daily-verify-and-commit.sh    ← verify+commit, авто-вызывается из daily-implement-now.sh
│   └── daily-cycle-orchestrator.sh   ← fixed-schedule флоу с Telegram-уведомлениями по фазам
│
/home/admin/approvals/                ← approval-файлы, ВНЕ репозитория (AUDIT-005), 0700
├── YYYY-MM-DD-approval.json

/home/admin/projects/nemohermes_bks/
├── audits/                           ← отчёты аудита и логи (не коммитятся, кроме *.md-отчётов)
│   ├── YYYY-MM-DD-audit-report.json
│   ├── YYYY-MM-DD-implementation.log
│   └── YYYY-MM-DD-verify-commit.log
└── kanban/
    └── YYYY-MM-DD-kanban.json
```

## 🔍 What Does the Audit Check?

Code quality, security (CVE/secrets/RBAC), test coverage, architecture
(SPOF/coupling/scaling), documentation gaps, performance, deployment
readiness, team processes — см. промпт в `scripts/daily-audit.sh`.

## 🧪 What Does Verification Check?

1. `pytest tests/ -q` — блокирующий гейт, не декоративный (AUDIT-004).
2. `ruff check scripts/`.
3. `pip-audit -r requirements-dev.txt` (best-effort, security).
4. Health-check `router` через прямой `curl http://127.0.0.1:4000/health` —
   без `docker compose`: в этом каталоге нет `docker-compose.yml`, router —
   отдельный репозиторий/стек.
5. `ci/check-changed-paths.sh` + `ci/guard-staged-secrets.sh`.
6. Финальная claude-верификация JSON (`ready_to_commit`).

## 🛠️ Manual Execution

```bash
cd /home/admin/projects/nemohermes_bks

# Immediate flow (после подписанного approval)
bash scripts/daily-audit-and-wait.sh     # генерирует отчёт, ждёт approval, сам стартует реализацию
bash scripts/daily-implement-now.sh      # если approval уже лежит и подписан
bash scripts/daily-verify-and-commit.sh  # только verify+commit

# Fixed-schedule flow
bash scripts/daily-cycle-orchestrator.sh
```

## ❓ FAQ

**Q: Что если не одобрить ни одной задачи?**
Реализация не стартует. Следующий день начинается со свежего аудита.

**Q: Что если approval-файл не подписан или ключ не совпадает?**
`verify-approval.py` откажет с конкретной причиной (`signature is a required
property`, `HMAC-подпись не совпадает`, `APPROVAL_HMAC_KEY не задан`) —
реализация не начнётся вообще, ни одного файла не тронуто.

**Q: Что если Claude упирается в `--max-turns` во время реализации?**
Задача помечается `⚠️ timeout or error`, но частичные правки на диске могут
остаться — проверить `git diff` перед следующим прогоном. Верификация в
конце всё равно должна поймать незавершённость через `pytest`/`ruff`.

**Q: Что если тесты не проходят на Phase 4?**
Коммита не будет. Правите вручную, затем `bash scripts/daily-verify-and-commit.sh` заново.

**Q: Как поправить сами гейты (`ci/`, `.gitlab-ci.yml`)?**
Автономный цикл не может — `ci/check-changed-paths.sh` это блокирует
намеренно (AUDIT-005). Правка `ci/` — это ручной коммит человеком, как
сегодняшние AUDIT-005/007/008/009/010a.

---

**Аудит-бэклог отслеживается как `AUDIT-XXX` в `audits/YYYY-MM-DD-audit-report.json`.**
