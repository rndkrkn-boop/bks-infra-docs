#!/usr/bin/env bash
# check-changed-paths.sh — allow-list гейт для автономного цикла (AUDIT task 8).
#
# Автономная реализация не должна иметь возможности менять собственные гейты:
# .gitlab-ci.yml (пайплайн, который её проверяет), ci/ (гейты и политики),
# k8s/ (манифесты доставки), .gitignore (что вообще видно git-у). Раньше
# scripts/daily-verify*.sh сами включали ci/, k8s/ и .gitlab-ci.yml в
# `git add` allow-list — агент технически мог протащить правку своих же
# гейтов тем же коммитом, который эти гейты проверяют.
#
# Гейт стоит ПОСЛЕ фазы реализации и ДО `git add`: проверяет рабочее дерево
# целиком (staged + unstaged + untracked), а не только то, что скрипт
# собирается закоммитить — иначе можно было бы обойти его, изменив
# allow-list в самом вызывающем скрипте.
#
# Коды возврата:
#   0 — запрещённые пути не затронуты
#   1 — найдена правка в запрещённом пути (коммит делать нельзя)
#   2 — ошибка окружения (не git-репозиторий)

set -euo pipefail

REPO_DIR="${REPO_DIR:-.}"

if ! git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    echo "❌ check-changed-paths: $REPO_DIR не git-репозиторий" >&2
    exit 2
fi

# Якорь ^ обязателен: без него `ci/` совпал бы и с `scripts/ci/foo`.
FORBIDDEN='^(\.gitlab-ci\.yml|ci/|k8s/|\.gitignore)$|^ci/|^k8s/'

offenders=()
while IFS= read -r path; do
    [ -z "$path" ] && continue
    if [[ "$path" =~ $FORBIDDEN ]]; then
        offenders+=("$path")
    fi
done < <(
    {
        git -C "$REPO_DIR" diff --name-only
        git -C "$REPO_DIR" diff --cached --name-only
        git -C "$REPO_DIR" status --porcelain | awk '{ $1=""; sub(/^ /, ""); print }'
    } | sort -u
)

if [ ${#offenders[@]} -gt 0 ]; then
    echo "❌ check-changed-paths: автономный цикл затронул запрещённые пути — коммит остановлен" >&2
    for path in "${offenders[@]}"; do
        echo "   $path" >&2
    done
    echo >&2
    echo "Это собственные гейты цикла (ci/, k8s/, .gitlab-ci.yml, .gitignore) —" >&2
    echo "их правка требует ручного PR/MR, не автономного коммита." >&2
    exit 1
fi

echo "✅ check-changed-paths: ci/, k8s/, .gitlab-ci.yml, .gitignore не затронуты"
