#!/usr/bin/env bash
# Install / (re)start the headless IB Gateway via the gnzsnz/ib-gateway Docker
# image. Replaces the native IBC install (incompatible with the modern flat
# standalone Gateway). Idempotent; run by provision_ib_gateway.sh on the VM.
#
# Reads creds LITERALLY from /etc/ict/ib-gateway.env (no shell eval — a special
# char in the password must never be evaluated/echoed) and renders the Docker
# env file the image expects (TWS_USERID/TWS_PASSWORD/TRADING_MODE). Disables
# the old native ib-gateway.service to avoid conflict.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
ENV_FILE="${IB_ENV_FILE:-/etc/ict/ib-gateway.env}"
DOCKER_ENV="${IB_DOCKER_ENV:-/etc/ict/ib-gateway-docker.env}"
COMPOSE_SRC="${REPO_ROOT}/deploy/ib-gateway.compose.yml"
COMPOSE_DST="${IB_COMPOSE:-/etc/ict/ib-gateway.compose.yml}"

log() { echo "[install_ib_gateway_docker] $*"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} missing — provision must stage it first" >&2
  exit 2
fi

# Literal reads — never source the secrets file.
read_val() { sed -n "s/^$1=//p" "${ENV_FILE}" | head -n1; }
IB_USERNAME="$(read_val IB_USERNAME)"
IB_PASSWORD="$(read_val IB_PASSWORD)"
TRADING_MODE="$(read_val TRADING_MODE)"; TRADING_MODE="${TRADING_MODE:-paper}"
if [[ -z "${IB_USERNAME}" || -z "${IB_PASSWORD}" ]]; then
  echo "ERROR: IB_USERNAME/IB_PASSWORD missing in ${ENV_FILE}" >&2
  exit 4
fi

# 1. Docker engine + compose plugin.
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker.io + compose plugin"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 \
    || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
else
  log "docker present: $(docker --version 2>/dev/null || echo '?')"
fi
# Resolve the compose command (plugin `docker compose` or legacy `docker-compose`).
if sudo docker compose version >/dev/null 2>&1; then
  COMPOSE=(sudo docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(sudo docker-compose)
else
  log "compose plugin missing — installing docker-compose-v2"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 || true
  COMPOSE=(sudo docker compose)
fi

# 2. Retire the old native unit so it can't conflict on the port.
sudo systemctl disable --now ib-gateway.service 2>/dev/null || true

# 3. Render the image's env file (root, 0600). No values are printed.
log "rendering ${DOCKER_ENV} (mode=${TRADING_MODE})"
sudo mkdir -p "$(dirname "${DOCKER_ENV}")"
umask 077
tmp_env="$(mktemp)"
{
  echo "TWS_USERID=${IB_USERNAME}"
  echo "TWS_PASSWORD=${IB_PASSWORD}"
  echo "TRADING_MODE=${TRADING_MODE}"
  echo "READ_ONLY_API=no"
  # On a 2FA timeout, restart and re-prompt rather than exiting dead. The
  # password is correct, so timeouts (not wrong-password) carry low lockout risk.
  echo "TWOFA_TIMEOUT_ACTION=restart"
  echo "AUTO_RESTART_TIME=11:59 PM"
  echo "TIME_ZONE=Etc/UTC"
} > "${tmp_env}"
sudo install -m 0600 -o root -g root "${tmp_env}" "${DOCKER_ENV}"
rm -f "${tmp_env}"

# 4. Install the compose file + bring the container up.
sudo install -m 0644 "${COMPOSE_SRC}" "${COMPOSE_DST}"
log "pulling image"
"${COMPOSE[@]}" -f "${COMPOSE_DST}" pull
log "starting container"
"${COMPOSE[@]}" -f "${COMPOSE_DST}" up -d
sleep 8
log "container state:"
"${COMPOSE[@]}" -f "${COMPOSE_DST}" ps || true
log "done — approve the IBKR Mobile 2FA tap to complete login."
