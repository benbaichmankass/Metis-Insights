#!/usr/bin/env bash
# Tier-2 operator action: align the live VM's runtime data dir with
# the canonical /data/bot-data block-storage mount.
#
# 2026-05-12 incident root cause: the live VM's .env carried
#   DATA_DIR=data/
# (a relative path) from before the OCI block-storage migration.
# src/utils/paths.py anchors a relative umbrella to repo_root, so the
# trader wrote runtime_logs/heartbeat/runtime_status/signal_audit to
# /home/ubuntu/ict-trading-bot/data/runtime_logs/ instead of the
# canonical /data/bot-data/runtime_logs/. All readers (watchdog,
# health-snapshot collector, web-api, dashboard, set-account-mode's
# verification probe) looked at the canonical path and saw stale
# 2026-05-11 mtimes — a split-brain that produced:
#   - phantom "heartbeat-writer silent failure" (FU-20260511-008)
#   - phantom "bybit_2 silent flip" (status served stale May-11 JSON)
#   - real ict-web-api + ict-claude-bridge crashloop (units load
#     files at canonical paths, files are at the split path, they die
#     on startup).
#
# Per operator directive 2026-05-12:
#   "ENV is not the canonical source of anything. If the ENV doesn't
#    comply with [canonical docs], the ENV needs to be changed. The
#    ENV is a product of our work; it is not what decides how the
#    work gets done."
#
# Canonical declaration is in the systemd drop-ins at
# /etc/systemd/system/ict-*.service.d/data-dir.conf:
#   Environment=DATA_DIR=/data/bot-data
#   Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db
#   RequiresMountsFor=/data/bot-data
# This wrapper makes .env conform by removing the override that
# contradicts it, then migrates any data already at the split path
# to the canonical path, then restarts every canonical unit so they
# come up reading + writing the same files.
#
# Steps (all idempotent):
#   1. Snapshot pre-state: .env DATA_DIR/TRADE_JOURNAL_DB lines, per-
#      unit is-active, file inventories at both candidate paths.
#   2. Stop ict-trader-live + ict-web-api + ict-claude-bridge +
#      ict-telegram-bot so nothing writes mid-migration.
#   3. Strip DATA_DIR= and TRADE_JOURNAL_DB= overrides from .env
#      (atomic tmp+rename, .bak backup retained). systemd drop-in
#      then wins on the next start.
#   4. Verify /data/bot-data and /data/bot-data/runtime_logs exist
#      and are writable; create if missing.
#   5. rsync /home/ubuntu/ict-trading-bot/data/runtime_logs/ →
#      /data/bot-data/runtime_logs/ (preserve attrs; do not delete).
#      Same for runtime_state/ and artifacts/ if they have content
#      at the split path.
#   6. systemctl daemon-reload (in case any unit file changed since
#      last boot — cheap safety).
#   7. Start all four units in order, poll each to 'active', dump 30
#      journal lines per unit.
#   8. Verify post-state: /api/health 200, canonical runtime_logs
#      heartbeat freshly written, .env contains no DATA_DIR override.
#
# Tier-2 (mutates .env + restarts every canonical unit). Sibling of
# set-account-mode — both are explicit, named, audited wires for the
# one structural class they govern.

set -euo pipefail

SCRIPT_NAME="fix_data_dir"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
ENV_FILE="${REPO_DIR}/.env"
SPLIT_DATA="${REPO_DIR}/data"
CANONICAL_DATA="/data/bot-data"

UNITS=(
    "ict-trader-live.service"
    "ict-web-api.service"
    "ict-claude-bridge.service"
    "ict-telegram-bot.service"
)

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "fix-data-dir" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defer if a /vm runner is mid-flight.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to migrate mid-runner."
    record_audit "fix-data-dir" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: .env not found at ${ENV_FILE}. Cannot run."
    record_audit "fix-data-dir" "error" "{\"reason\": \"env file missing\"}" >/dev/null || true
    exit 1
fi

# ----------------------------------------------------------------------
# 1. Pre-state snapshot
# ----------------------------------------------------------------------
log "Pre-state snapshot..."
echo "===== .env DATA_DIR / TRADE_JOURNAL_DB lines (pre-strip) ====="
grep -E '^(DATA_DIR|TRADE_JOURNAL_DB)=' "${ENV_FILE}" || echo "(no overrides present)"
echo

echo "===== unit states (pre-stop) ====="
for unit in "${UNITS[@]}"; do
    state="$("${SYSTEMCTL[@]}" is-active "${unit}" 2>/dev/null || echo "unknown")"
    printf '  %-35s %s\n' "${unit}" "${state}"
done
echo

# Note: `find | head` under `set -o pipefail` exits with SIGPIPE (141)
# when find has more entries than head consumes (the previous version
# of this script hit exactly that on a VM with ~50 pending_pings
# files). Pipe into a subshell wrapper so the upstream's SIGPIPE
# doesn't fail the whole script. The inventory is informational only.
echo "===== split-path inventory (${SPLIT_DATA}, first 40) ====="
if [ -d "${SPLIT_DATA}" ]; then
    { find "${SPLIT_DATA}" -type f -printf '  %TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null || true; } | head -40 || true
else
    echo "  (no split path; nothing to migrate)"
fi
echo

echo "===== canonical inventory (${CANONICAL_DATA}/runtime_logs, first 20) ====="
if [ -d "${CANONICAL_DATA}/runtime_logs" ]; then
    { find "${CANONICAL_DATA}/runtime_logs" -maxdepth 1 -type f -printf '  %TY-%Tm-%Td %TH:%TM  %10s  %p\n' 2>/dev/null || true; } | head -20 || true
else
    echo "  (canonical runtime_logs does not exist yet; will be created)"
fi
echo

# ----------------------------------------------------------------------
# 2. Stop services (in reverse-dependency order so claude-bridge
#    drains its queue cleanly).
# ----------------------------------------------------------------------
log "Stopping units..."
for unit in ict-claude-bridge.service ict-telegram-bot.service ict-web-api.service ict-trader-live.service; do
    if "${SYSTEMCTL[@]}" is-active --quiet "${unit}" 2>/dev/null; then
        "${SYSTEMCTL[@]}" stop "${unit}" || log "WARN: failed to stop ${unit} (may be already inactive)"
    fi
done
sleep 2  # let writers flush

# ----------------------------------------------------------------------
# 3. Strip DATA_DIR / TRADE_JOURNAL_DB overrides from .env
# ----------------------------------------------------------------------
log "Stripping DATA_DIR / TRADE_JOURNAL_DB overrides from .env (systemd drop-in becomes authoritative)..."

# Backup .env once. If a backup exists from a prior run, keep it.
if [ ! -f "${ENV_FILE}.bak" ]; then
    cp -a "${ENV_FILE}" "${ENV_FILE}.bak"
    log "Backup written: ${ENV_FILE}.bak"
fi

tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "${tmp_env}"' EXIT

# Filter out DATA_DIR= and TRADE_JOURNAL_DB= lines. Also strip the
# preceding comment block if it's a # comment immediately above the
# stripped line, to keep .env tidy.
grep -vE '^(DATA_DIR|TRADE_JOURNAL_DB)=' "${ENV_FILE}" > "${tmp_env}"

chown --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
chmod --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
mv "${tmp_env}" "${ENV_FILE}"
trap - EXIT

echo "===== .env DATA_DIR / TRADE_JOURNAL_DB lines (post-strip) ====="
if grep -qE '^(DATA_DIR|TRADE_JOURNAL_DB)=' "${ENV_FILE}"; then
    grep -E '^(DATA_DIR|TRADE_JOURNAL_DB)=' "${ENV_FILE}"
    log "ERROR: post-strip .env still contains DATA_DIR / TRADE_JOURNAL_DB lines."
    record_audit "fix-data-dir" "failed" '{"reason": "env strip verification failed"}' >/dev/null || true
    exit 1
else
    echo "  (clean — no overrides remain)"
fi
echo

# ----------------------------------------------------------------------
# 4. Verify canonical path is writable
# ----------------------------------------------------------------------
if [ ! -d "${CANONICAL_DATA}" ]; then
    log "ERROR: canonical ${CANONICAL_DATA} mount does not exist. Block-storage migration not in place. Refusing to proceed."
    record_audit "fix-data-dir" "error" "{\"reason\": \"canonical mount missing\", \"path\": \"${CANONICAL_DATA}\"}" >/dev/null || true
    exit 1
fi

mkdir -p "${CANONICAL_DATA}/runtime_logs" "${CANONICAL_DATA}/runtime_state" "${CANONICAL_DATA}/artifacts" "${CANONICAL_DATA}/data"
touch "${CANONICAL_DATA}/runtime_logs/.fix-data-dir-write-test" && rm "${CANONICAL_DATA}/runtime_logs/.fix-data-dir-write-test"
log "Canonical path ${CANONICAL_DATA} is writable."

# ----------------------------------------------------------------------
# 5. rsync split → canonical (preserve, no delete)
# ----------------------------------------------------------------------
if [ -d "${SPLIT_DATA}" ]; then
    log "Migrating split-path content (${SPLIT_DATA}/) → canonical (${CANONICAL_DATA}/)..."
    for sub in runtime_logs runtime_state artifacts data; do
        if [ -d "${SPLIT_DATA}/${sub}" ] && [ "$(ls -A "${SPLIT_DATA}/${sub}" 2>/dev/null)" ]; then
            log "  rsync ${sub}/..."
            rsync -a --info=stats1 "${SPLIT_DATA}/${sub}/" "${CANONICAL_DATA}/${sub}/" || {
                log "ERROR: rsync of ${sub}/ failed"
                record_audit "fix-data-dir" "failed" "{\"reason\": \"rsync ${sub} failed\"}" >/dev/null || true
                exit 1
            }
        fi
    done
    # Rename the split path so the next start can't accidentally
    # write to it via a stale relative resolver.
    migrated_marker="${SPLIT_DATA}.MIGRATED-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "${SPLIT_DATA}" "${migrated_marker}"
    log "Split path renamed to ${migrated_marker} (preserved for forensic review)."
else
    log "No split path at ${SPLIT_DATA}; nothing to migrate."
fi

# ----------------------------------------------------------------------
# 6. daemon-reload (safety — no-op if no unit files changed)
# ----------------------------------------------------------------------
log "systemctl daemon-reload..."
"${SYSTEMCTL[@]}" daemon-reload

# ----------------------------------------------------------------------
# 7. Start units in dependency order; poll to 'active'
# ----------------------------------------------------------------------
for unit in ict-trader-live.service ict-web-api.service ict-telegram-bot.service ict-claude-bridge.service; do
    log "Starting ${unit}..."
    "${SYSTEMCTL[@]}" start "${unit}" || log "WARN: start ${unit} returned non-zero (will verify below)"

    deadline=$(( $(date +%s) + 30 ))
    state="unknown"
    while [ "$(date +%s)" -lt "${deadline}" ]; do
        state="$("${SYSTEMCTL[@]}" is-active "${unit}" 2>/dev/null || echo "unknown")"
        if [ "${state}" = "active" ]; then
            break
        fi
        sleep 2
    done
    log "${unit}: ${state}"
    echo "===== journalctl ${unit} -n 30 ====="
    journalctl -u "${unit}" -n 30 --no-pager 2>/dev/null || true
    echo
done

# ----------------------------------------------------------------------
# 8. Post-state verification
# ----------------------------------------------------------------------
log "Post-state verification..."

all_active=1
for unit in "${UNITS[@]}"; do
    state="$("${SYSTEMCTL[@]}" is-active "${unit}" 2>/dev/null || echo "unknown")"
    if [ "${state}" != "active" ]; then
        log "WARN: ${unit} did not return to active (state=${state})"
        all_active=0
    fi
done

# Heartbeat freshness check against the CANONICAL path.
hb_path="${CANONICAL_DATA}/runtime_logs/heartbeat.txt"
hb_fresh=0
if [ -f "${hb_path}" ]; then
    hb_age=$(( $(date +%s) - $(stat -c %Y "${hb_path}") ))
    log "Canonical heartbeat age: ${hb_age}s (path: ${hb_path})"
    if [ "${hb_age}" -lt 180 ]; then
        hb_fresh=1
    fi
else
    log "WARN: canonical heartbeat not yet present at ${hb_path}"
fi

# Web-api health probe.
web_ok=0
if curl -sf --max-time 5 http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    log "Web-api /api/health: 200 OK"
    web_ok=1
else
    log "WARN: /api/health did not return 200"
fi

# Emit audit
record_audit "fix-data-dir" \
    "$([ "${all_active}" = 1 ] && [ "${web_ok}" = 1 ] && echo ok || echo partial)" \
    "{\"units_all_active\": ${all_active}, \"web_api_health_ok\": ${web_ok}, \"heartbeat_fresh\": ${hb_fresh}, \"canonical_path\": \"${CANONICAL_DATA}\"}" \
    >/dev/null || true

if [ "${all_active}" = 1 ] && [ "${web_ok}" = 1 ]; then
    log "fix-data-dir succeeded — .env stripped of overrides, data migrated, all units active, web-api healthy."
    exit 0
fi

log "fix-data-dir completed with warnings — review the journal tails above. Heartbeat may take a tick (~60s) to land on canonical path."
exit 1
