#!/usr/bin/env bash
# Tier-2 self-heal action: restart the read-only web API systemd unit.
#
# Why this lives outside system-actions.yml's allowlist:
#   The web-api hosts /api/diag/* — the read-only surface the
#   PM-side / web-sandbox session uses for visibility. When that
#   service is down, the diag relay (vm-diag-snapshot.yml) fails
#   with curl exit 7 on every request, which silently blinds Claude
#   to production state. Recovery used to require the operator to
#   SSH in or run /vm_write. This action closes that loop with an
#   issue-driven workflow Claude can drive autonomously.
#
# Blast radius:
#   - ict-web-api.service is the FastAPI process serving the
#     dashboard + diag endpoints. It does NOT execute trades, place
#     orders, or read/write strategy state. Restarting it bounces
#     dashboard polling for ~5 s and otherwise has no effect on the
#     live trader.
#   - This wrapper does NOT touch ict-trader-live.service,
#     accounts.yaml, strategies.yaml, risk caps, or any code path
#     that influences trade decisions. Any attempt to do so should
#     be rejected at code review.
#
# Pre/post checks:
#   - Capture is-active state of ict-web-api.service before restart.
#   - Issue `systemctl restart ict-web-api.service`.
#   - Poll up to 30 s for `is-active` to return "active".
#   - Probe http://127.0.0.1:8001/api/health for a 200 response so
#     "active but crashing on every request" is caught (systemd may
#     report a tight-restart-loop unit as "active" between crashes).
#   - Dump the last 30 journal lines so the operator / Claude can
#     spot crashes in the post-restart log.

set -euo pipefail

SCRIPT_NAME="restart_web_api"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-web-api.service"
HEALTH_URL="http://127.0.0.1:8001/api/health"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required (see deploy_pull_restart.sh)."
    record_audit "restart-web-api" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"
echo "===== pre-restart status ====="
"${SYSTEMCTL[@]}" status "${UNIT}" --no-pager -n 5 || true

log "Restarting ${UNIT}…"
"${SYSTEMCTL[@]}" restart "${UNIT}"

# Allow up to 30 s for systemd to settle.
deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    if [ "${post_state}" = "active" ]; then
        break
    fi
    sleep 2
done
log "Post-restart state of ${UNIT}: ${post_state}"

# is-active=active is necessary but not sufficient — a crashloop can
# flicker into "active" briefly. Probe /api/health for a 200 so we
# only declare success when the FastAPI process is actually serving.
health_status="unknown"
if [ "${post_state}" = "active" ]; then
    health_deadline=$(( $(date +%s) + 15 ))
    while [ "$(date +%s)" -lt "${health_deadline}" ]; do
        if curl -sS --fail --max-time 5 -o /dev/null "${HEALTH_URL}"; then
            health_status="ok"
            break
        fi
        health_status="unreachable"
        sleep 2
    done
fi
log "Post-restart health probe of ${HEALTH_URL}: ${health_status}"

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

if [ "${post_state}" = "active" ] && [ "${health_status}" = "ok" ]; then
    record_audit "restart-web-api" "ok" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"health\": \"${health_status}\"}" >/dev/null || true
    log "Restart succeeded — unit active and /api/health returning 200."
    exit 0
else
    record_audit "restart-web-api" "failed" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"health\": \"${health_status}\"}" >/dev/null || true
    log "ERROR: ${UNIT} did not return to a serving state (post=${post_state}, health=${health_status})."
    exit 1
fi
