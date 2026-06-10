#!/usr/bin/env bash
# Tier-2 operator action: purge the retired Cloudflare tunnel from the live VM.
#
# The Cloudflare tunnel integration was retired in the React→Streamlit
# dashboard pivot (ict-trader-dashboard#32) and removed from the repo in the
# full-system-audit cleanup (#3233). But the repo cleanup only deletes the
# unit FILE from source control — install_systemd_units.sh is INSTALL-only and
# never removes a unit already installed on the VM. So an `ict-cloudflared-
# tunnel.service` that was once installed + enabled keeps running. With the
# operator now disconnecting the Cloudflare account (2026-06-10), that orphaned
# daemon retries a dead tunnel forever — harmless to trading (nothing routes
# through it) but pointless churn on the CPU-constrained 2-core live box.
#
# This action stops + disables + removes the orphaned unit (+ its token
# drop-in) and reloads systemd. Fully IDEMPOTENT: if the unit was never present
# (already clean), every step is a no-op and the script still exits 0 with a
# "nothing to purge" report — so it's safe to run blind.
#
# What this script does NOT touch:
#   - ict-trader-live.service / ict-web-api.service (the live stack)
#   - Strategy params / accounts / risk caps
#   - Any unit other than ict-cloudflared-tunnel.service
#
# CLAUDE.md § Important Notes documents this exact remediation
# (`systemctl disable --now ict-cloudflared-tunnel.service`); this wraps it
# with the file removal + daemon-reload so the corpse is fully gone.

set -euo pipefail

SCRIPT_NAME="purge_cloudflared"
UNIT="ict-cloudflared-tunnel.service"
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"
UNIT_PATHS=(
  "/etc/systemd/system/${UNIT}"
  "/lib/systemd/system/${UNIT}"
  "/usr/lib/systemd/system/${UNIT}"
)

log() { echo "[${SCRIPT_NAME}] $*"; }

log "Target unit: ${UNIT}"

# 1. Was it ever installed? (unit-files list is authoritative; an active-but-
#    transient unit also shows under list-units.)
if systemctl list-unit-files "${UNIT}" 2>/dev/null | grep -q "${UNIT}" \
   || systemctl list-units --all "${UNIT}" 2>/dev/null | grep -q "${UNIT}"; then
  present=1
else
  present=0
fi

# Report pre-state regardless.
pre_active="$(systemctl is-active "${UNIT}" 2>/dev/null || true)"
pre_enabled="$(systemctl is-enabled "${UNIT}" 2>/dev/null || true)"
log "Pre-state: is-active=${pre_active:-unknown} is-enabled=${pre_enabled:-unknown} present=${present}"

if [ "${present}" -eq 0 ] && [ ! -e "/etc/systemd/system/${UNIT}" ]; then
  log "Nothing to purge — ${UNIT} is not installed on this VM. (no-op success)"
  exit 0
fi

# 2. Stop + disable (idempotent; ignore 'not loaded' on an already-gone unit).
log "Stopping + disabling ${UNIT} …"
sudo systemctl disable --now "${UNIT}" 2>&1 || log "disable --now returned nonzero (likely already stopped/absent) — continuing."

# 3. Remove the unit file(s) + any token drop-in so it can't be re-loaded.
for p in "${UNIT_PATHS[@]}"; do
  if [ -e "${p}" ]; then
    log "Removing unit file ${p}"
    sudo rm -f "${p}"
  fi
done
if [ -d "${DROPIN_DIR}" ]; then
  log "Removing drop-in dir ${DROPIN_DIR}"
  sudo rm -rf "${DROPIN_DIR}"
fi

# 4. Reload + reset any failed state.
log "Reloading systemd daemon + resetting failed state …"
sudo systemctl daemon-reload
sudo systemctl reset-failed "${UNIT}" 2>/dev/null || true

# 5. Verify it's gone.
post_active="$(systemctl is-active "${UNIT}" 2>/dev/null || true)"
post_loaded="$(systemctl list-unit-files "${UNIT}" 2>/dev/null | grep -c "${UNIT}" || true)"
log "Post-state: is-active=${post_active:-inactive} unit-files-matching=${post_loaded:-0}"

if [ "${post_active}" = "active" ] || { [ -n "${post_loaded}" ] && [ "${post_loaded}" -gt 0 ]; }; then
  log "WARNING: ${UNIT} still present after purge — manual inspection needed."
  exit 1
fi

log "Done — ${UNIT} stopped, disabled, and removed. Cloudflare tunnel fully purged from the VM."
exit 0
