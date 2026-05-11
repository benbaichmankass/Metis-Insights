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
TRADE_JOURNAL_DB_DEFAULT="${TRADE_JOURNAL_DB_DEFAULT:-/data/bot-data/trade_journal.db}"
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

log "==> trade_journal.db at $TRADE_JOURNAL_DB_DEFAULT"
if [[ -f "$TRADE_JOURNAL_DB_DEFAULT" ]]; then
    ls -lh "$TRADE_JOURNAL_DB_DEFAULT"
    if command -v sqlite3 >/dev/null 2>&1; then
        integ=$(sqlite3 "$TRADE_JOURNAL_DB_DEFAULT" 'PRAGMA integrity_check;' 2>&1 | head -1)
        log "  integrity: $integ"
    fi
else
    warn "trade_journal.db missing at $TRADE_JOURNAL_DB_DEFAULT — run scripts/migrate_journal_db.sh"
fi

log "==> systemd env for trader services"
for svc in "${SERVICES[@]}"; do
    if ! systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
        warn "$svc not installed; skipping."
        continue
    fi
    env_line=$(systemctl show -p Environment --value "$svc" 2>/dev/null || true)
    has_dd=no
    has_tj=no
    printf '%s' "$env_line" | tr ' ' '\n' | grep -qx "DATA_DIR=${DATA_DIR}" && has_dd=yes
    printf '%s' "$env_line" | tr ' ' '\n' | grep -qx "TRADE_JOURNAL_DB=${TRADE_JOURNAL_DB_DEFAULT}" && has_tj=yes
    if [[ "$has_dd" == yes && "$has_tj" == yes ]]; then
        log "  OK: $svc has DATA_DIR + TRADE_JOURNAL_DB"
    elif [[ "$has_dd" == yes && "$has_tj" == no ]]; then
        # ict-telegram-bot intentionally has no drop-in, so both flags
        # will be "no" — that's caught by the elif below. This branch
        # fires when a trader service has DATA_DIR but is missing
        # TRADE_JOURNAL_DB (drop-in installed pre-S-OCI-FU1).
        warn "$svc has DATA_DIR but no TRADE_JOURNAL_DB — reinstall deploy/dropins/data-dir.conf"
    elif [[ "$svc" == "ict-telegram-bot" ]]; then
        # By design — telegram bot has no DATA_DIR-resident state and
        # no drop-in. Don't warn.
        log "  OK: $svc (no drop-in by design)"
    else
        warn "$svc has no DATA_DIR — install deploy/dropins/data-dir.conf"
    fi
done

exit $fail
