#!/usr/bin/env bash
# Tier-2 operator action: disable the closed → exchange-flat invariant
# on the live trader (rollback path for the soak window).
#
# Removes the `CLOSED_FLAT_INVARIANT_ENABLED=true` line from
# /home/ubuntu/ict-trading-bot/.env (or sets it to false if you'd
# rather keep the line for visibility — see ENV_VALUE_ON_DISABLE),
# then restarts ict-trader-live.service so the running process drops
# back to the default-off path inside _closed_flat_wiring.
#
# Idempotent. Safe to re-run.
#
# Symmetric companion: scripts/ops/enable_closed_flat_invariant.sh.
#
# Use this when:
#   - the soak surfaced a bug in closed_flat_invariant.check itself,
#   - or the alert volume is overwhelming and the operator wants the
#     orphan reconciler back as the only safety net while the close
#     path is investigated.

set -euo pipefail

SCRIPT_NAME="disable_closed_flat_invariant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="CLOSED_FLAT_INVARIANT_ENABLED"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "disable-closed-flat-invariant" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "disable-closed-flat-invariant" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "WARNING: ${ENV_FILE} does not exist. Nothing to disable; treating as already-off."
    record_audit "disable-closed-flat-invariant" "noop" \
        '{"reason": "env file missing"}' >/dev/null || true
    # Still issue the restart for completeness so the running process
    # picks up whatever env state is current.
    "${SYSTEMCTL[@]}" restart "${UNIT}" || true
    exit 0
fi

pre_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Pre-toggle value of ${ENV_KEY}: '${pre_value:-<unset>}'"

# Strip the env line + the optional comment header that
# enable_closed_flat_invariant.sh writes alongside it. Keep all
# other lines untouched.
tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "${tmp_env}"' EXIT

# Drop:
#   - any line starting with CLOSED_FLAT_INVARIANT_ENABLED=
#   - the two header comment lines if present (so the file doesn't
#     accumulate dead headers across enable/disable cycles)
sed -E \
    -e "/^${ENV_KEY}=/d" \
    -e "/^# Closed → exchange-flat invariant alert-only soak \(Phase-1\)\.\$/d" \
    -e "/^# Set by scripts\/ops\/enable_closed_flat_invariant\.sh\.\$/d" \
    "${ENV_FILE}" > "${tmp_env}"

chown --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
chmod --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
mv "${tmp_env}" "${ENV_FILE}"
trap - EXIT

post_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Post-toggle value of ${ENV_KEY}: '${post_value:-<unset>}'"

if [ -n "${post_value}" ]; then
    log "ERROR: post-edit verification failed. ${ENV_KEY} should be unset; still '${post_value}'."
    record_audit "disable-closed-flat-invariant" "error" \
        "{\"reason\": \"verify mismatch\", \"actual\": \"${post_value}\"}" >/dev/null || true
    exit 1
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"

log "Restarting ${UNIT}…"
"${SYSTEMCTL[@]}" restart "${UNIT}"

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

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

if [ "${post_state}" = "active" ]; then
    record_audit "disable-closed-flat-invariant" "ok" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"<unset>\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "Closed-flat invariant DISABLED. The orphan reconciler is the only safety net."
    exit 0
else
    record_audit "disable-closed-flat-invariant" "failed" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"<unset>\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s after env toggle."
    exit 1
fi
