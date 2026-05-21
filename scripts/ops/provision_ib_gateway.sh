#!/usr/bin/env bash
# VM-side step of the IB Gateway provisioning. Installs the staged credential
# env file (scp'd by the workflow over the encrypted SSH channel — never in
# run logs), then runs the idempotent installer and restarts the Gateway.
#
# Invoked by .github/workflows/provision-ib-gateway.yml after it scps the
# rendered env file to ${IB_ENV_STAGED}. The workflow renders that file from
# the IB_USERNAME / IB_PASSWORD repo secrets on the runner; this script only
# moves it into place (root-owned, 0600) and never echoes its contents.
#
# Exits 2 if the staged file is missing so a failed transfer fails loudly
# rather than installing blank creds.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/ubuntu/ict-trading-bot}"
ENV_FILE="${IB_ENV_FILE:-/etc/ict/ib-gateway.env}"
ENV_STAGED="${IB_ENV_STAGED:-/tmp/ib-gateway.env.staged}"

if [[ ! -f "${ENV_STAGED}" ]]; then
  echo "ERROR: staged env file ${ENV_STAGED} not found (scp step failed?)" >&2
  exit 2
fi

echo "[provision_ib_gateway] installing ${ENV_FILE} (0600, ubuntu) from staged file"
sudo mkdir -p "$(dirname "${ENV_FILE}")"
# Owned by ubuntu (not root): the IBC service runs as User=ubuntu and the
# installer (run as ubuntu) sources this file to render config.ini. A
# root-only 0600 file broke install_ib_gateway.sh with "Permission denied".
# 0600 keeps it secret; ubuntu already holds the creds (it runs the Gateway).
sudo install -m 0600 -o ubuntu -g ubuntu "${ENV_STAGED}" "${ENV_FILE}"
shred -u "${ENV_STAGED}" 2>/dev/null || rm -f "${ENV_STAGED}"

echo "[provision_ib_gateway] running installer"
bash "${REPO_ROOT}/scripts/install_ib_gateway.sh"

echo "[provision_ib_gateway] restarting ib-gateway.service"
sudo systemctl restart ib-gateway.service || true
# Give IBC time to launch the Gateway + reach the login/2FA step before we
# snapshot state (the GUI boot under xvfb takes ~20-40s).
sleep 30
sudo systemctl --no-pager --full status ib-gateway.service | head -25 || true
echo "----- ib-gateway journal (last 50 lines) -----"
# Credential values are redacted by the workflow comment-back step before
# posting; IBC does not log the password regardless.
sudo journalctl -u ib-gateway.service -n 50 --no-pager 2>&1 | tail -50 || true
echo "[provision_ib_gateway] done — if login reached the 2FA step, approve the IBKR Mobile tap."
