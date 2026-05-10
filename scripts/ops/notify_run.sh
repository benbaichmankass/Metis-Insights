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
# Args:
#   action     — operator-actions allowlisted name (status-check,
#                pull-latest-logs, pull-and-deploy, restart-bot-service,
#                reboot-vm)
#   exit_code  — wrapper exit code captured by the workflow
#   run_url    — GitHub Actions run URL for click-through
#   reason     — operator-supplied reason input (Tier-2; may be empty)
#
# Priority mapping (per docs/claude/operator-actions.md § 5.5):
#   Tier 1 ok       → low
#   Tier 1 degraded → high
#   Tier 2 ok       → normal
#   Tier 2 deferred → normal  (exit 3 = vm-runner active)
#   Tier 2 failed   → urgent  (exit 1)
#   reboot-vm scheduled → high  (recovery uncertainty)
#   pull-and-deploy ok → normal  (same shape as restart-bot-service)
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

# The workflow may pass reason base64-encoded with a `:b64` suffix
# to avoid shell-quoting hazards over SSH. Decode if present.
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

# Resolve tier + priority + result label from action and exit code.
case "${action}" in
    status-check|pull-latest-logs)
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
        # The SSH step exits non-zero on reboot because the connection
        # drops; the workflow treats that as scheduled-success. Either
        # way the operator wants to know.
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
    setup-cloudflare-tunnel|teardown-cloudflare-tunnel)
        tier=2
        case "${exit_code}" in
            0) result="ok"; priority="normal" ;;
            3) result="deferred — vm-runner active, retry later"; priority="normal" ;;
            *) result="FAILED (exit ${exit_code})"; priority="urgent" ;;
        esac
        ;;
    *)
        # Unknown action — still notify but flag it as a contract drift.
        tier=0
        result="UNKNOWN ACTION (exit ${exit_code})"
        priority="urgent"
        ;;
esac

# Compose the message body. Keep it short — Telegram renders each
# pending-ping JSON's "body" verbatim. A leading [ops] tag makes the
# Claude bot channel scannable.
body="[ops] ${action}: ${result}"
if [ -n "${reason}" ]; then
    body+=$'\n'"reason: ${reason}"
fi
if [ -n "${run_url}" ]; then
    body+=$'\n'"run: ${run_url}"
fi
body+=$'\n'"tier: ${tier}"

# Enqueue via the canonical producer. send_ping.py exits 0 on success.
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
