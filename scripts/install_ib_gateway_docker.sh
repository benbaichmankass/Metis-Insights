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

# Resource caps for the gateway container (2026-06-10, trader-connection-
# stability). The IB Gateway is a heavy Java GUI app under Xvfb; an
# unauthenticated re-login loop during IBKR's reset window can spin it hot, and
# on the shared 2-core live VM that starved the trader's single-threaded main
# loop (loadavg ~10, the 2026-06-10 cascade that wedged the trader ~25 min).
# Cap CPU + memory so the gateway can NEVER dominate the box — the trader keeps
# ticking even while the gateway churns, and `docker restart` (the watchdog's
# recovery path) preserves these flags so restarts stay capped too. Setting
# --memory-swap == --memory disables container swap (we saw kswapd thrashing
# under the cascade). Override via env on the VM.
IB_GATEWAY_CPUS="${IB_GATEWAY_CPUS:-0.75}"
IB_GATEWAY_MEMORY="${IB_GATEWAY_MEMORY:-1500m}"

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

# 4. (Re)start the container via `docker run --env-file`.
#
# IMPORTANT: do NOT use compose `env_file:` — Docker Compose v2 performs
# $-interpolation on env_file values, which mangles a password containing '$'
# (it expanded "$Word" as a variable and blanked it → wrong password → failed
# logins). `docker run --env-file` reads the file LITERALLY, passing the
# password exactly as stored. (COMPOSE_SRC/COMPOSE_DST kept for reference.)
log "removing any prior container (stops a mis-credentialed one)"
sudo docker rm -f ib-gateway 2>/dev/null || true

IMAGE="ghcr.io/gnzsnz/ib-gateway:stable"
log "pulling image"
sudo docker pull "${IMAGE}"
log "starting container (literal --env-file)"
# Port map MUST target the socat relay port (4004 paper / 4003 live), NOT the
# Gateway's own port (4002/4001). Inside the gnzsnz image IB Gateway binds its
# API on 127.0.0.1:4002 (localhost-ONLY), so a connection arriving via Docker's
# NAT bridge (a non-loopback source IP) is refused — the prior `-p …:4002` map
# hit that localhost-only port and the handshake silently timed out
# ("API connection failed: TimeoutError()"). socat listens on 4004, accepts the
# non-localhost bridge connection, and relays it to the Gateway from 127.0.0.1
# (which IBC trusts), so the handshake completes. This mirrors the upstream
# compose mapping `127.0.0.1:4002:4004`; the bot connects to host 4002
# (config/accounts.yaml ib_paper.ib_port).
sudo docker run -d \
  --name ib-gateway \
  --restart unless-stopped \
  --cpus "${IB_GATEWAY_CPUS}" \
  --memory "${IB_GATEWAY_MEMORY}" \
  --memory-swap "${IB_GATEWAY_MEMORY}" \
  --env-file "${DOCKER_ENV}" \
  -p 127.0.0.1:4002:4004 \
  "${IMAGE}"
log "container resource caps: --cpus=${IB_GATEWAY_CPUS} --memory=${IB_GATEWAY_MEMORY} (swap disabled)"
sleep 8
log "container state:"
sudo docker ps --filter name=ib-gateway --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
log "done — approve the IBKR Mobile 2FA tap to complete login."
