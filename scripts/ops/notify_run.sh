#!/usr/bin/env bash
# Operator-actions transparency notifier — routes the run summary
# through @claude_ict_comms_bot.
#
# Invoked by the system-actions GitHub workflow as the final step,
# over SSH. Constructs a single Telegram-friendly message and queues
# it via scripts/send_ping.py --target claude. The Claude-bridge
# service (`ict-claude-bridge.service`) drains the queue within ~5 s
# and posts to @claude_ict_comms_bot.
#
# Usage:
#   notify_run.sh <action> <exit_code> <run_url> [reason]
#
# Priority mapping (per docs/claude/system-actions.md § 5.5):
#   Tier 1 ok       → low
#   Tier 1 degraded → high
#   Tier 2 ok       → normal
#   Tier 2 deferred → normal  (exit 3 = vm-runner active)
#   Tier 2 failed   → urgent  (exit 1)
#   reboot-vm scheduled → high  (recovery uncertainty)
#   set-account-mode ok → normal  (audited mode flip)
#   fix-data-dir ok → normal  (audited data-dir alignment)
#
# Failures inside this script never propagate. The system-actions
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

# send-ping IS the operator message — the action already delivered it.
# A transparency "[ops] send-ping: ok" ping right behind it would just be
# duplicate noise on the same channel, so skip the notify for it.
if [ "${action}" = "send-ping" ]; then
    echo "notify: send-ping is its own message; skipping transparency ping" >&2
    exit 0
fi

case "${action}" in
    status-check|list-listening-ports|gateway-logs|pull-latest-logs|inspect-closed-pnl|bybit-account-audit|strategy-performance-audit|monitor-miss-analysis|vwap-backtest-sweep|generate-strategy-review-packets|send-prop-test-ping|grade-closed-trades|net-r-regrade)
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
    enable-signal-dual-write|disable-signal-dual-write)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    enable-insights-generator|disable-insights-generator)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    inspect-insights)
        tier=1
        case "${exit_code}" in
            0) result="ok"; priority="low" ;;
            *) result="FAILED (exit ${exit_code})"; priority="normal" ;;
        esac
        ;;
    kick-insights)
        tier=1
        case "${exit_code}" in
            0) result="ok"; priority="low" ;;
            *) result="FAILED (exit ${exit_code})"; priority="normal" ;;
        esac
        ;;
    set-mobile-push-secrets)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    backfill-pnl-nulls|backfill-orphan-pnl|backfill-closed-null-pnl|backfill-monitor-closed-pnl|revert-backfill-monitor-closed-pnl|mark-reconciler-incomplete|reconcile-orphan-history|supersede-options-adoption-artifacts|supersede-reset-orphan-artifacts|supersede-intent-reduce-phantom-pnl|fix-prop-mislinked-close|rebuild-pnl-from-bybit|backfill-shadow-predictions|backfill-account-class|backfill-closed-at|backfill-trade-costs|backfill-broker-order-id|backfill-broker-truth-costs|migrate-closed-at-iso|pull-exchange-fills|pull-exchange-funding|pull-mes-ibkr-history|pull-mes-ibkr-history-daily|pull-ibkr-history|rotate-account-keys|init-diag-token|reset-daily-risk-state|repair-malformed-notes|repair-netted-rows)
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
    set-env)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    scrub-env-noncompliant)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    pause-autoheal|resume-autoheal)
        # 2026-06-05: pause/resume the liveness-watchdog autoheal timer.
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    sync-clock)
        # 2026-06-05: diagnose + correct VM clock drift (NTP).
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    purge-cloudflared)
        # 2026-06-10: purge the orphaned ict-cloudflared-tunnel.service from the VM.
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    flatten-ib-position|flatten-bybit-position|flatten-alpaca-position)
        # 2026-06-19/2026-06-29/2026-07-15: one-shot guarded flatten of one
        # IB/Bybit/Alpaca position (dry-run unless apply:true).
        tier=2
        case "${exit_code}" in
            0) result="ok (dry-run preview or flattened)"; priority="normal" ;;
            *) result="FAILED/refused (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    close-stranded-journal-row)
        # 2026-07-15: close a stranded open journal row whose broker position is
        # already flat (dry-run unless apply:true; refuses unless broker-flat).
        tier=2
        case "${exit_code}" in
            0) result="ok (dry-run preview or row closed)"; priority="normal" ;;
            *) result="FAILED/refused (exit ${exit_code})"; priority="urgent" ;;
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
