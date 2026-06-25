#!/usr/bin/env bash
# Tier-1 operator action: emit a bundle of recent log content.
#
# Reads only. Useful when the diag /api/diag/log_file route is
# unavailable (web-api down, port blocked) or when a wider tail is
# needed than the diag API caps allow.
#
# Output to stdout in plain text with section headers; the workflow
# captures it into the artifact bundle.

set -euo pipefail

SCRIPT_NAME="pull_logs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Cap the number of journal/log lines so the artifact stays under the
# 10 MB GitHub artifact-comment limit even on a noisy day.
JOURNAL_LINES="${JOURNAL_LINES:-500}"
AUDIT_LINES="${AUDIT_LINES:-200}"

log "Collecting recent logs…"

# Section ordering is intentional: system-actions truncates this
# bundle to the last 50 KB before posting back to the issue comment
# (the issue API caps at 65 KB). The MOST diagnostically useful
# content has to live at the BOTTOM of the bundle so it survives
# truncation. Order, lowest-priority first → highest-priority last:
#
#   1. status.json                     (tiny + rarely useful)
#   2. journalctl ict-web-api          (web-api context)
#   3. journalctl ict-telegram-bot     (telegram-side errors)
#   4. signal_audit.jsonl tail         (pipeline behaviour)
#   5. journalctl ict-trader-live      (the bot's own python log)
#
# Pre-2026-05-11 the order was inverted and an incident's bot.log was
# truncated out of the 50 KB window. Don't rearrange without measuring
# the truncation cutoff.

echo "===== runtime_logs/status.json ====="
STATUS="${REPO_DIR}/runtime_logs/status.json"
if [ -f "${STATUS}" ]; then
    cat "${STATUS}"
else
    echo "(no status.json yet)"
fi
echo

echo "===== runtime_logs/heartbeat.txt + runtime_status.json (mtimes) ====="
# Resolve heartbeat the same way the TRADER writes it (BL-20260605-005 /
# BL-20260618-STALEHEARTBEAT): the trader runs with DATA_DIR=/data/bot-data, so
# heartbeat.txt lives under $DATA_DIR/runtime_logs — the repo-relative copy froze
# at the 2026-05-12 data-dir cutover and reads ~weeks stale (a false alarm).
# Prefer DATA_DIR, then the canonical /data/bot-data mount, then the repo path —
# mirrors scripts/ops/status_check.sh.
HEARTBEAT=""
for _hb in \
    ${DATA_DIR:+"${DATA_DIR}/runtime_logs/heartbeat.txt"} \
    "/data/bot-data/runtime_logs/heartbeat.txt" \
    "${REPO_DIR}/runtime_logs/heartbeat.txt"; do
    if [ -f "${_hb}" ]; then HEARTBEAT="${_hb}"; break; fi
done
[ -z "${HEARTBEAT}" ] && HEARTBEAT="${REPO_DIR}/runtime_logs/heartbeat.txt"
RUNTIME_STATUS="${REPO_DIR}/runtime_logs/runtime_status.json"
for f in "${HEARTBEAT}" "${RUNTIME_STATUS}"; do
    if [ -f "$f" ]; then
        stat -c '%n  mtime=%y  size=%s' "$f" 2>/dev/null || ls -la "$f"
    else
        echo "$f  (missing)"
    fi
done
echo

echo "===== journalctl -u ict-web-api -n ${JOURNAL_LINES} ====="
journalctl -u ict-web-api.service -n "${JOURNAL_LINES}" --no-pager 2>/dev/null || \
    echo "(journalctl unavailable for ict-web-api)"
echo

echo "===== journalctl -u ict-telegram-bot -n ${JOURNAL_LINES} ====="
journalctl -u ict-telegram-bot.service -n "${JOURNAL_LINES}" --no-pager 2>/dev/null || \
    echo "(journalctl unavailable for ict-telegram-bot)"
echo

echo "===== tail -n ${AUDIT_LINES} runtime_logs/signal_audit.jsonl ====="
AUDIT="${REPO_DIR}/runtime_logs/signal_audit.jsonl"
if [ -f "${AUDIT}" ]; then
    tail -n "${AUDIT_LINES}" "${AUDIT}"
else
    echo "(no audit log yet)"
fi
echo

echo "===== journalctl -u ict-trader-live -n ${JOURNAL_LINES} ====="
journalctl -u ict-trader-live.service -n "${JOURNAL_LINES}" --no-pager 2>/dev/null || \
    echo "(journalctl unavailable for ict-trader-live)"
echo

record_audit "pull-latest-logs" "ok" \
    "{\"journal_lines\": ${JOURNAL_LINES}, \"audit_lines\": ${AUDIT_LINES}}" >/dev/null || true
log "Done."
