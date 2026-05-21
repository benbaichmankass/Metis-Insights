#!/usr/bin/env bash
# systemd ExecStart wrapper for the headless IB Gateway via IBC.
#
# IBC's ibcstart.sh REQUIRES the Gateway major version as its first arg plus
# --gateway / --mode / --ibc-ini flags; called bare it prints usage and exits
# 0 (which made ib-gateway.service flap: start → exit → auto-restart). This
# wrapper auto-detects the installed Gateway version (so we never hardcode it)
# and execs ibcstart.sh with the correct arguments. Credentials come from
# config.ini (rendered by install_ib_gateway.sh) — not passed here.
#
# Run under xvfb by the systemd unit. Reads IBC_PATH / TWS_PATH / IBC_INI /
# TRADING_MODE from the unit Environment / EnvironmentFile.
set -euo pipefail

IBC_PATH="${IBC_PATH:-/opt/ibc}"
TWS_PATH="${TWS_PATH:-/home/ubuntu/Jts}"
IBC_INI="${IBC_INI:-/opt/ibc/config.ini}"
TRADING_MODE="${TRADING_MODE:-paper}"

# Detect the installed Gateway version directory. Recent standalone builds
# install under $TWS_PATH/ibgateway/<version>; older layouts use $TWS_PATH/<version>.
detect_version() {
  local base v
  for base in "${TWS_PATH}/ibgateway" "${TWS_PATH}"; do
    [ -d "${base}" ] || continue
    v=$(ls -1 "${base}" 2>/dev/null | grep -E '^[0-9]+([.-][0-9]+)*$' | sort -V | tail -1)
    if [ -n "${v}" ]; then printf '%s' "${v}"; return 0; fi
  done
  return 1
}

VERSION="$(detect_version || true)"
if [ -z "${VERSION}" ]; then
  echo "[ib_gateway_start] ERROR: could not detect IB Gateway version." >&2
  echo "[ib_gateway_start] Layout of ${TWS_PATH}:" >&2
  ls -la "${TWS_PATH}" 2>&1 | sed 's/^/[ib_gateway_start]   /' >&2 || true
  ls -la "${TWS_PATH}/ibgateway" 2>&1 | sed 's/^/[ib_gateway_start]   /' >&2 || true
  exit 5
fi

echo "[ib_gateway_start] launching IB Gateway v${VERSION} mode=${TRADING_MODE} ibc=${IBC_PATH}"
exec "${IBC_PATH}/scripts/ibcstart.sh" "${VERSION}" --gateway \
  "--mode=${TRADING_MODE}" \
  "--ibc-path=${IBC_PATH}" \
  "--ibc-ini=${IBC_INI}" \
  "--tws-path=${TWS_PATH}"
