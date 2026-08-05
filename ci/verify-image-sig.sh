#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# verify-image-sig.sh — проверка подписей образов из ci/trusted-images.txt.
#
# Это ГЕЙТ, а не отчёт: любой провал (нет подписи, битый формат строки, не
# распознан cosign, недостаёт политики доверия) обязан вернуть ненулевой код.
# Раньше скрипт логировал "⚠️ No signature found (expected for internal
# images)" и всё равно завершался с exit 0 — джоба физически не могла
# провалиться, независимо от того, что происходило с образами.
#
# Модель доверия — две ветки:
#   - внутренний реестр ($REGISTRY/...)  → cosign verify --key ci/cosign.pub
#     (см. ci/cosign-lib.sh: cosign_load_pubkey, cosign_verify).
#   - всё остальное (внешние/базовые образы) → keyless-проверка с УЗКОЙ
#     политикой: --certificate-identity(-regexp) + --certificate-oidc-issuer
#     из COSIGN_CERT_IDENTITY[_REGEXP]/COSIGN_CERT_OIDC_ISSUER. Wildcard
#     ".*" здесь не подставляется НИКОГДА: если политика не задана, это
#     провал конфигурации, а не молчаливое доверие первому встречному сертификату.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ci/cosign-lib.sh
. "$SCRIPT_DIR/cosign-lib.sh"

REGISTRY="${REGISTRY:-192.168.2.180:5050}"
TRUSTED_IMAGES_FILE="${TRUSTED_IMAGES_FILE:-$SCRIPT_DIR/trusted-images.txt}"

echo "🔐 Base Image Signature Verification"
echo "====================================="

if [ ! -f "$TRUSTED_IMAGES_FILE" ]; then
  echo "[verify-image-sig] FATAL: список доверенных образов не найден: $TRUSTED_IMAGES_FILE" >&2
  exit 1
fi

# cosign обязателен для гейта: недоступность бинарника (сеть/checksum) валит
# джобу через _cosign_die — никакого "cosign не найден, пропускаем проверку".
cosign_install
cosign_load_pubkey

FAILED=0
CHECKED=0

# IMAGE@sha256:<64 hex> — pinned digest обязателен, тег без digest'а не пускаем.
DIGEST_RE='^([^[:space:]@]+)@(sha256:[0-9a-f]{64})$'

while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line//[[:space:]]/}" ]] && continue

  CHECKED=$((CHECKED + 1))
  echo ""
  echo "Verifying: $line"

  if [[ ! "$line" =~ $DIGEST_RE ]]; then
    echo "[verify-image-sig] ПРОВАЛ: строка не в формате IMAGE@sha256:<64 hex> (pinned digest обязателен): $line" >&2
    FAILED=$((FAILED + 1))
    continue
  fi

  IMG="${BASH_REMATCH[1]}"
  DIGEST="${BASH_REMATCH[2]}"
  REF="${IMG}@${DIGEST}"

  if [ "$IMG" = "$REGISTRY" ] || [[ "$IMG" == "$REGISTRY/"* ]]; then
    echo "внутренний образ — проверка ключом (ci/cosign.pub)"
    # stderr cosign'а НЕ подавляется: он идёт прямо в лог джобы.
    if cosign_verify "$REF"; then
      echo "✅ подпись валидна (key-based): $REF"
    else
      echo "[verify-image-sig] ПРОВАЛ: подпись не найдена/невалидна для $REF" >&2
      FAILED=$((FAILED + 1))
    fi
    continue
  fi

  if [ -z "${COSIGN_CERT_IDENTITY:-}" ] && [ -z "${COSIGN_CERT_IDENTITY_REGEXP:-}" ]; then
    echo "[verify-image-sig] ПРОВАЛ: для внешнего образа $REF не задана политика доверия (COSIGN_CERT_IDENTITY или COSIGN_CERT_IDENTITY_REGEXP). Wildcard '.*' запрещён политикой." >&2
    FAILED=$((FAILED + 1))
    continue
  fi
  if [ -z "${COSIGN_CERT_OIDC_ISSUER:-}" ]; then
    echo "[verify-image-sig] ПРОВАЛ: для внешнего образа $REF не задан COSIGN_CERT_OIDC_ISSUER." >&2
    FAILED=$((FAILED + 1))
    continue
  fi

  identity_flags=(--certificate-oidc-issuer "$COSIGN_CERT_OIDC_ISSUER")
  if [ -n "${COSIGN_CERT_IDENTITY:-}" ]; then
    identity_flags+=(--certificate-identity "$COSIGN_CERT_IDENTITY")
  else
    identity_flags+=(--certificate-identity-regexp "$COSIGN_CERT_IDENTITY_REGEXP")
  fi

  echo "внешний образ — keyless-проверка (certificate-identity + oidc-issuer)"
  # shellcheck disable=SC2086  # COSIGN_REGISTRY_FLAGS должен разбиться на слова
  if "$COSIGN" verify "${identity_flags[@]}" $COSIGN_REGISTRY_FLAGS "$REF"; then
    echo "✅ подпись валидна (keyless): $REF"
  else
    echo "[verify-image-sig] ПРОВАЛ: подпись не найдена или не совпадает с политикой доверия для $REF" >&2
    FAILED=$((FAILED + 1))
  fi
done < "$TRUSTED_IMAGES_FILE"

echo ""
echo "====================================="
echo "Проверено записей: $CHECKED, провалов: $FAILED"
if [ "$FAILED" -eq 0 ]; then
  echo "✅ Image verification complete"
  exit 0
else
  echo "❌ $FAILED image(s) failed verification" >&2
  exit 1
fi
