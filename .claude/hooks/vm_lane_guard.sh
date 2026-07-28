#!/usr/bin/env bash
# VM-lane guard — the structural teeth on the FIFO VM-lane queue, mirroring the
# merge-slot PreToolUse guard (docs/claude/coordination-board.md § Enforcement).
#
# Operator directive (2026-07-28): "we need some sort of queue like we have for
# merging so that everyone knows who is using the VM … nothing new starts till
# what is running ends, FIFO." The board VM-LANE CLAIM protocol is the queue;
# this guard makes claiming a PHYSICAL PRECONDITION of dispatching a HEAVY
# trainer-VM job, so the FIFO can't be silently skipped under load (the exact
# failure that forced the merge guard, 2026-07-27).
#
# Scope (DELIBERATELY NARROW + FAIL-OPEN so it can never strand the relays):
#   - Fires ONLY on mcp__github__issue_write whose payload carries the
#     `trainer-vm-heavy-request` label (a HEAVY/exclusive trainer job).
#   - Quick `trainer-vm-diag-request` reads, system-actions, prop-reports, and
#     every other issue create are NOT matched → always allowed.
#   - DENIES only when the heavy label is present AND no fresh (< 30 min) claim
#     marker /tmp/.claude-vm-lane-claim-<session_id> exists.
#   - Any parse ambiguity / error → ALLOW (exit 0). Never blocks on doubt.
#
# The marker is a speed-bump proving the session ran the claim protocol for its
# job — it is NOT the claim itself; the `🔒 VM-LANE CLAIM` comment on board #6927
# is what other sessions actually see (post it for real). Touch the marker only
# AFTER reading the board tail (lane free?) and posting the claim.

set -u

input="$(cat)"

# Extract fields from the hook stdin JSON with sed (jq may be absent on the
# runner/host; sed keeps this dependency-free like the existing nudge hook).
field() { printf '%s' "$input" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n1; }

tool="$(field tool_name)"
sid="$(field session_id)"

# Only guard github issue writes; everything else passes untouched.
case "$tool" in
  *issue_write*) ;;
  *) exit 0 ;;
esac

# Only guard HEAVY trainer dispatches. If the payload doesn't mention the heavy
# label, this is a quick read / action / prop / other issue — allow.
if ! printf '%s' "$input" | grep -q 'trainer-vm-heavy-request'; then
  exit 0
fi

# Heavy trainer dispatch. Require a fresh VM-lane claim marker.
marker="/tmp/.claude-vm-lane-claim-${sid:-default}"
if [ -f "$marker" ]; then
  # Fresh (< 30 min)? find -mmin +30 prints the path when OLDER than 30 min.
  if [ -z "$(find "$marker" -mmin +30 2>/dev/null)" ]; then
    exit 0   # fresh claim present → allow the heavy dispatch
  fi
fi

# No fresh claim → DENY with the runbook.
deny_reason=$(cat <<'EOF'
VM-LANE CLAIM REQUIRED before a HEAVY trainer-VM job (trainer-vm-heavy-request).
The trainer VM is a single core shared across sessions — nothing new starts until the running job ends (FIFO). Do this FIRST:
1) FIRST ASK: does this job need VM-RESIDENT state? If it's CPU-only (a public-feed fetch + a backtest over it), run it on a FREE GitHub runner instead (research-symbol-p0-build / research-exit-head-build pattern) — no lane, no contention. Most heavy work belongs there. See docs/claude/vm-resource-management.md.
2) If it genuinely needs the VM: read the board tail (issue #6927). If an open "🔒 VM-LANE CLAIM · trainer" has no matching "🔓 RELEASE", the lane is HELD — post "🕓 VM-LANE QUEUED · trainer" and WAIT (do not dispatch).
3) If the lane is FREE: post "🔒 VM-LANE CLAIM · trainer · <session> · <task> · ETA <min>" on #6927, then `touch /tmp/.claude-vm-lane-claim-<session_id>` and RETRY this call.
4) Post "🔓 VM-LANE RELEASE · trainer · <session>" the instant the job ends.
The marker is a speed-bump, not the claim — the board comment is what other sessions see, so post it for real.
EOF
)

# Emit a PreToolUse deny. printf-escape newlines into the JSON string.
esc=$(printf '%s' "$deny_reason" | sed ':a;N;$!ba;s/\n/\\n/g; s/"/\\"/g')
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}' "$esc"
exit 0
