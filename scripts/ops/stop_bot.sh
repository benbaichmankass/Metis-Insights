#!/usr/bin/env bash
# Tier-2 operator action: STOP the live trader systemd unit and hold it stopped.
#
# WHY THIS EXISTS. `restart-bot-service` was the only lifecycle action on the
# allowlist, so a repair that needs the trader ABSENT for a bounded window had
# no dispatch path at all. The motivating case (2026-08-26): a stranded IB
# protective group owned by the trader's own execution clientId 497. IBKR binds
# cancel rights to the SUBMITTING client, so `cancel-ib-order` must connect AS
# 497 -- and while the trader holds it, IBKR REFUSES the duplicate (Error 326)
# rather than evicting, so the cancel simply cannot land. Racing a restart
# window across two ~90 s Actions runs is not a control mechanism; this is.
#
# ⚠️ WHAT IS AT RISK WHILE STOPPED. A stopped trader evaluates NO exits: no
# monitor tick, no reconciler, no naked-autoprotect re-arm. What still protects
# an open position is its BROKER-SIDE resting bracket -- IB GTC OCA legs and
# Bybit position/partial SL-TP live on the venue, not in this process, and they
# keep working while it is down. That is the whole safety argument for a
# bounded stop, so this script REPORTS the open positions it is leaving in that
# state and records them in the audit rather than letting the window be taken
# on an unstated book.
#
# ⚠️ REFUSES UNLESS THE LIVENESS WATCHDOG IS ALREADY PAUSED. `check_heartbeat.py`
# (fired every 60 s by ict-liveness-watchdog.timer) issues `systemctl restart`
# on the trader once the heartbeat goes stale, so a stop taken without
# `pause-autoheal` is SILENTLY UNDONE minutes later -- and the operator sees a
# stop that "did not work" rather than one that was reverted. Those are
# different failures and only one of them is this script's. Pairing is enforced
# here, not left as a documented convention.
#   Note `Restart=always` on the unit is NOT the same hazard: systemd does not
#   restart a unit after an explicit `systemctl stop`.
#
# The symmetric companion is scripts/ops/start_bot.sh. Resume the watchdog with
# `resume-autoheal` after starting -- while it is paused the genuine dead-man
# switch is OFF.
#
# What this script does NOT touch: strategy params, account mode flags, risk
# caps, or any order. Stop-only.

set -euo pipefail

SCRIPT_NAME="stop_bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
WATCHDOG_TIMER="ict-liveness-watchdog.timer"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "stop-bot-service" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Same /dev/null hardening as restart_bot.sh: the OCI host agent has stripped
# /dev/null to 0444 mid-run (BL-20260713-DEVNULL-RESTART-MISREPORT), which turns
# a state read into "unknown" on an action that actually worked.
ERR_SINK="$(mktemp "${TMPDIR:-/tmp}/stop_bot_err.XXXXXX" 2>&1)" || ERR_SINK="${TMPDIR:-/tmp}/stop_bot_err.$$"
trap 'rm -f "${ERR_SINK}"' EXIT

# Defense in depth, borrowed from restart_bot.sh: never kill an in-flight runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>>"${ERR_SINK}" | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to stop ${UNIT} mid-runner."
    record_audit "stop-bot-service" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# THE PAIRING GATE. Read the timer's ACTIVE state, not just is-enabled: a timer
# can be disabled-but-still-running until the next boot, and it is the running
# one that would restart the trader out from under this window.
wd_active="$("${SYSTEMCTL[@]}" is-active "${WATCHDOG_TIMER}" 2>>"${ERR_SINK}" || echo "unknown")"
wd_enabled="$("${SYSTEMCTL[@]}" is-enabled "${WATCHDOG_TIMER}" 2>>"${ERR_SINK}" || echo "unknown")"
log "Liveness watchdog ${WATCHDOG_TIMER}: is-active=${wd_active} is-enabled=${wd_enabled}"
if [ "${wd_active}" = "active" ]; then
    log "ABORT: the liveness watchdog is still ACTIVE. It would restart ${UNIT} once the"
    log "       heartbeat goes stale, silently undoing this stop. Run pause-autoheal first,"
    log "       and resume-autoheal once the window is closed."
    record_audit "stop-bot-service" "refused" \
        "{\"reason\": \"liveness watchdog active\", \"watchdog_active\": \"${wd_active}\"}" >/dev/null || true
    exit 4
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
log "Pre-stop state of ${UNIT}: ${pre_state}"

# WHAT WE ARE LEAVING UNMANAGED. Best-effort and read-only -- a failure to read
# the book must not block a stop the operator has approved, but it must not be
# reported as "no open positions" either. Those are different facts.
open_summary='"unread"'
DB_PATH="$(runtime_db_path 2>>"${ERR_SINK}" || true)"
if [ -n "${DB_PATH:-}" ] && [ -f "${DB_PATH}" ]; then
    echo "===== open positions being left to broker-side brackets ====="
    if summary="$(python3 - "${DB_PATH}" <<'PY' 2>>"${ERR_SINK}"
import sqlite3, sys, json
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = con.execute(
    "SELECT account_id, symbol, direction, position_size, stop_loss, take_profit_1 "
    "FROM trades WHERE status = 'open' ORDER BY account_id, symbol"
).fetchall()
for a, s, d, q, sl, tp in rows:
    print(f"  {a:20s} {s:10s} {d:5s} qty={q} sl={sl} tp={tp}")
print(f"  ({len(rows)} open position(s))")
print("JSONCOUNT:" + json.dumps({"open_positions": len(rows)}))
PY
    )"; then
        echo "${summary}" | grep -v '^JSONCOUNT:' || true
        open_summary="$(echo "${summary}" | sed -n 's/^JSONCOUNT://p' | head -1)"
        [ -n "${open_summary}" ] || open_summary='"unread"'
    else
        log "WARNING: could not read open positions — proceeding, but the book is UNREAD, not empty."
    fi
else
    log "WARNING: trade journal not found — open-position report is UNREAD, not empty."
fi

log "Stopping ${UNIT}…"
"${SYSTEMCTL[@]}" stop "${UNIT}"
heal_devnull || true

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
    if [ "${post_state}" != "active" ]; then
        break
    fi
    sleep 2
done
log "Post-stop state of ${UNIT}: ${post_state}"

echo
echo "===== post-stop journalctl (last 20 lines) ====="
journalctl -u "${UNIT}" -n 20 --no-pager 2>>"${ERR_SINK}" || true

if [ "${post_state}" = "active" ]; then
    record_audit "stop-bot-service" "failed" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"open\": ${open_summary}}" >/dev/null || true
    log "ERROR: ${UNIT} was still 'active' 30 s after stop."
    exit 1
fi

record_audit "stop-bot-service" "ok" \
    "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"open\": ${open_summary}}" >/dev/null || true
log "Stopped. The trader is managing NO exits until start-bot-service runs."
log "Open positions rely on their broker-side resting brackets for the duration."
exit 0
