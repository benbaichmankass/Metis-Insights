#!/usr/bin/env bash
# Tier-2 operator action: stop the Cloudflare named tunnel and delete
# it from the account. Symmetric companion to
# setup_named_cloudflare_tunnel.sh.
#
# Idempotent — safe to re-run when nothing exists.
#
# Use this when:
#   * Decommissioning the bot.
#   * Rebuilding the tunnel from scratch after a configuration mistake.
#   * Migrating to a different transport (Tailscale Funnel, etc.).
#
# What it does:
#   1. Stop + disable ict-cloudflared-tunnel.service.
#   2. Look up the tunnel by name via CF API.
#   3. Clean up any connections, then DELETE the tunnel.
#   4. Remove /etc/ict-trader/cloudflared/ contents (credentials + config).
#   5. Drop the URL file so consumers don't read a dead tunnel.
#   6. Leave the cloudflared binary in place + the unit file installed
#      so a re-setup is one system-action away.

set -euo pipefail

SCRIPT_NAME="teardown_named_cloudflare_tunnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

TUNNEL_NAME="ict-trader-bot-tunnel"
UNIT_NAME="ict-cloudflared-tunnel.service"
CF_CONFIG_DIR="/etc/ict-trader/cloudflared"
CF_API="https://api.cloudflare.com/client/v4"
URL_FILE="${REPO_DIR}/runtime_logs/cloudflared_tunnel_url.txt"

if pgrep -af 'claude-vm-runner@' >/dev/null 2>&1; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to stop tunnel mid-runner."
    record_audit "teardown-named-cloudflare-tunnel" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# ─── 1. Stop + disable the systemd unit ────────────────────────────
if systemctl list-unit-files | grep -q "^${UNIT_NAME}"; then
    log "Stopping + disabling ${UNIT_NAME}..."
    sudo systemctl stop "${UNIT_NAME}" 2>/dev/null || true
    sudo systemctl disable "${UNIT_NAME}" 2>/dev/null || true
else
    log "${UNIT_NAME} not installed; skipping systemctl stop."
fi

# ─── 2. Look up + delete the tunnel via CF API ─────────────────────
DELETED=false
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
    cf_api() {
        local method="$1" path="$2" data="${3:-}"
        local args=(-sS --fail --max-time 30 -X "${method}"
                    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
                    -H "Content-Type: application/json")
        if [ -n "${data}" ]; then
            args+=(--data "${data}")
        fi
        curl "${args[@]}" "${CF_API}${path}" || true
    }
    LIST="$(cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel?name=${TUNNEL_NAME}&is_deleted=false")"
    TUNNEL_ID="$(printf '%s' "${LIST}" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print((r.get("result") or [{}])[0].get("id","") if r.get("success") else "")' \
        2>/dev/null || true)"
    if [ -n "${TUNNEL_ID}" ]; then
        log "Cleaning + deleting tunnel id=${TUNNEL_ID}..."
        # Cleanup pending connections first; ignored on failure.
        cf_api POST "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/connections" '{"empty": true}' >/dev/null || true
        cf_api DELETE "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}" >/dev/null \
            && DELETED=true \
            || log "WARN: tunnel delete API call failed (may have stale connections)."
    else
        log "No tunnel named ${TUNNEL_NAME} found on this account."
    fi
else
    log "CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID unset; skipping API delete. Tunnel may remain orphaned on the CF side; clean up via the dashboard."
fi

# ─── 3. Remove on-disk credentials + config ────────────────────────
if [ -d "${CF_CONFIG_DIR}" ]; then
    log "Removing ${CF_CONFIG_DIR}..."
    sudo rm -rf "${CF_CONFIG_DIR}"
fi

# ─── 4. Drop URL file ──────────────────────────────────────────────
rm -f "${URL_FILE}"

record_audit "teardown-named-cloudflare-tunnel" "ok" \
    "{\"tunnel_deleted\": ${DELETED}}" >/dev/null || true

echo
echo "=========================================="
echo "  NAMED CLOUDFLARE TUNNEL TORN DOWN"
echo "  Tunnel deleted via API: ${DELETED}"
echo "  Config dir removed:     ${CF_CONFIG_DIR}"
echo "  vercel.json should be updated next so the dashboard"
echo "  doesn't keep hitting a dead tunnel URL."
echo "=========================================="

exit 0
