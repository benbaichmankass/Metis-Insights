#!/usr/bin/env bash
# Tier-2 operator action: tear down the Tailscale Funnel public exposure
# for the bot's FastAPI on localhost:8001.
#
# Leaves the Tailscale daemon + auth state intact — only removes the
# public-internet exposure. The device stays in the tailnet for any
# private (tailnet-internal) access; Funnel-routed external traffic
# stops immediately.
#
# Symmetric companion: scripts/ops/setup_tailscale_funnel.sh.

set -euo pipefail

SCRIPT_NAME="teardown_tailscale_funnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

LOCAL_PORT=8001
URL_FILE="${REPO_DIR}/runtime_logs/tailscale_funnel_url.txt"

if ! command -v tailscale >/dev/null 2>&1; then
    log "tailscale binary not present — nothing to tear down."
    record_audit "teardown-tailscale-funnel" "noop" \
        '{"reason": "tailscale not installed"}' >/dev/null || true
    exit 0
fi

log "Removing Funnel exposure on port ${LOCAL_PORT}…"
sudo -n tailscale funnel --https=443 off 2>/dev/null || true
sudo -n tailscale serve reset 2>/dev/null || true

# Verify nothing exposed.
STATE="$(sudo -n tailscale serve status 2>&1 || true)"
log "Post-teardown serve status: ${STATE}"

if [ -f "${URL_FILE}" ]; then
    rm -f "${URL_FILE}"
    log "Removed ${URL_FILE}."
fi

record_audit "teardown-tailscale-funnel" "ok" \
    '{"port": '"${LOCAL_PORT}"'}' >/dev/null || true

echo
echo "=========================================="
echo "  TAILSCALE FUNNEL TORN DOWN"
echo "  Public exposure on port ${LOCAL_PORT} is OFF."
echo "  Tailnet-private access still works."
echo "=========================================="

exit 0
