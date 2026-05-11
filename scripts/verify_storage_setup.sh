#!/usr/bin/env bash
# scripts/verify_storage_setup.sh — sanity-check the OCI storage setup
# on the live VM. Intended to run on the VM (over SSH), but pure-bash
# so the test suite can shellcheck it.
#
# Exit codes:
#   0 — mount + data dir present (warnings allowed).
#   1 — mount or data dir missing.
set -euo pipefail

MOUNT_POINT="${MOUNT_POINT:-/data}"
DATA_DIR="${DATA_DIR:-/data/bot-data}"
SERVICES=(ict-trader-live ict-web-api ict-telegram-bot ict-claude-bridge)
fail=0

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
err() { printf 'FAIL: %s\n' "$*" >&2; fail=1; }

log "==> mount: $MOUNT_POINT"
if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$MOUNT_POINT"; then
    df -h "$MOUNT_POINT"
else
    err "$MOUNT_POINT is not a mountpoint."
fi

log "==> data dir: $DATA_DIR"
if [[ -d "$DATA_DIR" ]]; then
    ls -ld "$DATA_DIR"
else
    err "$DATA_DIR is missing."
fi

log "==> fstab entry for $MOUNT_POINT"
if grep -E "\\s${MOUNT_POINT}\\s" /etc/fstab >/dev/null 2>&1; then
    grep -E "\\s${MOUNT_POINT}\\s" /etc/fstab
else
    warn "no fstab entry — mount will not survive reboot."
fi

log "==> systemd DATA_DIR for trader services"
for svc in "${SERVICES[@]}"; do
    if ! systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
        warn "$svc not installed; skipping."
        continue
    fi
    env_line=$(systemctl show -p Environment --value "$svc" 2>/dev/null || true)
    if printf '%s' "$env_line" | tr ' ' '\n' | grep -qx "DATA_DIR=${DATA_DIR}"; then
        log "  OK: $svc has DATA_DIR=$DATA_DIR"
    else
        warn "$svc has no DATA_DIR=$DATA_DIR — install deploy/dropins/data-dir.conf."
    fi
done

exit $fail
