#!/usr/bin/env bash
# VM-side step of the IB Gateway provisioning. Installs the staged credential
# env file (scp'd by the workflow over the encrypted SSH channel — never in
# run logs), then runs the idempotent Docker installer
# (scripts/install_ib_gateway_docker.sh) and restarts the Gateway container.
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
# root-only 0600 file broke the (now-removed native) installer with "Permission denied".
# 0600 keeps it secret; ubuntu already holds the creds (it runs the Gateway).
sudo install -m 0600 -o ubuntu -g ubuntu "${ENV_STAGED}" "${ENV_FILE}"
shred -u "${ENV_STAGED}" 2>/dev/null || rm -f "${ENV_STAGED}"

# Mark this host as the gateway VM (BL-20260622-GATEWAY-VM-ROLE). The role
# marker drives two things: scripts/install_systemd_units.sh enables the
# gateway-only timers (ict-ib-gateway-{watchdog,reset}) only where it is
# "gateway", and scripts/deploy_pull_restart.sh takes its minimal gateway
# branch (no pip / no trader-service restart). Provisioning never set it, so
# the gateway timers survived only because they were hand-enabled once, and an
# enabled git-sync would have run the full trader deploy here. Idempotent.
echo "[provision_ib_gateway] marking host role: /etc/ict-vm-role=gateway"
echo gateway | sudo tee /etc/ict-vm-role >/dev/null

echo "[provision_ib_gateway] running Docker installer (gnzsnz/ib-gateway)"
bash "${REPO_ROOT}/scripts/install_ib_gateway_docker.sh"

echo "----- ib-gateway container logs (last 60 lines) -----"
# Credential values are redacted by the workflow comment-back step before
# posting; the image does not log the password.
sudo docker logs --tail 60 ib-gateway 2>&1 | tail -60 || true
echo "[provision_ib_gateway] done — if login reached the 2FA step, approve the IBKR Mobile tap."
