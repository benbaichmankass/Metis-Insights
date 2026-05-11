#!/usr/bin/env bash
# scripts/migrate_journal_db.sh — migrate trade_journal.db (and any
# associated -wal / -shm files) from the repo root to the OCI-mounted
# /data/bot-data directory.
#
# Unlike scripts/migrate_to_data_dir.sh (which rsyncs runtime_logs etc.
# safely while the trader is running), the SQLite journal CANNOT be
# copied with the trader running — open file handles, WAL state, and
# locked rows would all corrupt the copy. This script therefore:
#   1. Stops the trader services that hold the DB open.
#   2. Copies the DB + -wal + -shm to the new location, preserving
#      mode and timestamp.
#   3. Chowns to ubuntu:ubuntu so the trader can write.
#   4. Runs PRAGMA integrity_check on the destination.
#   5. Leaves services STOPPED on success — the caller (the
#      oci-storage workflow) installs the data-dir.conf drop-in
#      (which sets TRADE_JOURNAL_DB) and `systemctl restart`, which
#      brings services up on the new path. On integrity failure,
#      restarts services on the OLD path so the trader stays alive.
#
# The source files are NOT renamed or deleted. Rollback = remove the
# drop-in line; the trader falls back to the repo path (which still
# has the pre-migration state).
#
# Idempotent: if the destination DB's mtime is ≥ source's, the script
# is a no-op (services are not stopped).
#
# Usage:
#   sudo ./scripts/migrate_journal_db.sh                # full execute
#   sudo ./scripts/migrate_journal_db.sh --dry-run      # show plan
#
# Env:
#   REPO_DIR   repo root (default: parent of this script's dir)
#   SRC_DB     source DB path; default $REPO_DIR/trade_journal.db
#   DEST_DB    destination DB path; default /data/bot-data/trade_journal.db
#   SERVICES   space-separated services to stop;
#              default "ict-trader-live ict-web-api ict-claude-bridge"
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
SRC_DB="${SRC_DB:-$REPO_DIR/trade_journal.db}"
DEST_DB="${DEST_DB:-/data/bot-data/trade_journal.db}"
SERVICES="${SERVICES:-ict-trader-live ict-web-api ict-claude-bridge}"
DRYRUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRYRUN=true ;;
        -h|--help) sed -n '2,42p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 64 ;;
    esac
done

log() { printf '[migrate_journal_db] %s\n' "$*"; }
err() { printf '[migrate_journal_db] ERROR: %s\n' "$*" >&2; }

restart_old() {
    # Called on failure paths: try to leave services running on the
    # old DB so the trader is alive even if the migration aborted.
    for svc in $SERVICES; do
        if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
            systemctl start "$svc" 2>/dev/null || true
        fi
    done
}

if [[ ! -f "$SRC_DB" ]]; then
    err "source $SRC_DB missing"
    exit 2
fi
if [[ ! -d "$(dirname "$DEST_DB")" ]]; then
    err "destination directory $(dirname "$DEST_DB") does not exist — run check_data_dir.sh first"
    exit 3
fi

log "src:      $SRC_DB"
log "dst:      $DEST_DB"
log "services: $SERVICES"

# Idempotency: if destination is newer or equal, skip.
if [[ -f "$DEST_DB" ]]; then
    src_mtime=$(stat -c %Y "$SRC_DB" 2>/dev/null || echo 0)
    dst_mtime=$(stat -c %Y "$DEST_DB" 2>/dev/null || echo 0)
    if [[ "$dst_mtime" -ge "$src_mtime" ]]; then
        log "destination is up to date (dst $dst_mtime >= src $src_mtime); no-op"
        exit 0
    fi
    log "destination exists but source is newer (dst $dst_mtime < src $src_mtime); refreshing"
fi

if $DRYRUN; then
    log "DRY-RUN: would stop services, cp source + -wal + -shm, chown, integrity_check; leave services stopped on success"
    exit 0
fi

# 1. Stop services.
for svc in $SERVICES; do
    if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
        log "stopping $svc"
        systemctl stop "$svc" || true
    else
        log "$svc not installed; skipping stop"
    fi
done

# 2. Copy main + WAL + SHM (whatever exists).
for ext in '' '-wal' '-shm'; do
    src="${SRC_DB}${ext}"
    dst="${DEST_DB}${ext}"
    if [[ -f "$src" ]]; then
        log "cp -p $src $dst"
        cp -p "$src" "$dst"
    fi
done

# 3. Chown so the trader can write. Best-effort — if chown fails (e.g.,
# ubuntu user already owns it), don't abort.
chown ubuntu:ubuntu "$DEST_DB" 2>/dev/null || true
[[ -f "${DEST_DB}-wal" ]] && chown ubuntu:ubuntu "${DEST_DB}-wal" 2>/dev/null || true
[[ -f "${DEST_DB}-shm" ]] && chown ubuntu:ubuntu "${DEST_DB}-shm" 2>/dev/null || true

# 4. Verify integrity at the destination. PRAGMA integrity_check returns
# "ok" on a clean DB; anything else means the copy is corrupt and we
# must NOT leave the trader pointing at it.
if command -v sqlite3 >/dev/null 2>&1; then
    integ=$(sqlite3 "$DEST_DB" 'PRAGMA integrity_check;' 2>&1 | head -3 | tr '\n' ' ')
    case "$integ" in
        "ok"*|*" ok"*)
            log "integrity_check: ok"
            ;;
        *)
            err "integrity_check failed on $DEST_DB: $integ"
            err "restarting services on the OLD path so the trader stays alive"
            restart_old
            exit 4
            ;;
    esac
else
    log "sqlite3 not available; skipping integrity check"
fi

log "DB copied. Services LEFT STOPPED — caller installs the data-dir.conf drop-in (which sets TRADE_JOURNAL_DB=$DEST_DB) and \`systemctl restart\` to flip."
