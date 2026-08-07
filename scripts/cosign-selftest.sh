#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# cosign-selftest.sh — доказывает, что ci/verify-image-sig.sh реально гейтит:
# подписанный образ проходит, НЕПОДПИСАННЫЙ — валит джобу (а не варнинг).
#
# Использует ci/cosign-test-image/ (FROM scratch, PAYLOAD различает варианты,
# чтобы у "подписан"/"не подписан" были РАЗНЫЕ digest'ы — иначе подпись одного
# накрыла бы и другой, и негативный кейс стал бы ложно-зелёным).
#
# Ключ — одноразовый (cosign generate-key-pair), только для этого прогона:
# самотест обязан работать независимо от того, провизионирован ли уже
# прод-ключ (COSIGN_KEY/ci/cosign.pub). Локальный registry слушает 127.0.0.1 —
# Docker считает 127.0.0.0/8 insecure по умолчанию, менять daemon.json на
# раннере не требуется.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=ci/cosign-lib.sh
. "$REPO_ROOT/ci/cosign-lib.sh"

REGISTRY_IMAGE="registry:2.8.3"
LOCAL_REGISTRY_PORT="${LOCAL_REGISTRY_PORT:-5511}"
LOCAL_REGISTRY="127.0.0.1:${LOCAL_REGISTRY_PORT}"
REGISTRY_CONTAINER="bks-cosign-selftest-registry"

_selftest_cleanup() {
  docker rm -f "$REGISTRY_CONTAINER" >/dev/null 2>&1 || true
}
cosign_on_exit _selftest_cleanup

cosign_install

echo "[selftest] стартуем эфемерный локальный registry на $LOCAL_REGISTRY"
docker rm -f "$REGISTRY_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$REGISTRY_CONTAINER" -p "${LOCAL_REGISTRY_PORT}:5000" "$REGISTRY_IMAGE" >/dev/null

ready=0
for _ in $(seq 1 30); do
  if curl -sf "http://${LOCAL_REGISTRY}/v2/" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || _cosign_die "локальный registry не поднялся за 30с"

KEYDIR=$(mktemp -d)
(
  cd "$KEYDIR"
  COSIGN_PASSWORD="selftest" "$COSIGN" generate-key-pair >/dev/null
)
export COSIGN_PUBKEY_FILE="$KEYDIR/cosign.pub"
export COSIGN_KEY_FILE="$KEYDIR/cosign.key"
export COSIGN_PASSWORD="selftest"

build_and_push() {
  local payload="$1" tag="$2" ref
  ref="${LOCAL_REGISTRY}/cosign-selftest:${tag}"
  docker build --build-arg "PAYLOAD=${payload}" -t "$ref" "$REPO_ROOT/ci/cosign-test-image" >/dev/null
  docker push "$ref" >/dev/null
  cosign_resolve_digest "$ref"
}

echo "[selftest] собираем и пушим signed/unsigned варианты"
SIGNED_DIGEST=$(build_and_push "selftest signed" "signed")
UNSIGNED_DIGEST=$(build_and_push "selftest unsigned" "unsigned")

[ "$SIGNED_DIGEST" != "$UNSIGNED_DIGEST" ] \
  || _cosign_die "signed/unsigned варианты дали одинаковый digest — негативный кейс не показателен"

SIGNED_REF="${LOCAL_REGISTRY}/cosign-selftest@${SIGNED_DIGEST}"
UNSIGNED_REF="${LOCAL_REGISTRY}/cosign-selftest@${UNSIGNED_DIGEST}"

echo "[selftest] подписываем ТОЛЬКО signed-вариант"
cosign_sign_digest "${LOCAL_REGISTRY}/cosign-selftest" "$SIGNED_DIGEST"

echo "[selftest] позитив (низкий уровень): cosign_verify signed-варианта"
cosign_verify "$SIGNED_REF" \
  || _cosign_die "позитивный кейс сломан: подписанный образ не проходит cosign_verify"

# ── Негативный кейс: сам гейт, а не только библиотека ──────────────────────
echo "[selftest] негатив: verify-image-sig.sh обязан провалить неподписанный образ"
UNSIGNED_LIST=$(mktemp)
printf '%s\n' "$UNSIGNED_REF" > "$UNSIGNED_LIST"

rc=0
REGISTRY="$LOCAL_REGISTRY" TRUSTED_IMAGES_FILE="$UNSIGNED_LIST" COSIGN_PUBKEY_FILE="$COSIGN_PUBKEY_FILE" \
  bash "$REPO_ROOT/ci/verify-image-sig.sh" || rc=$?
rm -f "$UNSIGNED_LIST"

if [ "$rc" -eq 0 ]; then
  _cosign_die "НЕГАТИВНЫЙ КЕЙС ПРОВАЛЕН: verify-image-sig.sh вернул 0 для неподписанного образа $UNSIGNED_REF"
fi
echo "[selftest] OK: неподписанный образ провалил verify-image-sig.sh (exit $rc)"

echo "[selftest] позитив: verify-image-sig.sh обязан пропустить подписанный образ"
SIGNED_LIST=$(mktemp)
printf '%s\n' "$SIGNED_REF" > "$SIGNED_LIST"
REGISTRY="$LOCAL_REGISTRY" TRUSTED_IMAGES_FILE="$SIGNED_LIST" COSIGN_PUBKEY_FILE="$COSIGN_PUBKEY_FILE" \
  bash "$REPO_ROOT/ci/verify-image-sig.sh"
rm -f "$SIGNED_LIST"

echo "[selftest] OK: оба кейса (подписан → проходит, не подписан → валит джобу) ведут себя как ожидается"
