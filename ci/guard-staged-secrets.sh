#!/usr/bin/env bash
# guard-staged-secrets.sh — pre-commit-гейт: носители секретов не уходят в индекс.
#
# Зачем отдельный гейт, если есть .gitignore и секрет-сканер в CI:
#   * .gitignore не действует на уже отслеживаемый файл — `git add -f`, `git mv`
#     или ранее закоммиченный путь проходят мимо него молча;
#   * сканер в CI ловит находку ПОСЛЕ коммита, а секрет в истории не отзывается
#     удалением файла — только ротацией и перезаписью истории;
#   * автономный цикл (scripts/daily-*.sh) коммитит без человека в контуре, и
#     единственная точка, где ещё можно остановиться, — момент до `git commit`.
#
# Проверяется ИНДЕКС (`git diff --cached`), а не рабочее дерево: гейт обязан
# судить ровно тот набор файлов, который уйдёт в коммит.
#
# Использование:
#   bash ci/guard-staged-secrets.sh            # проверить индекс текущего репо
#   REPO_DIR=/path bash ci/guard-staged-secrets.sh
#
# Коды возврата:
#   0 — в индексе нет носителей секретов
#   1 — найден запрещённый путь (коммит делать нельзя)
#   2 — ошибка окружения (не git-репозиторий)

set -euo pipefail

REPO_DIR="${REPO_DIR:-.}"

if ! git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    echo "❌ guard-staged-secrets: $REPO_DIR не git-репозиторий" >&2
    exit 2
fi

# Носители секретов по определению: .env в любом каталоге, приватные ключи и
# сертификаты, ssh-ключи. Совпадение по ИМЕНИ, а не по содержимому: содержимое
# проверяет secrets-scan (gitleaks) в .gitlab-ci.yml, здесь дешёвый и
# детерминированный барьер, который нельзя обойти изобретательным форматом.
FORBIDDEN='(^|/)\.env|\.pem$|\.key$|id_rsa'

# Шаблоны переменных — единственное исключение, и оно должно быть закрытым
# списком. Тот же список в rules.toml (SEC-002, поле allow) и в .gitignore
# (`!.env.example`): расхождение между ними означало бы, что один слой
# разрешает то, что другой запрещает.
ALLOWED='(^|/)\.env\.(example|template)$'

offenders=()
while IFS= read -r -d '' path; do
    [[ "$path" =~ $ALLOWED ]] && continue
    [[ "$path" =~ $FORBIDDEN ]] && offenders+=("$path")
done < <(git -C "$REPO_DIR" diff --cached --name-only -z)

if [ ${#offenders[@]} -gt 0 ]; then
    echo "❌ guard-staged-secrets: в индексе носители секретов — коммит остановлен" >&2
    for path in "${offenders[@]}"; do
        echo "   $path" >&2
    done
    echo >&2
    echo "Что делать:" >&2
    echo "   git restore --staged <путь>   # убрать из индекса" >&2
    echo "   проверить .gitignore и ci/secrets-policy.yaml (правило env-001)" >&2
    echo "   если секрет уже утёк — перевыпустить его, удаление файла не отзывает" >&2
    exit 1
fi

echo "✅ guard-staged-secrets: носителей секретов в индексе нет"
