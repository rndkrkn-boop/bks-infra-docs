#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# cosign-lib.sh — общая обвязка для подписи/проверки образов bks (Sigstore cosign).
#
# Источник истины для CI-джобов bks/infra, bks/router, bks/memgraphrag и для
# локального прогона scripts/cosign-selftest.sh: одна и та же логика в CI и
# на хосте, чтобы «проверено локально» означало «проверено то, что уедет в CI».
#
# Статус в этом репозитории (nemohermes_bks): подключён только к
# scripts/cosign-selftest.sh, ни в один реальный pipeline не встроен — здесь
# нет build-стадии, которую можно гейтить. Подробности и кто должен
# подключать: docs/ci-pipeline-notes.md.
#
# Использование:
#   . ci/cosign-lib.sh
#   cosign_install
#   cosign_load_key            # приватный ключ из COSIGN_KEY (base64)
#   cosign_sign_digest "$IMAGE" "$DIGEST"
#   cosign_load_pubkey
#   cosign_verify "$IMAGE@$DIGEST"
#
# Все временные файлы ключей ложатся в mktemp -d с umask 077 и удаляются
# trap'ом на EXIT — приватный ключ никогда не попадает в рабочую копию
# (а значит, и в артефакты джоба или в git).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Версия и контрольные суммы ───────────────────────────────────────────────
# Пиннится намеренно. История bks уже знает, чем кончается неверсионированный
# `pip install ruff` в CI (0.16.0 сломал lint без изменений кода, коммит
# c0eece8 в bks/memgraphrag) — для бинарника, которому мы доверяем подпись
# прод-образов, «latest» тем более неприемлем.
# Суммы — из официального cosign_checksums.txt релиза, сверены 2026-08-04.
COSIGN_VERSION="${COSIGN_VERSION:-v2.4.3}"
# shellcheck disable=SC2034 # читаются динамически: eval "want=\${COSIGN_SHA256_${arch}}" ниже (cosign_install)
COSIGN_SHA256_amd64="caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708"
# shellcheck disable=SC2034
COSIGN_SHA256_arm64="bd0f9763bca54de88699c3656ade2f39c9a1c7a2916ff35601caf23a79be0629"

# Куда ставить бинарник. В CI — внутрь чекаута (нет root), локально можно
# переопределить.
COSIGN_BIN_DIR="${COSIGN_BIN_DIR:-${CI_PROJECT_DIR:-$PWD}/.cosign-bin}"
COSIGN="${COSIGN:-$COSIGN_BIN_DIR/cosign}"

# ── Флаги реестра ────────────────────────────────────────────────────────────
# Registry 192.168.2.180:5050 отдаёт HTTP без TLS (см. ARCHITECTURE.md §1),
# поэтому cosign'у нужны оба флага: --allow-http-registry разрешает plain HTTP,
# --allow-insecure-registry не требует валидного сертификата. Для localhost
# go-containerregistry считает реестр insecure сам, но полагаться на это в
# прод-джобе нельзя — 192.168.2.180 не localhost.
COSIGN_REGISTRY_FLAGS="${COSIGN_REGISTRY_FLAGS:---allow-insecure-registry --allow-http-registry}"

# ── Transparency log ─────────────────────────────────────────────────────────
# ВАЖНО: по умолчанию `cosign sign` публикует запись в ПУБЛИЧНЫЙ Rekor
# (rekor.sigstore.dev) — проверено 2026-08-04, получили tlog index 2336641408
# для тестового образа. Для внутреннего реестра это утечка: наружу уходят
# digest'ы и метаданные приватных образов, а удалить запись из Rekor нельзя
# (immutable record). Поэтому подписываем с --tlog-upload=false и проверяем
# с --insecure-ignore-tlog.
#
# Эти два флага — ПАРА. Подпись без tlog + проверка без --insecure-ignore-tlog
# = «no matching signatures». Менять только вместе.
COSIGN_TLOG_SIGN_FLAGS="${COSIGN_TLOG_SIGN_FLAGS:---tlog-upload=false}"
COSIGN_TLOG_VERIFY_FLAGS="${COSIGN_TLOG_VERIFY_FLAGS:---insecure-ignore-tlog}"

# Рабочая директория для ключей — создаётся лениво, чистится на выходе.
_COSIGN_TMPDIR=""

_cosign_log() { printf '[cosign] %s\n' "$*" >&2; }
_cosign_die() { printf '[cosign] FATAL: %s\n' "$*" >&2; exit 1; }

# ── Хуки выхода ──────────────────────────────────────────────────────────────
# trap на EXIT ставится ОДИН раз при подключении библиотеки, а вызывающие
# скрипты добавляют свою уборку через cosign_on_exit. Прямой `trap ... EXIT`
# в вызывающем скрипте затёр бы наш — и приватный ключ остался бы на диске
# раннера. Так этого не случится независимо от порядка вызовов.
_COSIGN_EXIT_HOOKS=""
cosign_on_exit() { _COSIGN_EXIT_HOOKS="${_COSIGN_EXIT_HOOKS} $1"; }

_cosign_run_exit_hooks() {
  local rc=$? h
  for h in $_COSIGN_EXIT_HOOKS; do
    "$h" || true
  done
  [ -n "$_COSIGN_TMPDIR" ] && rm -rf "$_COSIGN_TMPDIR"
  return "$rc"
}
trap _cosign_run_exit_hooks EXIT

_cosign_tmpdir() {
  if [ -z "$_COSIGN_TMPDIR" ]; then
    local old_umask
    old_umask=$(umask)
    umask 077
    _COSIGN_TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/cosign-keys.XXXXXX")
    umask "$old_umask"
  fi
  printf '%s' "$_COSIGN_TMPDIR"
}

# ── cosign_arch ──────────────────────────────────────────────────────────────
# Прод-хост GB10 — aarch64. Хардкод amd64 (как в большинстве примеров из
# документации Sigstore) даёт «не удаётся запустить бинарный файл: ошибка
# формата» — ровно на это наступили при первой ручной установке 2026-08-04.
cosign_arch() {
  case "$(uname -m)" in
    x86_64 | amd64) printf 'amd64' ;;
    aarch64 | arm64) printf 'arm64' ;;
    *) _cosign_die "неподдерживаемая архитектура: $(uname -m)" ;;
  esac
}

# ── cosign_install ───────────────────────────────────────────────────────────
# Идемпотентна: если бинарник уже на месте и нужной версии — ничего не делает.
cosign_install() {
  if [ -x "$COSIGN" ] && "$COSIGN" version 2>/dev/null | grep -q "GitVersion:.*${COSIGN_VERSION#v}"; then
    _cosign_log "cosign ${COSIGN_VERSION} уже установлен: $COSIGN"
    return 0
  fi

  local arch url want got
  arch=$(cosign_arch)
  eval "want=\${COSIGN_SHA256_${arch}}"
  url="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${arch}"

  mkdir -p "$COSIGN_BIN_DIR"
  _cosign_log "скачиваем cosign ${COSIGN_VERSION} (${arch})"
  curl -sfL --retry 3 --retry-delay 2 -m 180 -o "$COSIGN.tmp" "$url" \
    || _cosign_die "не удалось скачать $url"

  got=$(sha256sum "$COSIGN.tmp" | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then
    rm -f "$COSIGN.tmp"
    _cosign_die "checksum не совпал для cosign-linux-${arch}: ожидали $want, получили $got"
  fi
  _cosign_log "checksum OK: $got"

  chmod 0755 "$COSIGN.tmp"
  mv "$COSIGN.tmp" "$COSIGN"
  "$COSIGN" version 2>&1 | grep GitVersion >&2
}

# ── cosign_load_key ──────────────────────────────────────────────────────────
# Приватный ключ приходит CI/CD-переменной COSIGN_KEY в base64 (masked-
# переменные GitLab не переносят многострочный PEM — отсюда base64).
# Пароль — COSIGN_PASSWORD, cosign читает его из env неинтерактивно.
# Экспортирует COSIGN_KEY_FILE.
cosign_load_key() {
  [ -n "${COSIGN_KEY:-}" ] || _cosign_die "COSIGN_KEY не задан (base64 приватного ключа, CI/CD Variable)"
  [ -n "${COSIGN_PASSWORD:-}" ] || _cosign_die "COSIGN_PASSWORD не задан (CI/CD Variable)"

  local dir
  dir=$(_cosign_tmpdir)
  COSIGN_KEY_FILE="$dir/cosign.key"

  # tr -d удаляет переводы строк, которые GitLab/копипаст могли внести в
  # base64-значение: `base64 -d` на них падает "invalid input".
  printf '%s' "$COSIGN_KEY" | tr -d '\r\n' | base64 -d > "$COSIGN_KEY_FILE" \
    || _cosign_die "COSIGN_KEY не декодируется из base64"
  chmod 600 "$COSIGN_KEY_FILE"

  grep -q "BEGIN ENCRYPTED SIGSTORE PRIVATE KEY" "$COSIGN_KEY_FILE" \
    || _cosign_die "декодированный COSIGN_KEY не похож на приватный ключ cosign"

  export COSIGN_KEY_FILE
  _cosign_log "приватный ключ загружен (в tmpfs-каталог, чистится на выходе)"
}

# ── cosign_load_pubkey ───────────────────────────────────────────────────────
# Публичный ключ НЕ секрет, поэтому приоритет у файла в репозитории
# (ci/cosign.pub): он под git, изменение доверенного якоря видно в diff'е и
# проходит review. Переменная COSIGN_PUBLIC_KEY — резервный путь (например,
# на время ротации). Экспортирует COSIGN_PUBKEY_FILE.
cosign_load_pubkey() {
  local repo_pub="${CI_PROJECT_DIR:-$PWD}/ci/cosign.pub"

  if [ -n "${COSIGN_PUBKEY_FILE:-}" ] && [ -s "${COSIGN_PUBKEY_FILE:-}" ]; then
    _cosign_log "публичный ключ: $COSIGN_PUBKEY_FILE (задан вызывающим)"
  elif [ -s "$repo_pub" ]; then
    COSIGN_PUBKEY_FILE="$repo_pub"
    _cosign_log "публичный ключ: ci/cosign.pub (из репозитория)"
  elif [ -n "${COSIGN_PUBLIC_KEY:-}" ]; then
    local dir
    dir=$(_cosign_tmpdir)
    COSIGN_PUBKEY_FILE="$dir/cosign.pub"
    # Принимаем и base64, и «сырой» PEM — обе формы встречаются в чужих гайдах.
    if printf '%s' "$COSIGN_PUBLIC_KEY" | grep -q "BEGIN PUBLIC KEY"; then
      printf '%s\n' "$COSIGN_PUBLIC_KEY" > "$COSIGN_PUBKEY_FILE"
    else
      printf '%s' "$COSIGN_PUBLIC_KEY" | tr -d '\r\n' | base64 -d > "$COSIGN_PUBKEY_FILE" \
        || _cosign_die "COSIGN_PUBLIC_KEY не декодируется из base64"
    fi
    _cosign_log "публичный ключ: из COSIGN_PUBLIC_KEY"
  else
    _cosign_die "нет публичного ключа: положите ci/cosign.pub в репозиторий или задайте COSIGN_PUBLIC_KEY"
  fi

  grep -q "BEGIN PUBLIC KEY" "$COSIGN_PUBKEY_FILE" \
    || _cosign_die "$COSIGN_PUBKEY_FILE не похож на публичный ключ"
  export COSIGN_PUBKEY_FILE
}

# ── cosign_registry_login ────────────────────────────────────────────────────
# cosign берёт креды из docker-config, поэтому пишем изолированный
# $DOCKER_CONFIG вместо ~/.docker (на shell-раннере gb10-shell домашний
# каталог общий между джобами — чужие креды там оставлять нельзя).
cosign_registry_login() {
  local registry="${1:-${CI_REGISTRY:-}}"
  local user="${2:-${CI_REGISTRY_USER:-}}"
  local pass="${3:-${CI_REGISTRY_PASSWORD:-}}"
  [ -n "$registry" ] || _cosign_die "cosign_registry_login: не задан реестр"

  local dir
  dir=$(_cosign_tmpdir)
  export DOCKER_CONFIG="$dir/docker"
  mkdir -p "$DOCKER_CONFIG"
  printf '%s' "$pass" | "$COSIGN" login "$registry" -u "$user" --password-stdin >&2 \
    || _cosign_die "cosign login в $registry не прошёл"
  _cosign_log "залогинены в $registry (DOCKER_CONFIG=$DOCKER_CONFIG)"
}

# ── cosign_resolve_digest ────────────────────────────────────────────────────
# Отдаёт digest, КОТОРЫЙ ВИДИТ COSIGN для данного тега.
#
# Почему не curl по /v2/<repo>/manifests/<tag>: реестр отдаёт РАЗНЫЙ
# Docker-Content-Digest в зависимости от Accept-заголовка (конвертирует
# манифест между OCI и Docker v2). Проверено 2026-08-04 на registry:2 —
# один и тот же образ: OCI-заголовок → sha256:4d06e4b5…, docker-v2 →
# sha256:9bf3af24…. Подпись, поставленную на «неправильный» digest, cosign
# при проверке по тегу не находит: `cosign sign` завершается успешно, а
# `cosign verify` говорит "no signatures found". Класс ошибки, который
# создаёт ЛОЖНОЕ ощущение защищённости, поэтому digest только от инструмента,
# который сам его и разрешает.
cosign_resolve_digest() {
  local ref="$1"
  # triangulate печатает <repo>:sha256-<digest>.sig — вырезаем digest.
  # shellcheck disable=SC2086  # флаги должны разбиться на слова
  "$COSIGN" triangulate $COSIGN_REGISTRY_FLAGS "$ref" 2>/dev/null \
    | sed -n 's/.*:sha256-\([0-9a-f]\{64\}\)\.sig$/sha256:\1/p'
}

# ── cosign_sign_digest ───────────────────────────────────────────────────────
# Подписываем ТОЛЬКО по digest'у: тег — изменяемая ссылка, между «собрали» и
# «подписали» его может перебить другой пайплайн, и подпись уедет не на тот
# образ. digest берётся из --digest-file kaniko (см. ci/cosign.gitlab-ci.yml).
cosign_sign_digest() {
  local image="$1" digest="$2"
  [ -n "$image" ] || _cosign_die "cosign_sign_digest: пустой image"
  case "$digest" in
    sha256:*) : ;;
    *) _cosign_die "cosign_sign_digest: digest должен быть в форме sha256:<hex>, получили '$digest'" ;;
  esac

  _cosign_log "подписываем ${image}@${digest}"
  # shellcheck disable=SC2086  # флаги должны разбиться на слова
  "$COSIGN" sign \
    --key "$COSIGN_KEY_FILE" \
    --yes \
    $COSIGN_TLOG_SIGN_FLAGS \
    $COSIGN_REGISTRY_FLAGS \
    "${image}@${digest}"
}

# ── cosign_verify ────────────────────────────────────────────────────────────
# Возвращает код cosign'а как есть. Наблюдённые коды (2026-08-04, v2.4.3):
#   0  — подпись есть и сделана доверенным ключом;
#   10 — подписи нет вообще ("no signatures found");
#   12 — подпись есть, но чужим ключом ("no matching signatures").
# Оба ненулевых кода обязаны валить джоб — см. cosign_verify_or_die.
#
# ВНИМАНИЕ: не заворачивать вызов в `| tee`/`| tail` — в пайпе `set -e`
# видит код последней команды, и провал проверки станет зелёным джобом.
cosign_verify() {
  local ref="$1"
  # shellcheck disable=SC2086
  "$COSIGN" verify \
    --key "$COSIGN_PUBKEY_FILE" \
    $COSIGN_TLOG_VERIFY_FLAGS \
    $COSIGN_REGISTRY_FLAGS \
    "$ref"
}

# ── cosign_verify_or_die ─────────────────────────────────────────────────────
# Гейт для deploy-джобов: любой ненулевой код cosign'а = остановка пайплайна.
cosign_verify_or_die() {
  local ref="$1" rc=0
  cosign_verify "$ref" || rc=$?
  if [ "$rc" -ne 0 ]; then
    cat >&2 <<EOF
[cosign] ─────────────────────────────────────────────────────────────────
[cosign] ПРОВЕРКА ПОДПИСИ НЕ ПРОЙДЕНА — ДЕПЛОЙ ОСТАНОВЛЕН
[cosign]   образ:      $ref
[cosign]   код выхода: $rc  (10 = подписи нет, 12 = подпись чужим ключом)
[cosign] Неподписанный образ в прод не уезжает. Разбор — docs/supply-chain/cosign-setup.md
[cosign] ─────────────────────────────────────────────────────────────────
EOF
    exit "$rc"
  fi
  _cosign_log "OK: подпись $ref валидна"
}
