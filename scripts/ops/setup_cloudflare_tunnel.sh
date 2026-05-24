#!/usr/bin/env bash
# Tier-2 operator action: install + run a Cloudflare quick tunnel
# pointing http://localhost:8001 → public https://<random>.trycloudflare.com.
#
# Why: the dashboard's Vercel rewrite from `/api/bot/*` to
# http://158.178.210.252:8001 stopped resolving. The bot's API is
# reachable directly from a browser (verified 2026-05-10) but Vercel's
# edge isn't proxying — likely Vercel tightened their HTTP-upstream
# policy on Hobby plan. Putting the bot behind a Cloudflare tunnel
# gives Vercel a TLS upstream the rewrite can hit.
#
# Quick-tunnel mode → no Cloudflare account required. The hostname is
# ephemeral: every restart of the cloudflared process picks a new
# `<random>.trycloudflare.com` URL. The wrapper:
#
#   1. Installs the cloudflared static binary into ~/.local/bin (no
#      sudo / dpkg required — the existing system-actions sudoers
#      grants NOPASSWD systemctl only, not package management).
#   2. Stops any prior cloudflared instance owned by this wrapper
#      (PID-file tracked).
#   3. Launches cloudflared in the background under nohup, logging to
#      ~/cloudflared-bot.log.
#   4. Waits up to 30s for the trycloudflare.com URL to appear in the
#      log. Errors loudly on a mid-startup crash.
#   5. Persists the URL to runtime_logs/cloudflared_tunnel_url.txt so
#      the diag-relay surface can return it.
#   6. Installs an `@reboot` crontab entry so the tunnel comes back
#      after a VM reboot. Idempotent — strips the prior entry first.
#   7. Probes ${URL}/api/health to confirm the tunnel reaches the bot.
#   8. Echoes the URL on stdout (workflow log + issue comment) so the
#      next session can lift it into vercel.json.
#
# Persistence caveat: nohup is good enough for "live across logout"
# but the URL changes on every cloudflared restart. A follow-up sprint
# will swap to a named tunnel via Cloudflare API token (stable URL,
# survives all restarts) once the operator generates a token.
#
# Symmetric companion: scripts/ops/teardown_cloudflare_tunnel.sh.

set -euo pipefail

SCRIPT_NAME="setup_cloudflare_tunnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

CLOUDFLARED="${HOME}/.local/bin/cloudflared"
LOG_FILE="${HOME}/cloudflared-bot.log"
PID_FILE="${HOME}/cloudflared-bot.pid"
URL_FILE="${REPO_DIR}/runtime_logs/cloudflared_tunnel_url.txt"
LOCAL_PORT=8001
DOWNLOAD_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

# Defense in depth — don't churn cloudflared mid-runner.
if pgrep -af 'claude-vm-runner@' >/dev/null 2>&1; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart cloudflared mid-runner."
    record_audit "setup-cloudflare-tunnel" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# 1. Install cloudflared if missing.
if [ ! -x "${CLOUDFLARED}" ]; then
    log "cloudflared not found at ${CLOUDFLARED} — downloading static binary."
    mkdir -p "${HOME}/.local/bin"
    if ! curl -sSL --fail -o "${CLOUDFLARED}" "${DOWNLOAD_URL}"; then
        log "ERROR: cloudflared download failed (${DOWNLOAD_URL})."
        record_audit "setup-cloudflare-tunnel" "error" \
            '{"reason": "binary download failed"}' >/dev/null || true
        exit 1
    fi
    chmod +x "${CLOUDFLARED}"
fi

INSTALLED_VERSION="$("${CLOUDFLARED}" --version 2>/dev/null | head -1 || echo "unknown")"
log "cloudflared installed: ${INSTALLED_VERSION}"

# 2. Stop any prior instance owned by this wrapper.
if [ -f "${PID_FILE}" ]; then
    OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
        log "Stopping prior cloudflared (pid ${OLD_PID})."
        kill "${OLD_PID}" 2>/dev/null || true
        sleep 2
        kill -9 "${OLD_PID}" 2>/dev/null || true
    fi
    rm -f "${PID_FILE}"
fi
# Belt + braces — kill any rogue cloudflared bound to localhost:8001
# that the PID file didn't catch (e.g. from a previous wrapper crash).
pkill -f "cloudflared tunnel --url http://localhost:${LOCAL_PORT}" 2>/dev/null || true
sleep 1

# 3. Truncate log so the URL parser only sees the new run.
: > "${LOG_FILE}"

# 4. Launch in background.
log "Starting cloudflared quick tunnel for http://localhost:${LOCAL_PORT}…"
nohup "${CLOUDFLARED}" tunnel --url "http://localhost:${LOCAL_PORT}" \
    --logfile "${LOG_FILE}" \
    >> "${LOG_FILE}" 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > "${PID_FILE}"
disown "${NEW_PID}" 2>/dev/null || true
log "cloudflared pid: ${NEW_PID}"

# 5. Wait for the tunnel URL to appear in the log.
DEADLINE=$(( $(date +%s) + 30 ))
TUNNEL_URL=""
while [ "$(date +%s)" -lt "${DEADLINE}" ]; do
    if [ -s "${LOG_FILE}" ]; then
        TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "${LOG_FILE}" | head -1 || true)"
        if [ -n "${TUNNEL_URL}" ]; then
            break
        fi
    fi
    if ! kill -0 "${NEW_PID}" 2>/dev/null; then
        log "ERROR: cloudflared died during startup. Last 20 log lines:"
        tail -20 "${LOG_FILE}" >&2 || true
        record_audit "setup-cloudflare-tunnel" "error" \
            '{"reason": "cloudflared exited during startup"}' >/dev/null || true
        rm -f "${PID_FILE}"
        exit 1
    fi
    sleep 1
done

if [ -z "${TUNNEL_URL}" ]; then
    log "ERROR: timed out (30s) waiting for trycloudflare URL in log. Last 20 lines:"
    tail -20 "${LOG_FILE}" >&2 || true
    record_audit "setup-cloudflare-tunnel" "error" \
        '{"reason": "url not in log within 30s"}' >/dev/null || true
    exit 1
fi

log "Tunnel URL: ${TUNNEL_URL}"

# 6. Persist the URL to runtime_logs so the diag-relay surface can read it.
mkdir -p "$(dirname "${URL_FILE}")"
echo "${TUNNEL_URL}" > "${URL_FILE}"

# 7. Install an @reboot crontab entry so the tunnel survives a VM reboot.
#    Idempotent — strip the prior entry first, then append the current one.
#
#    The naive form `( crontab -l | grep -v ... ; echo $LINE ) | crontab -`
#    blows up on a fresh user account: `crontab -l` returns 1 when no
#    crontab exists, `grep -v` returns 1 on empty input, and `set -o
#    pipefail` (inherited by the subshell) propagates the failure into
#    `set -e`, killing the script BEFORE the trailing `echo` runs.
#    Issue #721 (this wrapper's first run) hit exactly this — the
#    tunnel came up, the URL was logged, then the script exited 1
#    here without installing the crontab entry.
#
#    Fix: capture each stage with explicit `|| true` so pipefail
#    semantics don't propagate, then assemble the final crontab body
#    in a single printf piped to `crontab -`.
CRON_LINE="@reboot ${CLOUDFLARED} tunnel --url http://localhost:${LOCAL_PORT} --logfile ${LOG_FILE} >> ${LOG_FILE} 2>&1 &"
EXISTING_CRONTAB="$(crontab -l 2>/dev/null || true)"
FILTERED_CRONTAB="$(printf '%s\n' "${EXISTING_CRONTAB}" | grep -v 'cloudflared tunnel --url http://localhost:8001' || true)"
printf '%s\n%s\n' "${FILTERED_CRONTAB}" "${CRON_LINE}" | crontab -
log "Installed @reboot crontab entry."

# 8. Quick health probe via the tunnel itself (proves end-to-end works).
PROBE_OUT="$(curl -sS --max-time 10 "${TUNNEL_URL}/api/health" || echo 'curl failed')"
log "Tunnel /api/health probe: ${PROBE_OUT}"

record_audit "setup-cloudflare-tunnel" "ok" \
    "{\"tunnel_url\": \"${TUNNEL_URL}\", \"pid\": ${NEW_PID}, \"version\": \"${INSTALLED_VERSION}\", \"probe\": \"${PROBE_OUT}\"}" \
    >/dev/null || true

# Echo URL to stdout — visible in workflow log + issue comment.
echo
echo "=========================================="
echo "  CLOUDFLARE TUNNEL READY"
echo "  URL:  ${TUNNEL_URL}"
echo "  Health probe: ${PROBE_OUT}"
echo
echo "  Next: update ict-trader-dashboard/vercel.json"
echo "    \"destination\": \"${TUNNEL_URL}/api/bot/:path*\""
echo "  Then redeploy the dashboard."
echo "=========================================="

exit 0
