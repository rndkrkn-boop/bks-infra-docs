# Минимальный образ для self-теста подписи (scripts/cosign-selftest.sh).
#
# FROM scratch намеренно: тест не должен зависеть от доступности внешнего
# базового образа (Docker Hub) и от архитектуры хоста — прод-хост GB10 это
# aarch64. Образ не предназначен для запуска; для cosign важен только
# манифест, который подписывается и проверяется.
#
# PAYLOAD различает «подписанный» и «неподписанный» варианты: без разного
# содержимого оба тега свелись бы к ОДНОМУ digest'у, подпись накрыла бы сразу
# оба, и негативный тест стал бы ложно-зелёным (проверено 2026-08-04).
FROM scratch
ARG PAYLOAD="bks cosign selftest"
COPY payload.txt /payload.txt
LABEL org.opencontainers.image.title="bks-cosign-selftest" \
      org.opencontainers.image.description="Throwaway image used to prove cosign sign/verify gating" \
      org.opencontainers.image.vendor="bks" \
      bks.selftest.payload="${PAYLOAD}"
