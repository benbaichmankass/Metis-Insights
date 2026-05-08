#!/usr/bin/env bash
# Tier-1 operator action: collect a one-shot health snapshot.
#
# Reads only. Does not restart anything. Safe to run autonomously.
#
# Output to stdout:
#   - systemctl is-active for the canonical trading services
#   - heartbeat.txt mtime + age in seconds
#   - last 20 lines of journalctl for ict-trader-live
#   - last 5 lines of signal_audit.jsonl
#
# Exit codes:
#   0 — all canonical services active
#   1 — at least one canonical service is not active

set -euo pipefail

SCRIPT_NAME="status_check"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

CANONICAL_UNITS=(
    ict-trader-live.service
    ict-web-api.service
    ict-telegram-bot.service
)

log "Collecting service status…"
echo "===== systemctl is-active ====="
overall_ok=0
for unit in "${CANONICAL_UNITS[@]}"; do
    state="$(systemctl is-active "${unit}" 2>/dev/null || echo "unknown")"
    printf '%-32s %s\n' "${unit}" "${state}"
    if [ "${state}" != "active" ]; then
        overall_ok=1
    fi
done

# claude bridge is optional — report but don't fail on it.
if [ -f /etc/systemd/system/ict-claude-bridge.service ]; then
    state="$(systemctl is-active ict-claude-bridge.service 2>/dev/null || echo "unknown")"
    printf '%-32s %s (optional)\n' "ict-claude-bridge.service" "${state}"
fi

echo
echo "===== heartbeat ====="
HEARTBEAT="${REPO_DIR}/runtime_logs/heartbeat.txt"
if [ -f "${HEARTBEAT}" ]; then
    mtime="$(stat -c %Y "${HEARTBEAT}")"
    now="$(date +%s)"
    age=$(( now - mtime ))
    printf 'path:    %s\n' "${HEARTBEAT}"
    printf 'mtime:   %s\n' "$(date -u -d "@${mtime}" +%Y-%m-%dT%H:%M:%SZ)"
    printf 'age_sec: %d\n' "${age}"
else
    echo "MISSING: ${HEARTBEAT}"
    overall_ok=1
fi

echo
echo "===== journalctl -u ict-trader-live -n 20 ====="
journalctl -u ict-trader-live.service -n 20 --no-pager 2>/dev/null || \
    echo "(journalctl unavailable)"

echo
echo "===== tail -n 5 runtime_logs/signal_audit.jsonl ====="
AUDIT="${REPO_DIR}/runtime_logs/signal_audit.jsonl"
if [ -f "${AUDIT}" ]; then
    tail -n 5 "${AUDIT}"
else
    echo "(no audit log yet)"
fi

if [ "${overall_ok}" -eq 0 ]; then
    record_audit "status-check" "ok" "{\"all_active\": true}" >/dev/null || true
    log "All canonical services active."
    exit 0
else
    record_audit "status-check" "degraded" "{\"all_active\": false}" >/dev/null || true
    log "One or more canonical services are NOT active."
    exit 1
fi
