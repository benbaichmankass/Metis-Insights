#!/usr/bin/env bash
# Tier-2 system-action: set / update one env var in the VM's .env and
# restart the service that consumes it.
#
# This is the autonomous "Claude configures the VM environment" path:
# Claude owns the env, edits it, and applies it — no operator hand-off.
# Idempotent single-key upsert (preserves every other line + comments
# byte-for-byte); the targeted service is restarted so systemd re-reads
# its EnvironmentFile.
#
# Dispatched by the system-actions workflow (issue body:
#   action: set-env
#   env_key: TELEGRAM_CLAUDE_THREAD_ID
#   env_value: 42                 (omit for secret-backed keys; see below)
#   service: ict-claude-bridge
#   reason: <why>
# ). The workflow threads ENV_KEY / ENV_VALUE / ENV_SERVICE. For keys whose
# value is a secret (e.g. TELEGRAM_CLAUDE_BOT_TOKEN), the workflow supplies
# ENV_VALUE from a GitHub Actions secret of the same name, so the value
# never appears in the (public) issue body or run log.
#
# Values are NEVER logged or recorded in the audit JSON — only the key,
# service, and whether the key was created vs updated.
#
# Exit codes: 0 success, 1 validation / write / restart failure.

set -euo pipefail

SCRIPT_NAME="set_env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

ENV_FILE="${REPO_DIR}/.env"

KEY="${ENV_KEY:-}"
VALUE="${ENV_VALUE:-}"
SERVICE="${ENV_SERVICE:-}"

# Services this action is allowed to restart. Keeps the blast radius
# bounded to the bot units; never the order path beyond the trader unit
# the operator already restarts via restart-bot-service.
ALLOWED_SERVICES="ict-claude-bridge.service ict-telegram-bot.service ict-web-api.service ict-hourly-snapshot.service ict-trader-live.service none"

if [ -z "${KEY// }" ]; then
    log "ERROR: set-env requires 'env_key'."
    record_audit "set-env" "error" '{"reason": "missing env_key"}' >/dev/null || true
    exit 1
fi

# Env-var name charset guard (also defends the sed/grep edit below).
if ! [[ "${KEY}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
    log "ERROR: env_key '${KEY}' invalid (allowed: ^[A-Z][A-Z0-9_]*$)."
    record_audit "set-env" "error" '{"reason": "invalid env_key charset"}' >/dev/null || true
    exit 1
fi

# Value must be single-line (no newlines / control chars that would
# corrupt the KEY=VALUE line). Empty value is allowed (clears a setting).
if printf '%s' "${VALUE}" | grep -q '[[:cntrl:]]'; then
    log "ERROR: env_value contains control characters / newlines."
    record_audit "set-env" "error" "{\"reason\": \"invalid env_value\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

# Normalise + validate the service (default: restart the claude bridge,
# the most common target; 'none' skips the restart for env-only changes).
SERVICE="${SERVICE:-ict-claude-bridge.service}"
case "${SERVICE}" in
    *.service|none) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac
if ! printf '%s' " ${ALLOWED_SERVICES} " | grep -q " ${SERVICE} "; then
    log "ERROR: service '${SERVICE}' not in allowlist: ${ALLOWED_SERVICES}"
    record_audit "set-env" "error" "{\"reason\": \"service not allowlisted\", \"service\": \"${SERVICE}\"}" >/dev/null || true
    exit 1
fi

touch "${ENV_FILE}"

# Idempotent single-key upsert via Python (handles quoting, preserves the
# rest of the file). Returns "created" or "updated" on stdout.
op="$(
KEY="${KEY}" VALUE="${VALUE}" ENV_FILE="${ENV_FILE}" /usr/bin/python3 - <<'PY'
import os, pathlib
key, value, path = os.environ["KEY"], os.environ["VALUE"], pathlib.Path(os.environ["ENV_FILE"])
lines = path.read_text().splitlines() if path.exists() else []
new_line = f"{key}={value}"
found = False
out = []
for ln in lines:
    stripped = ln.lstrip()
    # Match KEY= possibly preceded by 'export ' (ignore commented lines).
    bare = stripped[len("export "):] if stripped.startswith("export ") else stripped
    if bare.split("=", 1)[0].strip() == key and not stripped.startswith("#"):
        out.append(new_line)
        found = True
    else:
        out.append(ln)
if not found:
    out.append(new_line)
path.write_text("\n".join(out) + "\n")
print("updated" if found else "created")
PY
)"
log "env_key ${KEY} ${op} in ${ENV_FILE}."

# Confirm the key reads back (value not echoed).
if ! grep -qE "^(export )?${KEY}=" "${ENV_FILE}"; then
    log "ERROR: post-write read-back for ${KEY} failed."
    record_audit "set-env" "failed" "{\"reason\": \"readback failed\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

if [ "${SERVICE}" = "none" ]; then
    log "service=none — env written, no restart requested."
    record_audit "set-env" "ok" "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"service\": \"none\"}" >/dev/null || true
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "set-env" "failed" "{\"reason\": \"sudo unavailable\", \"key\": \"${KEY}\"}" >/dev/null || true
    exit 1
fi

log "Restarting ${SERVICE} to pick up the new env..."
"${SYSTEMCTL[@]}" restart "${SERVICE}"

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${SERVICE}" 2>/dev/null || echo "unknown")"
    [ "${post_state}" = "active" ] && break
    sleep 2
done
log "Post-restart ${SERVICE} state: ${post_state}"

if [ "${post_state}" = "active" ]; then
    record_audit "set-env" "ok" \
        "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"service\": \"${SERVICE}\", \"unit\": \"active\"}" >/dev/null || true
    exit 0
else
    record_audit "set-env" "failed" \
        "{\"key\": \"${KEY}\", \"op\": \"${op}\", \"service\": \"${SERVICE}\", \"unit\": \"${post_state}\"}" >/dev/null || true
    log "ERROR: ${SERVICE} did not return to 'active' within 30 s."
    exit 1
fi
