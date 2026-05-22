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

# Detect the installed Gateway version. The current standalone build installs
# FLAT in $TWS_PATH (no version subdir) — the version only appears in the
# launcher name, e.g. "IB Gateway 10.45.desktop". Older install4j layouts use
# a numeric version subdir under ibgateway/ or $TWS_PATH. Try both.
detect_version() {
  local base v
  # 1) Flat standalone: parse "<...> 10.45.desktop".
  v=$(ls "${TWS_PATH}"/*.desktop 2>/dev/null \
        | sed -nE 's#.*[^0-9]([0-9]+\.[0-9]+)\.desktop$#\1#p' | head -1)
  if [ -n "${v}" ]; then printf '%s' "${v}"; return 0; fi
  # 2) install4j version subdir.
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

# IBC's offline-layout check looks for jars at $TWS_PATH/$VERSION/jars, but the
# modern standalone installer lays everything out FLAT in $TWS_PATH (jars at
# $TWS_PATH/jars, version only in the .desktop name). Bridge with a symlink
# $TWS_PATH/$VERSION -> $TWS_PATH so $TWS_PATH/$VERSION/jars (and the rest)
# resolve. Self-referential but only used for direct path lookups, not
# recursive traversal.
if [ ! -e "${TWS_PATH}/${VERSION}/jars" ] && [ -d "${TWS_PATH}/jars" ]; then
  echo "[ib_gateway_start] bridging flat install: ${TWS_PATH}/${VERSION} -> ${TWS_PATH}"
  ln -sfn "${TWS_PATH}" "${TWS_PATH}/${VERSION}" 2>/dev/null || true
fi

echo "[ib_gateway_start] launching IB Gateway v${VERSION} mode=${TRADING_MODE} ibc=${IBC_PATH}"
exec "${IBC_PATH}/scripts/ibcstart.sh" "${VERSION}" --gateway \
  "--mode=${TRADING_MODE}" \
  "--ibc-path=${IBC_PATH}" \
  "--ibc-ini=${IBC_INI}" \
  "--tws-path=${TWS_PATH}"
