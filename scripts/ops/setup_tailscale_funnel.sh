#!/usr/bin/env bash
# Tier-2 operator action: install Tailscale on the VM, authenticate,
# enable Funnel for the bot's FastAPI on localhost:8001, and persist
# the resulting public HTTPS URL so the dashboard's vercel.json can
# point at it.
#
# Why: Tailscale Funnel exposes a *stable* HTTPS URL of the form
# https://<vm-hostname>.<tailnet>.ts.net that survives reboots,
# restarts, and key rotations. (Note: the current Streamlit dashboard
# makes its upstream call server-side and needs no tunnel at all, so
# this funnel path is only relevant to a future browser-direct
# consumer; the retired Cloudflare tunnel stack it replaced has been
# purged from the repo.)
#
# Prerequisites (operator does ONCE before invoking this action, all
# from the browser — no VM SSH required):
#   1. Sign up for Tailscale at https://login.tailscale.com (free).
#   2. Admin console → Settings → DNS → "HTTPS Certificates" → Enable.
#      (Funnel requires HTTPS to be enabled for the tailnet.)
#   3. Admin console → Settings → Keys → "Generate auth key":
#        - Reusable:    no
#        - Ephemeral:   no
#        - Pre-approved: yes (if device approval is on)
#        - Tags:        none required
#        - Expiration:  90 days (max)
#   4. Add the auth key as a GitHub Actions secret:
#        github.com/benbaichmankass/ict-trading-bot → Settings → Secrets and variables
#        → Actions → "New repository secret":
#          Name:   TS_AUTHKEY
#          Value:  tskey-auth-... (paste from step 3)
#      The workflow passes this secret to the VM over the existing
#      SSH channel for the duration of one wrapper invocation; the
#      key is NEVER written to disk on the VM and NEVER committed.
#   5. After the FIRST successful run of this action, the device
#      appears in the tailnet. Then in admin console → Machines →
#      `ict-trader-live` → "Edit Funnel" → enable for this device.
#      (Funnel is opt-in per-machine; this is a one-time click.)
#      Re-run the action after enabling; it's idempotent.
#
# Once prerequisites are in place, this wrapper is idempotent:
#   - if tailscale is already installed, skip the install
#   - if already logged in, skip `tailscale up`
#   - always re-runs `tailscale funnel` (cheap; ensures the port
#     forward is active after a Funnel toggle in the admin panel)
#
# Persistence:
#   - Tailscale daemon is installed as a systemd unit (tailscaled)
#     by the upstream installer; survives reboot.
#   - `tailscale funnel --bg` writes its serve-config to
#     /var/lib/tailscale/tailscaled.state; survives reboot.
#
# Symmetric companion: scripts/ops/teardown_tailscale_funnel.sh.

set -euo pipefail

SCRIPT_NAME="setup_tailscale_funnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

LOCAL_PORT=8001
URL_FILE="${REPO_DIR}/runtime_logs/tailscale_funnel_url.txt"
# TS_AUTHKEY comes in via the SSH env channel from system-actions.yml.
# It is consumed once (for `tailscale up`) and then unset; nothing touches
# disk, nothing logs the value.

# Defense in depth — don't churn the tunnel mid-runner.
if pgrep -af 'claude-vm-runner@' >/dev/null 2>&1; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to reconfigure Tailscale mid-runner."
    record_audit "setup-tailscale-funnel" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# 1. Install tailscale if missing.
if ! command -v tailscale >/dev/null 2>&1; then
    log "tailscale not found — installing via upstream script."
    if ! curl -fsSL https://tailscale.com/install.sh | sudo -n bash; then
        log "ERROR: tailscale install failed."
        record_audit "setup-tailscale-funnel" "error" \
            '{"reason": "install failed"}' >/dev/null || true
        exit 1
    fi
fi

INSTALLED_VERSION="$(tailscale version | head -1 || echo unknown)"
log "tailscale installed: ${INSTALLED_VERSION}"

# 2. Ensure daemon running.
if ! sudo -n systemctl is-active --quiet tailscaled; then
    log "tailscaled not active — starting + enabling."
    sudo -n systemctl enable --now tailscaled
fi

# 3. Authenticate if not already.
if ! tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; then
    log "tailscale not authenticated — reading TS_AUTHKEY from SSH env."
    if [ -z "${TS_AUTHKEY:-}" ]; then
        log "ERROR: TS_AUTHKEY env var unset. The system-actions workflow must"
        log "       pass it through from the GitHub Actions secret of the same name."
        log "       Add the secret at: Settings → Secrets and variables → Actions."
        record_audit "setup-tailscale-funnel" "error" \
            '{"reason": "TS_AUTHKEY unset in SSH env"}' >/dev/null || true
        exit 1
    fi
    sudo -n tailscale up --authkey="${TS_AUTHKEY}" --hostname=ict-trader-live --accept-routes=false --ssh=false
    # Scrub key from this shell immediately after consuming it.
    unset TS_AUTHKEY
fi

# 4. Resolve the device's public DNS name from tailscale status.
TS_DNS="$(tailscale status --json 2>/dev/null | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
    name = s.get("Self", {}).get("DNSName", "").rstrip(".")
    print(name)
except Exception:
    pass
')"

if [ -z "${TS_DNS}" ]; then
    log "ERROR: could not read Self.DNSName from tailscale status. Is the device approved in the admin panel?"
    record_audit "setup-tailscale-funnel" "error" \
        '{"reason": "DNSName empty"}' >/dev/null || true
    exit 1
fi
log "Tailscale device DNS name: ${TS_DNS}"

# 5. Configure Funnel: public HTTPS at <TS_DNS> → http://127.0.0.1:8001.
#    `tailscale funnel --bg <port>` exposes localhost:<port> publicly
#    on the device's HTTPS hostname. Re-running is a no-op when the
#    config is already in place.
log "Enabling Funnel on local port ${LOCAL_PORT}…"
sudo -n tailscale funnel --bg "${LOCAL_PORT}"

# 6. Persist the URL the dashboard should target.
PUBLIC_URL="https://${TS_DNS}"
mkdir -p "$(dirname "${URL_FILE}")"
echo "${PUBLIC_URL}" > "${URL_FILE}"

# 7. Probe through the public URL to confirm end-to-end works.
#    Note: this hits the public internet, so it validates not just
#    the daemon but also DNS / cert / Funnel routing.
PROBE_OUT="$(curl -sS --max-time 15 "${PUBLIC_URL}/api/health" || echo 'curl failed')"
log "Funnel /api/health probe: ${PROBE_OUT}"

record_audit "setup-tailscale-funnel" "ok" \
    "{\"public_url\": \"${PUBLIC_URL}\", \"version\": \"${INSTALLED_VERSION}\", \"probe\": \"${PROBE_OUT}\"}" \
    >/dev/null || true

echo
echo "=========================================="
echo "  TAILSCALE FUNNEL READY"
echo "  URL:  ${PUBLIC_URL}"
echo "  Health probe: ${PROBE_OUT}"
echo
echo "  Next: update ict-trader-dashboard/vercel.json"
echo "    \"destination\": \"${PUBLIC_URL}/api/bot/:path*\""
echo "  Then redeploy the dashboard. URL is stable across reboots."
echo "=========================================="

exit 0
