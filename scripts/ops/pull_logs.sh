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

echo "===== journalctl -u ict-trader-live -n ${JOURNAL_LINES} ====="
journalctl -u ict-trader-live.service -n "${JOURNAL_LINES}" --no-pager 2>/dev/null || \
    echo "(journalctl unavailable for ict-trader-live)"
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

echo "===== runtime_logs/status.json ====="
STATUS="${REPO_DIR}/runtime_logs/status.json"
if [ -f "${STATUS}" ]; then
    cat "${STATUS}"
else
    echo "(no status.json yet)"
fi
echo

# Cloudflare quick-tunnel URL — written by setup_cloudflare_tunnel.sh
# on every (re)start. Surfaced in the bundle so the operator-actions
# issue comment carries the current public hostname for the dashboard's
# Vercel rewrite, without needing a separate VM trip.
echo "===== runtime_logs/cloudflared_tunnel_url.txt ====="
TUNNEL_URL="${REPO_DIR}/runtime_logs/cloudflared_tunnel_url.txt"
if [ -f "${TUNNEL_URL}" ]; then
    cat "${TUNNEL_URL}"
else
    echo "(no cloudflared tunnel URL recorded — tunnel may not be running)"
fi

record_audit "pull-latest-logs" "ok" \
    "{\"journal_lines\": ${JOURNAL_LINES}, \"audit_lines\": ${AUDIT_LINES}}" >/dev/null || true
log "Done."
