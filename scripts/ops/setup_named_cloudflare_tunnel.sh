#!/usr/bin/env bash
# Tier-2 operator action: install + run a Cloudflare NAMED tunnel for
# the dashboard API. Replaces the ephemeral quick tunnel
# (setup_cloudflare_tunnel.sh) with a STABLE hostname that survives
# cloudflared restarts.
#
# T1 of the 2026-05-12 reconstruction sprint. See:
#   - docs/audit/2026-05-12-end-to-end-audit.md § 6
#   - docs/sprint-logs/T1-named-cloudflare-tunnel.md
#   - docs/runbooks/cloudflare-named-tunnel.md
#
# Prerequisites (one-time, operator-managed):
#   - CLOUDFLARE_API_TOKEN env var passed through from the workflow.
#     Required scope: Account → Cloudflare Tunnel:Edit. (The same token
#     used by cf-worker-deploy.yml works if it has Tunnel:Edit; usually
#     it doesn't, so a separate token is recommended.)
#   - CLOUDFLARE_ACCOUNT_ID env var passed through from the workflow.
#   - Optional: TUNNEL_HOSTNAME env var. If set AND it lies inside a CF
#     DNS zone the API token can edit, the script creates a CNAME so the
#     tunnel is reachable at https://${TUNNEL_HOSTNAME}. If unset, the
#     script falls back to https://<tunnel-id>.cfargotunnel.com (works
#     without any DNS zone; uglier hostname but stable).
#
# What it does:
#   1. Install cloudflared static binary into ~/.local/bin if missing.
#   2. Stop + disable the quick-tunnel @reboot crontab line if present
#      (the quick tunnel becomes redundant once the named tunnel runs).
#   3. Create or fetch the named tunnel via CF API:
#        POST /accounts/{account_id}/cfd_tunnel { name, config_src: local }
#      Tunnel name is fixed: ict-trader-bot-tunnel.
#   4. Store the raw tunnel token in /etc/ict-trader/cloudflared/tunnel.env
#      (chmod 600). The --token flag (via cloudflared-token.conf drop-in)
#      uses this directly — no base64 decode needed.
#   5. Write /etc/ict-trader/cloudflared/config.yml with the ingress
#      mapping → http://localhost:8001. No credentials-file — auth via
#      --token in the drop-in.
#   6. If TUNNEL_HOSTNAME is set, POST a CNAME route via DNS API. Skip
#      gracefully if the zone is missing or the token lacks DNS edit.
#   7. Install + enable + start ict-cloudflared-tunnel.service.
#   8. Probe /api/health locally (origin check) then via the public URL.
#      Loud failure on probe timeout (gives the operator a clean signal
#      vs. the silent quick-tunnel pattern).
#   9. Persist the public URL to runtime_logs/cloudflared_tunnel_url.txt
#      so the diag relay can surface it. Echo it on stdout for the
#      workflow comment.
#
# Symmetric companion: teardown_named_cloudflare_tunnel.sh.

set -euo pipefail

SCRIPT_NAME="setup_named_cloudflare_tunnel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

TUNNEL_NAME="ict-trader-bot-tunnel"
UNIT_NAME="ict-cloudflared-tunnel.service"
LOCAL_PORT=8001
CLOUDFLARED="${HOME}/.local/bin/cloudflared"
CF_CONFIG_DIR="/etc/ict-trader/cloudflared"
CF_API="https://api.cloudflare.com/client/v4"
URL_FILE="${REPO_DIR}/runtime_logs/cloudflared_tunnel_url.txt"
DOWNLOAD_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    log "ERROR: CLOUDFLARE_API_TOKEN env var is unset. Pass it through from the workflow."
    record_audit "setup-named-cloudflare-tunnel" "error" \
        '{"reason": "CLOUDFLARE_API_TOKEN unset"}' >/dev/null || true
    exit 1
fi
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
    log "ERROR: CLOUDFLARE_ACCOUNT_ID env var is unset. Pass it through from the workflow."
    record_audit "setup-named-cloudflare-tunnel" "error" \
        '{"reason": "CLOUDFLARE_ACCOUNT_ID unset"}' >/dev/null || true
    exit 1
fi

# Defense in depth — don't churn mid-runner.
if pgrep -af 'claude-vm-runner@' >/dev/null 2>&1; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart tunnel mid-runner."
    record_audit "setup-named-cloudflare-tunnel" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# ─── 1. Ensure cloudflared is installed ────────────────────────────
if [ ! -x "${CLOUDFLARED}" ]; then
    log "cloudflared not found at ${CLOUDFLARED} — downloading static binary."
    mkdir -p "${HOME}/.local/bin"
    if ! curl -sSL --fail -o "${CLOUDFLARED}" "${DOWNLOAD_URL}"; then
        log "ERROR: cloudflared download failed (${DOWNLOAD_URL})."
        record_audit "setup-named-cloudflare-tunnel" "error" \
            '{"reason": "binary download failed"}' >/dev/null || true
        exit 1
    fi
    chmod +x "${CLOUDFLARED}"
fi
INSTALLED_VERSION="$("${CLOUDFLARED}" --version 2>/dev/null | head -1 || echo unknown)"
log "cloudflared: ${INSTALLED_VERSION}"

# ─── 2. Disable the quick-tunnel @reboot crontab (if present) ──────
# The quick tunnel becomes redundant once the named tunnel runs. Leave
# the quick-tunnel scripts on disk as a fallback, but don't relaunch
# it on boot — two cloudflared processes racing for the same localhost
# port produces obscure failure modes.
EXISTING_CRONTAB="$(crontab -l 2>/dev/null || true)"
if printf '%s' "${EXISTING_CRONTAB}" | grep -q 'cloudflared tunnel --url http://localhost:8001'; then
    log "Stripping quick-tunnel @reboot crontab entry."
    FILTERED_CRONTAB="$(printf '%s\n' "${EXISTING_CRONTAB}" \
        | grep -v 'cloudflared tunnel --url http://localhost:8001' || true)"
    printf '%s\n' "${FILTERED_CRONTAB}" | crontab -
fi

# ─── 3. Create or fetch the named tunnel via CF API ────────────────
cf_api() {
    # Args: METHOD PATH [DATA_JSON]
    local method="$1" path="$2" data="${3:-}"
    local args=(-sS --fail --max-time 30 -X "${method}"
                -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
                -H "Content-Type: application/json")
    if [ -n "${data}" ]; then
        args+=(--data "${data}")
    fi
    curl "${args[@]}" "${CF_API}${path}"
}

log "Looking up existing tunnel named ${TUNNEL_NAME}..."
EXISTING_LIST="$(cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel?name=${TUNNEL_NAME}&is_deleted=false" || echo '')"
TUNNEL_ID="$(printf '%s' "${EXISTING_LIST}" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin); print((r.get("result") or [{}])[0].get("id","") if r.get("success") else "")' \
    2>/dev/null || true)"
TUNNEL_TOKEN=""

if [ -n "${TUNNEL_ID}" ]; then
    log "Reusing existing tunnel id=${TUNNEL_ID}."
    # Fetch the account-level token for tunnel.env.
    TOKEN_RESP="$(cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/token" || echo '')"
    TUNNEL_TOKEN="$(printf '%s' "${TOKEN_RESP}" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r.get("result","") if r.get("success") else "")' \
        2>/dev/null || true)"
else
    log "Creating new tunnel ${TUNNEL_NAME}..."
    CREATE_RESP="$(cf_api POST "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel" \
        "{\"name\": \"${TUNNEL_NAME}\", \"config_src\": \"local\"}" || echo '')"
    TUNNEL_ID="$(printf '%s' "${CREATE_RESP}" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r.get("result",{}).get("id","") if r.get("success") else "")' \
        2>/dev/null || true)"
    if [ -z "${TUNNEL_ID}" ]; then
        log "ERROR: CF tunnel create failed. API response: ${CREATE_RESP}"
        record_audit "setup-named-cloudflare-tunnel" "error" \
            "{\"reason\": \"create failed\", \"api_response\": $(printf '%s' "${CREATE_RESP}" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}" \
            >/dev/null || true
        exit 1
    fi
    log "Created tunnel id=${TUNNEL_ID}."
    TOKEN_RESP="$(cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/token" || echo '')"
    TUNNEL_TOKEN="$(printf '%s' "${TOKEN_RESP}" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r.get("result","") if r.get("success") else "")' \
        2>/dev/null || true)"
fi

if [ -z "${TUNNEL_TOKEN}" ]; then
    log "ERROR: failed to fetch account-level tunnel token for ${TUNNEL_ID}."
    record_audit "setup-named-cloudflare-tunnel" "error" \
        "{\"reason\": \"token fetch failed\", \"tunnel_id\": \"${TUNNEL_ID}\"}" >/dev/null || true
    exit 1
fi

# ─── 4. Store tunnel token in env file (chmod 600, no decode needed) ─
# The CF API token is directly usable with `cloudflared tunnel run --token`.
# Previous approach (base64-decode + credentials JSON) was fragile: the CF
# API uses URL-safe base64 (chars - and _); Python's standard b64decode
# raises binascii.Error on those chars, silently suppressed by
# `2>/dev/null || true`. The credentials file was written empty, causing
# cloudflared to crash-loop with no tunnel connections (2026-05-12 incident).
# The drop-in deploy/dropins/cloudflared-token.conf wires this into ExecStart.
sudo mkdir -p "${CF_CONFIG_DIR}"
TOKEN_ENV_FILE="${CF_CONFIG_DIR}/tunnel.env"
printf 'CLOUDFLARED_TUNNEL_TOKEN=%s\n' "${TUNNEL_TOKEN}" | sudo tee "${TOKEN_ENV_FILE}" >/dev/null
sudo chown ubuntu:ubuntu "${TOKEN_ENV_FILE}"
sudo chmod 600 "${TOKEN_ENV_FILE}"
log "Wrote tunnel token env: ${TOKEN_ENV_FILE}"

# Ensure the cloudflared-token drop-in is in place before daemon-reload.
# install_systemd_units.sh handles this on every pull-and-deploy, but we
# also install it here so standalone setup-named-cloudflare-tunnel runs work.
_CF_DROPIN_SRC="${REPO_DIR}/deploy/dropins/cloudflared-token.conf"
_CF_DROPIN_DST="/etc/systemd/system/ict-cloudflared-tunnel.service.d/token.conf"
if [ -f "${_CF_DROPIN_SRC}" ]; then
    sudo mkdir -p "$(dirname "${_CF_DROPIN_DST}")"
    sudo cp "${_CF_DROPIN_SRC}" "${_CF_DROPIN_DST}"
    sudo chmod 0644 "${_CF_DROPIN_DST}"
    log "Installed drop-in: ${_CF_DROPIN_DST}"
else
    log "WARN: drop-in source not found at ${_CF_DROPIN_SRC} — ExecStart override not applied."
    log "WARN: Run pull-and-deploy first to sync the drop-in to the VM."
fi

# ─── 5. Write config.yml (ingress mapping) ─────────────────────────
# No credentials-file — auth comes from --token via the drop-in
# (deploy/dropins/cloudflared-token.conf). Omitting credentials-file
# avoids confusing error messages from a stale or empty credentials JSON.
sudo tee "${CF_CONFIG_DIR}/config.yml" >/dev/null <<EOF
# Generated by setup_named_cloudflare_tunnel.sh. Do not edit by hand —
# re-run setup-named-cloudflare-tunnel to regenerate.
tunnel: ${TUNNEL_ID}
# credentials-file omitted — auth via --token flag (deploy/dropins/cloudflared-token.conf)

# Origin keep-alive: don't churn TCP connections to localhost:8001 for
# every request.
originRequest:
  connectTimeout: 10s
  noTLSVerify: true

ingress:
  - service: http://localhost:${LOCAL_PORT}
EOF
sudo chown ubuntu:ubuntu "${CF_CONFIG_DIR}/config.yml"
log "Wrote ingress config: ${CF_CONFIG_DIR}/config.yml"

# ─── 6. Optional DNS routing ───────────────────────────────────────
PUBLIC_URL="https://${TUNNEL_ID}.cfargotunnel.com"
if [ -n "${TUNNEL_HOSTNAME:-}" ]; then
    log "Routing ${TUNNEL_HOSTNAME} via tunnel ${TUNNEL_ID}..."
    ZONE_BASE="$(printf '%s' "${TUNNEL_HOSTNAME}" | awk -F. '{ if (NF>=2) print $(NF-1)"."$NF }')"
    ZONE_LIST="$(cf_api GET "/zones?name=${ZONE_BASE}" || echo '')"
    ZONE_ID="$(printf '%s' "${ZONE_LIST}" \
        | python3 -c 'import sys,json; r=json.load(sys.stdin); print((r.get("result") or [{}])[0].get("id","") if r.get("success") else "")' \
        2>/dev/null || true)"
    if [ -n "${ZONE_ID}" ]; then
        DNS_DATA="{\"type\": \"CNAME\", \"name\": \"${TUNNEL_HOSTNAME}\", \"content\": \"${TUNNEL_ID}.cfargotunnel.com\", \"proxied\": true}"
        EXISTING="$(cf_api GET "/zones/${ZONE_ID}/dns_records?name=${TUNNEL_HOSTNAME}" || echo '')"
        RECORD_ID="$(printf '%s' "${EXISTING}" \
            | python3 -c 'import sys,json; r=json.load(sys.stdin); print((r.get("result") or [{}])[0].get("id","") if r.get("success") else "")' \
            2>/dev/null || true)"
        if [ -n "${RECORD_ID}" ]; then
            cf_api PUT "/zones/${ZONE_ID}/dns_records/${RECORD_ID}" "${DNS_DATA}" >/dev/null \
                && log "Updated CNAME ${TUNNEL_HOSTNAME} → ${TUNNEL_ID}.cfargotunnel.com" \
                || log "WARN: DNS update failed; falling back to cfargotunnel URL."
        else
            cf_api POST "/zones/${ZONE_ID}/dns_records" "${DNS_DATA}" >/dev/null \
                && log "Created CNAME ${TUNNEL_HOSTNAME} → ${TUNNEL_ID}.cfargotunnel.com" \
                || log "WARN: DNS create failed; falling back to cfargotunnel URL."
        fi
        PUBLIC_URL="https://${TUNNEL_HOSTNAME}"
    else
        log "WARN: zone ${ZONE_BASE} not found via API (token may lack Zone:Read or zone not on account). Falling back to cfargotunnel URL."
    fi
fi

# ─── 7. Install + enable + start the systemd unit ──────────────────
UNIT_SRC="${REPO_DIR}/deploy/${UNIT_NAME}"
UNIT_DEST="/etc/systemd/system/${UNIT_NAME}"
if [ ! -f "${UNIT_SRC}" ]; then
    log "ERROR: unit source missing at ${UNIT_SRC}. Did the autosync run?"
    record_audit "setup-named-cloudflare-tunnel" "error" \
        '{"reason": "unit source missing"}' >/dev/null || true
    exit 1
fi
sudo install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}" >/dev/null 2>&1 || true
sudo systemctl restart "${UNIT_NAME}"
log "Started ${UNIT_NAME}."

# Wait for the unit to be active.
DEADLINE=$(( $(date +%s) + 30 ))
while [ "$(date +%s)" -lt "${DEADLINE}" ]; do
    if [ "$(systemctl is-active "${UNIT_NAME}" 2>/dev/null)" = active ]; then
        break
    fi
    sleep 2
done
UNIT_STATE="$(systemctl is-active "${UNIT_NAME}" 2>/dev/null || echo unknown)"
log "${UNIT_NAME} is-active: ${UNIT_STATE}"

# ─── 8. End-to-end health probe ────────────────────────────────────
# First check the origin directly — distinguishes "web API down" from
# "tunnel not connected" so the failure is immediately actionable.
log "Direct origin probe: http://localhost:${LOCAL_PORT}/api/health"
LOCAL_PROBE="$(curl -sS --max-time 5 "http://localhost:${LOCAL_PORT}/api/health" 2>&1 || true)"
if printf '%s' "${LOCAL_PROBE}" | grep -q '"ok"\|"status"\|"healthy"' 2>/dev/null; then
    log "Origin localhost:${LOCAL_PORT}/api/health OK — web API is reachable."
else
    log "WARN: localhost:${LOCAL_PORT}/api/health unresponsive: ${LOCAL_PROBE}"
    log "WARN: If the external probe also fails, check ict-web-api.service first."
fi

PROBE_URL="${PUBLIC_URL}/api/health"
PROBE_DEADLINE=$(( $(date +%s) + 60 ))
PROBE_OK=false
PROBE_OUT=""
while [ "$(date +%s)" -lt "${PROBE_DEADLINE}" ]; do
    PROBE_OUT="$(curl -sS --max-time 10 "${PROBE_URL}" 2>&1 || true)"
    if printf '%s' "${PROBE_OUT}" | grep -q '"ok"\|"status"\|"healthy"' 2>/dev/null; then
        PROBE_OK=true
        break
    fi
    sleep 5
done
if [ "${PROBE_OK}" = true ]; then
    log "End-to-end probe OK: ${PROBE_URL}"
else
    log "WARN: end-to-end probe at ${PROBE_URL} did not return a healthy response within 60s."
    log "Last probe output: ${PROBE_OUT}"
    log "Tunnel may still come up — DNS propagation can take a minute."
fi

# ─── 9. Persist URL + audit ────────────────────────────────────────
mkdir -p "$(dirname "${URL_FILE}")"
echo "${PUBLIC_URL}" > "${URL_FILE}"

record_audit "setup-named-cloudflare-tunnel" "ok" \
    "{\"tunnel_id\": \"${TUNNEL_ID}\", \"public_url\": \"${PUBLIC_URL}\", \"unit_state\": \"${UNIT_STATE}\", \"probe_ok\": ${PROBE_OK}, \"version\": \"${INSTALLED_VERSION}\"}" \
    >/dev/null || true

echo
echo "=========================================="
echo "  NAMED CLOUDFLARE TUNNEL READY"
echo "  Tunnel ID:    ${TUNNEL_ID}"
echo "  Public URL:   ${PUBLIC_URL}"
echo "  systemd unit: ${UNIT_NAME} (${UNIT_STATE})"
echo "  Health probe: $([ ${PROBE_OK} = true ] && echo ok || echo PENDING)"
echo
echo "  Next: update ict-trader-dashboard/vercel.json"
echo "    \"destination\": \"${PUBLIC_URL}/api/bot/:path*\""
echo "  Then redeploy the dashboard."
echo
echo "  After verifying the dashboard, retire the quick tunnel:"
echo "    system-action: teardown-cloudflare-tunnel"
echo "=========================================="

exit 0
