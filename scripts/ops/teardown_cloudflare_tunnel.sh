#!/usr/bin/env bash
# Tier-2 operator action: stop the Cloudflare tunnel previously started
# by setup_cloudflare_tunnel.sh and remove the @reboot crontab entry.
#
# Idempotent — safe to re-run when nothing is currently running. Leaves
# the cloudflared binary on disk so the operator can re-dispatch
# setup-cloudflare-tunnel without re-downloading.
#
# Use this when:
#   * The dashboard is being moved off the tunnel onto a stable named
#     tunnel / direct HTTPS, and the quick tunnel is no longer needed.
#   * The tunnel is misbehaving and you want a clean restart (teardown
#     then setup-).
#   * The bot is being relocated and the tunnel pointing at port 8001
#     is no longer accurate.

set -euo pipefail

SCRIPT_NAME="teardown_cloudflare_tunnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

PID_FILE="${HOME}/cloudflared-bot.pid"
URL_FILE="${REPO_DIR}/runtime_logs/cloudflared_tunnel_url.txt"
LOCAL_PORT=8001

if pgrep -af 'claude-vm-runner@' >/dev/null 2>&1; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to stop cloudflared mid-runner."
    record_audit "teardown-cloudflare-tunnel" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# 1. Stop the tracked instance.
KILLED=false
if [ -f "${PID_FILE}" ]; then
    OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
        log "Stopping cloudflared (pid ${OLD_PID})."
        kill "${OLD_PID}" 2>/dev/null || true
        sleep 2
        kill -9 "${OLD_PID}" 2>/dev/null || true
        KILLED=true
    fi
    rm -f "${PID_FILE}"
fi

# 2. Belt + braces — kill any rogue cloudflared bound to localhost:8001
#    that wasn't tracked by the PID file.
if pkill -f "cloudflared tunnel --url http://localhost:${LOCAL_PORT}" 2>/dev/null; then
    KILLED=true
fi

# 3. Strip the @reboot crontab entry. Always-run; safe if the line
#    isn't there.
( crontab -l 2>/dev/null | grep -v 'cloudflared tunnel --url http://localhost:8001' ) | crontab - || true
log "Stripped @reboot crontab entry (if present)."

# 4. Drop the stale URL file so consumers don't read a tunnel that no
#    longer exists.
rm -f "${URL_FILE}"

if [ "${KILLED}" = true ]; then
    log "cloudflared tunnel torn down."
    record_audit "teardown-cloudflare-tunnel" "ok" '{"killed": true}' >/dev/null || true
else
    log "No cloudflared tunnel was running. Crontab + URL file cleared."
    record_audit "teardown-cloudflare-tunnel" "noop" '{"killed": false}' >/dev/null || true
fi

echo
echo "=========================================="
echo "  CLOUDFLARE TUNNEL DOWN"
echo "  Killed running process: ${KILLED}"
echo "  vercel.json should be updated next so the dashboard"
echo "  doesn't keep hitting a dead tunnel URL."
echo "=========================================="

exit 0
