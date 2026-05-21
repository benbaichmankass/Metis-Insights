#!/usr/bin/env bash
# Idempotent installer for the headless IB Gateway + IBC on the live VM.
#
# Installs: xvfb (virtual display for the Gateway GUI), the IB Gateway
# standalone offline build (bundles its own JRE), and IBC (the auto-login
# driver). Renders /opt/ibc/config.ini from deploy/ibc/config.ini.template +
# /etc/ict/ib-gateway.env, and installs the ib-gateway systemd unit.
#
# Run by .github/workflows/provision-ib-gateway.yml over SSH AFTER
# scripts/ops/provision_ib_gateway.sh has written /etc/ict/ib-gateway.env
# from the IB_USERNAME / IB_PASSWORD repo secrets. Safe to re-run.
#
# Credentials are never read or printed here — they live only in the
# root-owned env file and are substituted into config.ini at render time.
#
# NOTE: this touches no trading code and no other systemd unit. The Gateway
# runs independently of ict-trader-live so bot restarts never re-auth IBKR.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IBC_VERSION="${IBC_VERSION:-3.20.0}"
IBC_PATH="${IBC_PATH:-/opt/ibc}"
TWS_PATH="${TWS_PATH:-/home/ubuntu/Jts}"
ENV_FILE="${IB_ENV_FILE:-/etc/ict/ib-gateway.env}"
UNIT_SRC="${REPO_ROOT}/deploy/ib-gateway.service"
CONFIG_TEMPLATE="${REPO_ROOT}/deploy/ibc/config.ini.template"

log() { echo "[install_ib_gateway] $*"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} missing — run scripts/ops/provision_ib_gateway.sh first" >&2
  exit 2
fi

# 1. System deps (xvfb for the headless GUI; envsubst from gettext-base).
log "installing apt deps (xvfb, unzip, curl, gettext-base)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  xvfb unzip curl gettext-base

# 2. IB Gateway standalone (offline build bundles a JRE). Idempotent: skip
#    if already installed.
if [[ ! -d "${TWS_PATH}" ]]; then
  log "downloading IB Gateway standalone installer"
  tmp_installer="$(mktemp --suffix=.sh)"
  curl -sSL \
    "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh" \
    -o "${tmp_installer}"
  chmod +x "${tmp_installer}"
  log "installing IB Gateway to ${TWS_PATH} (quiet)"
  # InstallBuilder quiet mode; -dir sets the install path.
  "${tmp_installer}" -q -dir "${TWS_PATH}" || {
    echo "ERROR: IB Gateway installer failed" >&2; exit 3;
  }
  rm -f "${tmp_installer}"
else
  log "IB Gateway already present at ${TWS_PATH} — skipping download"
fi

# 3. IBC (auto-login driver).
if [[ ! -x "${IBC_PATH}/scripts/ibcstart.sh" ]]; then
  log "installing IBC ${IBC_VERSION} to ${IBC_PATH}"
  tmp_ibc="$(mktemp --suffix=.zip)"
  curl -sSL \
    "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" \
    -o "${tmp_ibc}"
  sudo mkdir -p "${IBC_PATH}"
  sudo unzip -o -q "${tmp_ibc}" -d "${IBC_PATH}"
  sudo chmod -R +x "${IBC_PATH}/scripts" 2>/dev/null || true
  sudo chown -R ubuntu:ubuntu "${IBC_PATH}"
  rm -f "${tmp_ibc}"
else
  log "IBC already present at ${IBC_PATH} — skipping download"
fi

# 4. Render config.ini from the template + env file (no creds in repo).
log "rendering ${IBC_PATH}/config.ini from template"
# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a
: "${IB_USERNAME:?IB_USERNAME missing in ${ENV_FILE}}"
: "${IB_PASSWORD:?IB_PASSWORD missing in ${ENV_FILE}}"
: "${TRADING_MODE:=paper}"
: "${IB_PORT:=7497}"
export IB_USERNAME IB_PASSWORD TRADING_MODE IB_PORT
tmp_cfg="$(mktemp)"
envsubst '${IB_USERNAME} ${IB_PASSWORD} ${TRADING_MODE} ${IB_PORT}' \
  < "${CONFIG_TEMPLATE}" > "${tmp_cfg}"
sudo install -m 0600 -o ubuntu -g ubuntu "${tmp_cfg}" "${IBC_PATH}/config.ini"
rm -f "${tmp_cfg}"

# 5. systemd unit (independent of ict-trader-live).
log "installing ib-gateway.service"
sudo install -m 0644 "${UNIT_SRC}" /etc/systemd/system/ib-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable ib-gateway.service
log "done. Start with: sudo systemctl restart ib-gateway.service"
log "Then approve the IBKR Mobile 2FA prompt on your phone."
