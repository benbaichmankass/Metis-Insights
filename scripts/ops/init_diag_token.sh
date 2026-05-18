#!/usr/bin/env bash
# Tier-2 operator action: generate and install DIAG_READ_TOKEN on the VM.
#
# Generates a fresh DIAG_READ_TOKEN (or preserves the existing one) and
# writes it to /home/ubuntu/ict-trading-bot/.env, which ict-web-api.service
# loads as a second EnvironmentFile. Restarts ict-web-api.service so the
# token takes effect immediately.
#
# The token value is printed to stdout so the workflow result (issue comment)
# carries it. The operator must then add the same value to the GitHub repo
# secret DIAG_READ_TOKEN:
#   https://github.com/benbaichmankass/ict-trading-bot/settings/secrets/actions
#
# Priority chain for reading an existing token (highest → lowest):
#   1. /etc/ict-trader/web-api.env  (written by deploy_diag.sh when run as root)
#   2. /home/ubuntu/ict-trading-bot/.env  (written by this script)
#   3. /etc/ict-trading-bot/diag_token   (legacy plain-text fallback)
# If none found, a new 64-hex-char token is generated.

set -euo pipefail

SCRIPT_NAME="init_diag_token"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

ENV_FILE="/home/ubuntu/ict-trading-bot/.env"
SYSTEM_ENV="/etc/ict-trader/web-api.env"
LEGACY_TOKEN_FILE="/etc/ict-trading-bot/diag_token"
UNIT="ict-web-api.service"

# ── Resolve or generate token ──────────────────────────────────────────────

TOKEN=""

if grep -q '^DIAG_READ_TOKEN=' "${SYSTEM_ENV}" 2>/dev/null; then
    TOKEN="$(grep '^DIAG_READ_TOKEN=' "${SYSTEM_ENV}" | head -n1 | cut -d= -f2-)"
    log "Reusing DIAG_READ_TOKEN from ${SYSTEM_ENV}"
elif grep -q '^DIAG_READ_TOKEN=' "${ENV_FILE}" 2>/dev/null; then
    TOKEN="$(grep '^DIAG_READ_TOKEN=' "${ENV_FILE}" | head -n1 | cut -d= -f2-)"
    log "Reusing DIAG_READ_TOKEN from ${ENV_FILE}"
elif [ -r "${LEGACY_TOKEN_FILE}" ]; then
    TOKEN="$(cat "${LEGACY_TOKEN_FILE}")"
    log "Reusing DIAG_READ_TOKEN from legacy ${LEGACY_TOKEN_FILE}"
else
    log "No existing token found — generating new DIAG_READ_TOKEN..."
    TOKEN="$(openssl rand -hex 32)"
    log "Generated new token."
fi

# ── Write to .env (ubuntu-writable; loaded after system env file) ──────────

touch "${ENV_FILE}"
sed -i '/^DIAG_READ_TOKEN=/d' "${ENV_FILE}"
echo "DIAG_READ_TOKEN=${TOKEN}" >> "${ENV_FILE}"
log "Written DIAG_READ_TOKEN to ${ENV_FILE}"

# ── Restart web API ────────────────────────────────────────────────────────

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl unavailable."
    exit 1
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"
"${SYSTEMCTL[@]}" restart "${UNIT}"

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    [ "${post_state}" = "active" ] && break
    sleep 2
done
log "Post-restart state of ${UNIT}: ${post_state}"

# ── Output token so the operator can add it to GitHub secrets ─────────────

echo ""
echo "======================================================================"
echo "DIAG_READ_TOKEN is now active on the VM."
echo ""
echo "Add this value to GitHub repo secrets as DIAG_READ_TOKEN:"
echo "  https://github.com/benbaichmankass/ict-trading-bot/settings/secrets/actions"
echo ""
echo "  DIAG_READ_TOKEN=${TOKEN}"
echo ""
echo "======================================================================"

if [ "${post_state}" != "active" ]; then
    log "ERROR: ${UNIT} did not return to active state — check journalctl."
    exit 1
fi

record_audit "init-diag-token" "ok" \
    "{\"unit\": \"${UNIT}\", \"pre\": \"${pre_state}\", \"post\": \"${post_state}\"}" \
    >/dev/null || true

log "Done."
