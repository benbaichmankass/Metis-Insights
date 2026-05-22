#!/usr/bin/env bash
# Operator-actions transparency notifier — routes the run summary
# through @claude_ict_comms_bot.
#
# Invoked by the operator-actions GitHub workflow as the final step,
# over SSH. Constructs a single Telegram-friendly message and queues
# it via scripts/send_ping.py --target claude. The Claude-bridge
# service (`ict-claude-bridge.service`) drains the queue within ~5 s
# and posts to @claude_ict_comms_bot.
#
# Usage:
#   notify_run.sh <action> <exit_code> <run_url> [reason]
#
# Priority mapping (per docs/claude/operator-actions.md § 5.5):
#   Tier 1 ok       → low
#   Tier 1 degraded → high
#   Tier 2 ok       → normal
#   Tier 2 deferred → normal  (exit 3 = vm-runner active)
#   Tier 2 failed   → urgent  (exit 1)
#   reboot-vm scheduled → high  (recovery uncertainty)
#   set-account-mode ok → normal  (audited mode flip)
#   fix-data-dir ok → normal  (audited data-dir alignment)
#
# Failures inside this script never propagate. The operator-actions
# workflow already records the action's success/failure via its own
# exit code; a notify failure shouldn't flip an otherwise-successful
# run to failed.

set -uo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
SEND_PING="${REPO_DIR}/scripts/send_ping.py"

action="${1:-unknown}"
exit_code="${2:-0}"
run_url="${3:-}"
reason_raw="${4:-}"

if [[ "${reason_raw}" == *:b64 ]]; then
    encoded="${reason_raw%:b64}"
    if [ -n "${encoded}" ]; then
        reason="$(printf '%s' "${encoded}" | base64 -d 2>/dev/null || echo "${encoded}")"
    else
        reason=""
    fi
else
    reason="${reason_raw}"
fi

case "${action}" in
    status-check|gateway-logs|pull-latest-logs|inspect-closed-pnl|bybit-account-audit|strategy-performance-audit|monitor-miss-analysis|vwap-backtest-sweep)
        tier=1
        if [ "${exit_code}" -eq 0 ]; then
            result="ok"
            priority="low"
        else
            result="degraded (exit ${exit_code})"
            priority="high"
        fi
        ;;
    restart-bot-service)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    pull-and-deploy)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    reboot-vm)
        tier=2
        case "${exit_code}" in
            0|255) result="scheduled (shutdown -r +1)"; priority="high" ;;
            *)     result="FAILED to schedule (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    enable-closed-flat-invariant|disable-closed-flat-invariant)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    enable-m5-consumer|disable-m5-consumer)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    setup-cloudflare-tunnel|teardown-cloudflare-tunnel|setup-named-cloudflare-tunnel|teardown-named-cloudflare-tunnel)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    setup-tailscale-funnel|teardown-tailscale-funnel)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    backfill-pnl-nulls|backfill-orphan-pnl|backfill-monitor-closed-pnl|revert-backfill-monitor-closed-pnl|rebuild-pnl-from-bybit|backfill-shadow-predictions|rotate-account-keys|init-diag-token)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    set-account-mode)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    enable-mes|disable-mes)
        # PR #1656/#1670: IB MES multi-symbol activation toggle (restarts trader).
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    fix-data-dir)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    *)
        tier=0
        result="UNKNOWN ACTION (exit ${exit_code})"
        priority="urgent"
        ;;
esac

body="[ops] ${action}: ${result}"
if [ -n "${reason}" ]; then
    body+=$'\n'"reason: ${reason}"
fi
if [ -n "${run_url}" ]; then
    body+=$'\n'"run: ${run_url}"
fi
body+=$'\n'"tier: ${tier}"

if [ ! -x "${SEND_PING}" ] && [ ! -f "${SEND_PING}" ]; then
    echo "WARN: ${SEND_PING} not found; cannot notify" >&2
    exit 0
fi

if /usr/bin/python3 "${SEND_PING}" \
        --target claude \
        --priority "${priority}" \
        "${body}" 2>&1; then
    echo "notify: queued ${priority} ping for action=${action} result=${result}" >&2
else
    echo "WARN: send_ping.py failed; notify skipped (action=${action})" >&2
fi
exit 0
