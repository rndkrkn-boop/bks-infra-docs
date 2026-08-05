#!/usr/bin/env bash
# claude-task-orchestrator-lib.sh — многоходовая реализация одной одобренной
# задачи через claude -p --session-id/--resume вместо одного вызова с
# фиксированным --max-turns.
#
# Раньше (до 2026-08-06) daily-implement-now.sh делал ОДИН вызов claude -p
# --max-turns 20 на задачу: сложные многофайловые задачи регулярно упирались
# в "Reached max turns" и помечались failed, хотя частичные правки уже легли
# на диск (Edit/Write — реальные файловые операции, не часть разговора).
# --resume продолжает ТОТ ЖЕ разговор с новым бюджетом ходов на каждый вызов,
# так что "кончились ходы" перестаёт быть смертельным исходом.
#
# Решения по ходу диалога — гибрид, а не сплошной LLM-оркестратор:
#   - "продолжать / готово / бросить" — детерминированная bash-логика
#     (orchestrator_decide), git diff как источник истины о прогрессе.
#   - "воркер застрял с вопросом" — единственный случай, где нужно реальное
#     суждение, поэтому единственный случай отдельного дешёвого claude -p
#     вызова (ask_orchestrator_question).
# Обоснование: в headless-цикле без человека в контуре LLM, "отвечающий
# самому себе" на основе той же информации, что уже есть у воркера, не
# добавляет ничего, кроме риска и стоимости — кроме случая, когда ответ
# требует политики (можно ли пушить/публиковать), а не факта из контекста.
#
# Источником (после claude-rate-limit-lib.sh — использует claude_should_retry):
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   . "$SCRIPT_DIR/claude-rate-limit-lib.sh"
#   . "$SCRIPT_DIR/claude-task-orchestrator-lib.sh"
#
# Требует, чтобы вызывающий скрипт уже определил $PROJECT_DIR (корень
# репозитория) — используется для вызова ci/check-changed-paths.sh.

CLAUDE_TASK_MAX_RESUME_ATTEMPTS="${CLAUDE_TASK_MAX_RESUME_ATTEMPTS:-5}"
CLAUDE_TASK_STAGNANT_ABANDON_THRESHOLD="${CLAUDE_TASK_STAGNANT_ABANDON_THRESHOLD:-2}"
CLAUDE_TASK_INNER_MAX_TURNS="${CLAUDE_TASK_INNER_MAX_TURNS:-20}"
CLAUDE_TASK_INNER_TIMEOUT="${CLAUDE_TASK_INNER_TIMEOUT:-600}"

# Общие ограничения worker-сессии на КАЖДОМ вызове (и --session-id, и каждый
# --resume) — 70ed818 показал, что накопленные project-level allow-паттерны
# (.claude/settings.local.json: Bash(git commit *), Bash(curl *) без
# привязки к пути) складываются с --allowedTools вызова, если вызов не
# ограничен явно. --setting-sources project (без local) убирает источник
# проблемы, deny-лист — второй слой на случай, если по какой-то причине
# local всё же подмешается.
readonly WORKER_ALLOWED_TOOLS="Read,Edit,Write,Bash(ls:*),Bash(bash -n:*),Bash(python -m py_compile:*),Bash(pytest:*),Bash(npm test:*),Bash(yamllint:*),Bash(yq:*),Bash(shellcheck:*),Bash(git add:*),Bash(git rm:*),Bash(git status:*),Bash(git diff:*)"
readonly WORKER_DISALLOWED_TOOLS="Bash(git commit*),Bash(git push*),Bash(curl*),Bash(wget*),Bash(sudo*)"

# Схема ответа воркера. Не включает files_changed — git status --porcelain
# уже даёт эту информацию как источник истины, самоотчёт модели тут лишний.
readonly WORKER_JSON_SCHEMA='{"type":"object","properties":{"status":{"type":"string","enum":["done","blocked","in_progress"]},"summary":{"type":"string"},"question":{"type":["string","null"]}},"required":["status","summary","question"],"additionalProperties":false}'

# orchestrator_decide STATUS DIFF_CHANGED STAGNANT_COUNT ATTEMPT MAX_ATTEMPTS
# Чистая функция без побочных эффектов — единственное место с логикой
# "что делать дальше", тестируется без единого реального вызова claude.
# Печатает одно слово в stdout: complete|nudge|ask_orchestrator|abandon.
orchestrator_decide() {
    local status="$1" diff_changed="$2" stagnant_count="$3" attempt="$4" max_attempts="$5"

    # "done" проверяется ДО лимита попыток: если работа реально завершилась на
    # последней разрешённой попытке, это успех, а не повод его выбросить —
    # лимит ограничивает ЧИСЛО ХОДОВ, а не отменяет уже случившийся результат.
    if [ "$status" = "done" ]; then
        echo "complete"
        return
    fi

    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "abandon"
        return
    fi

    case "$status" in
        blocked)
            echo "ask_orchestrator"
            ;;
        in_progress|call_failed)
            if [ "$stagnant_count" -ge "$CLAUDE_TASK_STAGNANT_ABANDON_THRESHOLD" ]; then
                echo "abandon"
            else
                echo "nudge"
            fi
            ;;
        *)
            echo "abandon"
            ;;
    esac
}

# _worker_call SESSION_ID IS_FIRST MESSAGE OUT_JSON_FILE
# Один вызов worker-сессии (первый через --session-id, последующие через
# --resume), обёрнутый во внутренний rate-limit retry. Пишет полный JSON-ответ
# claude в OUT_JSON_FILE. Возвращает код возврата claude (0 = успешный ход,
# не обязательно status=done — просто вызов не упал).
_worker_call() {
    local session_id="$1" is_first="$2" message="$3" out_json_file="$4"
    local attempt=1
    local resume_flag=(--resume "$session_id")
    [ "$is_first" = "1" ] && resume_flag=(--session-id "$session_id")

    while :; do
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
        local rc=$?
        if [ "$rc" -eq 0 ]; then
            return 0
        fi
        if claude_should_retry "$attempt" "$(cat "$out_json_file" 2>/dev/null)"; then
            attempt=$((attempt + 1))
            sleep "$CLAUDE_RATE_LIMIT_WAIT_SECONDS"
            continue
        fi
        return "$rc"
    done
}

# ask_orchestrator_question ISSUE_TITLE ISSUE_PRIORITY ISSUE_IMPL QUESTION SUMMARY
# Единственный LLM-вызов вне worker-сессии. Не resumed, без инструментов,
# низкий --max-turns — чистое рассуждение над переданным текстом, с жёстко
# зашитой политикой безопасности для headless-цикла без человека в контуре.
# Печатает текстовый ответ в stdout (используется как следующее сообщение
# воркеру), либо пустую строку при полном отказе — вызывающий код должен
# трактовать пустой ответ как "не удалось получить суждение" и через
# orchestrator_decide на следующей итерации это станет stagnant/abandon.
ask_orchestrator_question() {
    local title="$1" priority="$2" impl="$3" question="$4" summary="$5"

    local prompt
    prompt=$(cat << PROMPT_EOF
Это автономный headless-цикл реализации без человека в контуре — сообщение
не будет прочитано человеком до завершения задачи. Задача-воркер застряла
с вопросом. Дай короткое (2-4 предложения) прямое указание, что делать
дальше, без встречных вопросов — их некому будет прочитать.

ЖЁСТКОЕ ПРАВИЛО: никогда не разрешай пуш в удалённый git-репозиторий,
открытие pull request, отправку сообщений (Telegram/email/API) или любое
другое видимое вовне / необратимое действие. На такой вопрос отвечай явным
отказом с указанием пропустить этот шаг и задокументировать его в summary
как "требует ручного действия человека".

Для внутренних обратимых развилок (выбор между несколькими безопасными
вариантами реализации, формат файла, порядок шагов) выбирай самый
консервативный/минимальный вариант и коротко объясни, почему.

Контекст задачи:
Приоритет: $priority
Название: $title
План реализации: $impl

Текущий прогресс воркера: $summary

Вопрос воркера: $question

Ответь ТОЛЬКО текстом указания для воркера, без преамбулы.
PROMPT_EOF
)

    local out_file
    out_file=$(mktemp)
    local attempt=1
    local rc=1
    while :; do
        timeout 120 claude -p "$prompt" \
            --permission-mode dontAsk \
            --setting-sources project \
            --disallowedTools "$WORKER_DISALLOWED_TOOLS" \
            --max-turns 3 \
            < /dev/null > "$out_file" 2>&1
        rc=$?
        if [ "$rc" -eq 0 ]; then
            break
        fi
        if claude_should_retry "$attempt" "$(cat "$out_file" 2>/dev/null)"; then
            attempt=$((attempt + 1))
            sleep "$CLAUDE_RATE_LIMIT_WAIT_SECONDS"
            continue
        fi
        break
    done

    if [ "$rc" -eq 0 ]; then
        cat "$out_file"
    fi
    rm -f "$out_file"
}

# run_task_via_worker_session ISSUE_ID PRIORITY TITLE IMPL LOG_FILE
# Возвращает 0 (задача реализована) / 1 (провал/заброшена). Пишет ход
# диалога в LOG_FILE в том же формате, что вызывающий цикл ожидает сегодня.
run_task_via_worker_session() {
    local issue_id="$1" priority="$2" title="$3" impl="$4" log_file="$5"

    local session_id
    session_id=$(uuidgen)

    local initial_prompt
    initial_prompt=$(cat << PROMPT_EOF
Task: $title

Implementation requirements:
$impl

IMPORTANT: As you implement, include testing:
1. Create files/changes
2. For each file created, verify it exists: \`ls -la FILE\`
3. For code files, run basic syntax check
4. For scripts, run: \`bash -n SCRIPT.sh\` (syntax check)
5. For Python, run: \`python -m py_compile FILE.py\`
6. For YAML, run: \`yamllint FILE.yaml\` or \`yq . FILE.yaml\`
7. Run any existing unit tests: npm test, pytest

Keep implementation focused and concise.

You will be asked for a structured status after this and possibly further
turns. Use status "done" only once the requirements above are actually
implemented and tested; "blocked" only if you genuinely cannot proceed
without a decision outside the given requirements (put the decision needed
in the question field); otherwise "in_progress".
PROMPT_EOF
)

    local baseline_status
    baseline_status=$(git status --porcelain)
    local initial_head_log_line
    initial_head_log_line=$(git log --oneline -1 2>/dev/null)

    # Baseline для check-changed-paths.sh: если запрещённый путь уже был
    # тронут ДО старта этой задачи (например, застрявшее незакоммиченное
    # состояние от прошлого прерванного запуска), это не вина текущего
    # воркера — сравниваем состояние ДО/ПОСЛЕ, а не абсолютный результат
    # гейта, иначе чужая грязь в рабочем дереве валит первую же попытку.
    local baseline_paths_ok=1
    if ! bash "$PROJECT_DIR/ci/check-changed-paths.sh" > /dev/null 2>&1; then
        baseline_paths_ok=0
        echo "⚠️  check-changed-paths уже нарушен ДО старта задачи (не вина этой задачи) — проверяю только НОВЫЕ нарушения" >> "$log_file"
    fi

    local stagnant_count=0
    local next_message="$initial_prompt"
    local is_first=1
    local attempt=1

    {
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "📌 Task ($issue_id): $title [session $session_id]"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Priority: $priority"
        echo
    } >> "$log_file"

    while :; do
        local pre_status
        pre_status=$(git status --porcelain)

        local out_json
        out_json=$(mktemp)
        _worker_call "$session_id" "$is_first" "$next_message" "$out_json"
        local call_rc=$?
        is_first=0

        local post_status
        post_status=$(git status --porcelain)
        local diff_changed=0
        [ "$pre_status" != "$post_status" ] && diff_changed=1

        # Belt-and-suspenders: --permission-mode dontAsk/--disallowedTools
        # проверены эмпирически 2026-08-06 (см. память
        # claude-code-allowedtools-not-isolated), но независимая проверка
        # ПОСЛЕ каждого хода дешевле, чем доверять флагам вслепую на каждом
        # будущем resume. Триггерим abort только на ПЕРЕХОД ok→fail — если
        # гейт уже был нарушен до старта задачи (baseline_paths_ok=0), это
        # не отменяем, но и не считаем виной текущего воркера повторно.
        if bash "$PROJECT_DIR/ci/check-changed-paths.sh" >> "$log_file" 2>&1; then
            :
        elif [ "$baseline_paths_ok" -eq 1 ]; then
            echo "🚨 SECURITY: worker затронул запрещённые пути (ci/, k8s/, .gitlab-ci.yml, .gitignore) — немедленный abort, не обычный fail" >> "$log_file"
            rm -f "$out_json"
            return 1
        fi
        if [ "$(git log --oneline -1 2>/dev/null)" != "$initial_head_log_line" ]; then
            echo "🚨 SECURITY: worker создал коммит в обход daily-verify-and-commit.sh — немедленный abort" >> "$log_file"
            rm -f "$out_json"
            return 1
        fi

        local status="call_failed" summary="" question=""
        if [ "$call_rc" -eq 0 ]; then
            status=$(jq -r '.structured_output.status // "call_failed"' "$out_json" 2>/dev/null)
            summary=$(jq -r '.structured_output.summary // ""' "$out_json" 2>/dev/null)
            question=$(jq -r '.structured_output.question // ""' "$out_json" 2>/dev/null)
            [ -z "$status" ] || [ "$status" = "null" ] && status="call_failed"
        fi

        echo "🤖 [$attempt/$CLAUDE_TASK_MAX_RESUME_ATTEMPTS] status=$status diff_changed=$diff_changed summary: $summary" >> "$log_file"

        if [ "$diff_changed" -eq 0 ]; then
            stagnant_count=$((stagnant_count + 1))
        else
            stagnant_count=0
        fi

        local action
        action=$(orchestrator_decide "$status" "$diff_changed" "$stagnant_count" "$attempt" "$CLAUDE_TASK_MAX_RESUME_ATTEMPTS")
        rm -f "$out_json"

        case "$action" in
            complete)
                local final_status
                final_status=$(git status --porcelain)
                if [ "$final_status" = "$baseline_status" ]; then
                    echo "⚠️  status=done, но суммарный diff с начала задачи пуст — возможно, легитимный no-op (investigated, no code change needed), логирую как предупреждение, не проваливаю" >> "$log_file"
                fi
                echo "✅ Task ($issue_id) completed after $attempt attempt(s)" >> "$log_file"
                return 0
                ;;
            nudge)
                next_message="Continue implementing per the original requirements above. If you believe you are done, respond with status \"done\"."
                ;;
            ask_orchestrator)
                echo "❓ Task ($issue_id) blocked, question: $question" >> "$log_file"
                next_message=$(ask_orchestrator_question "$title" "$priority" "$impl" "$question" "$summary")
                if [ -z "$next_message" ]; then
                    echo "⚠️  ask_orchestrator_question вернул пустой ответ (сам упал/лимит) — считаю ход застоем" >> "$log_file"
                    stagnant_count=$((stagnant_count + 1))
                    next_message="No guidance available right now — use your best, most conservative judgement and continue, or report status \"done\" documenting the open question in your summary if you cannot proceed safely."
                else
                    echo "💬 Ответ оркестратора воркеру: $next_message" >> "$log_file"
                fi
                ;;
            abandon)
                echo "❌ Task ($issue_id) abandoned after $attempt attempt(s) (last status=$status)" >> "$log_file"
                return 1
                ;;
        esac

        attempt=$((attempt + 1))
    done
}
