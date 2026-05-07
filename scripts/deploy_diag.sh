#!/usr/bin/env bash
# One-command deploy for S-051 diag surface + claude-vm-runner EROFS fix.
#
# Usage (from the operator's laptop):
#   ssh ubuntu@<vm-host> 'cd ict-trading-bot && git pull && sudo bash scripts/deploy_diag.sh'
#
# Or on the VM directly:
#   cd ~/ict-trading-bot && git pull && sudo bash scripts/deploy_diag.sh
#
# Idempotent — safe to re-run. Preserves an existing DIAG_READ_TOKEN.
# To rotate: remove the DIAG_READ_TOKEN= line from /etc/ict-trader/web-api.env
# and re-run this script (it will mint a fresh one).

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (use sudo)" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="/etc/ict-trader/web-api.env"
RUNNER_UNIT_SRC="${REPO_ROOT}/deploy/claude-vm-runner@.service"
RUNNER_UNIT_DST="/etc/systemd/system/claude-vm-runner@.service"

# ---------------------------------------------------------------------------
# 0. /opt/ict-trading-bot symlink — required by ict-web-api.service.
# ---------------------------------------------------------------------------
# The web-api unit has WorkingDirectory=/opt/ict-trading-bot, but the only
# checkout on this VM lives at /home/ubuntu/ict-trading-bot. If the symlink
# is missing (post-reboot wipe, manual cleanup, fresh VM), the API CHDIRs to
# a non-existent path and crashloops every 5s with status=200/CHDIR. Found
# 2026-05-07 after the first deploy left the API in a flap loop until
# someone manually created the link.

if [[ ! -e /opt/ict-trading-bot ]]; then
    ln -s /home/ubuntu/ict-trading-bot /opt/ict-trading-bot
    echo "/opt/ict-trading-bot symlink created → /home/ubuntu/ict-trading-bot"
elif [[ -L /opt/ict-trading-bot ]]; then
    echo "/opt/ict-trading-bot symlink already present."
else
    echo "/opt/ict-trading-bot exists as a real directory — leaving alone."
fi

# ---------------------------------------------------------------------------
# 1. DIAG_READ_TOKEN — preserve if set, mint if not.
# ---------------------------------------------------------------------------

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
chmod 0640 "$ENV_FILE"
chown root:ubuntu "$ENV_FILE"

if grep -q '^DIAG_READ_TOKEN=' "$ENV_FILE"; then
    TOKEN_OUT="$(grep '^DIAG_READ_TOKEN=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
    echo "DIAG_READ_TOKEN already set in ${ENV_FILE} — preserving."
else
    if ! command -v openssl >/dev/null 2>&1; then
        echo "ERROR: openssl not found; install it or set DIAG_READ_TOKEN manually." >&2
        exit 1
    fi
    TOKEN_OUT="$(openssl rand -hex 32)"
    printf 'DIAG_READ_TOKEN=%s\n' "$TOKEN_OUT" >> "$ENV_FILE"
    echo "DIAG_READ_TOKEN generated and written to ${ENV_FILE}."
fi

# ---------------------------------------------------------------------------
# 2. claude-vm-runner@.service — apply the EROFS fix
#    (ReadWritePaths=-/home/ubuntu/.claude.json) if drift detected.
# ---------------------------------------------------------------------------

if [[ -f "$RUNNER_UNIT_SRC" ]]; then
    if ! cmp -s "$RUNNER_UNIT_SRC" "$RUNNER_UNIT_DST" 2>/dev/null; then
        if [[ -f "$RUNNER_UNIT_DST" ]]; then
            BACKUP="${RUNNER_UNIT_DST}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
            cp "$RUNNER_UNIT_DST" "$BACKUP"
            echo "Backed up existing runner unit to ${BACKUP}."
        fi
        cp "$RUNNER_UNIT_SRC" "$RUNNER_UNIT_DST"
        systemctl daemon-reload
        echo "Runner unit installed; daemon-reload run."
    else
        echo "Runner unit already up to date — no change."
    fi
else
    echo "WARNING: ${RUNNER_UNIT_SRC} not found; skipping runner unit update." >&2
fi

# ---------------------------------------------------------------------------
# 3. Restart ict-web-api so it picks up DIAG_READ_TOKEN.
# ---------------------------------------------------------------------------

systemctl restart ict-web-api.service
echo "ict-web-api.service restarted."

# Brief pause for the API to bind its port before the smoke-test hint below.
sleep 1

# ---------------------------------------------------------------------------
# 4. Output the token for the operator to share with PM-side Claude.
# ---------------------------------------------------------------------------
# We deliberately don't auto-detect the public host: `hostname -I` returns
# the internal VPC IP first on Oracle Cloud, which is unreachable from
# outside. The operator knows their VM's public IP — printing 127.0.0.1
# for the local smoke test plus a templated external URL is unambiguous.

cat <<EOF

=== DIAG ENDPOINT READY ===
  Token: ${TOKEN_OUT}

Local smoke test (run on the VM):
  curl -sS -H "Authorization: Bearer ${TOKEN_OUT}" \\
       http://127.0.0.1:8001/api/diag/snapshot | head -c 800

External access (PM-side Claude):
  http://<VM_PUBLIC_IP>:8001/api/diag/snapshot
  Port 8001 must be open in the VM's firewall / Oracle Cloud security
  list for the source you're calling from.

Share the token with the PM-side session. To rotate later, delete the
DIAG_READ_TOKEN= line from ${ENV_FILE} and re-run this script.
EOF
