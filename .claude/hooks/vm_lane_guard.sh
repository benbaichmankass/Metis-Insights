#!/usr/bin/env bash
# VM-lane guard — the structural teeth on the FIFO VM-lane queue, mirroring the
# merge-slot PreToolUse guard (docs/claude/coordination-board.md § Enforcement).
#
# Operator directive (2026-07-28): "we need some sort of queue like we have for
# merging so that everyone knows who is using the VM … nothing new starts till
# what is running ends, FIFO." Originally the board VM-LANE CLAIM protocol was
# the queue and this guard made claiming it a PHYSICAL PRECONDITION of
# dispatching a HEAVY trainer-VM job.
#
# ⚠️ UPDATED 2026-09-21 (PI-20260921-0001): the board is archived and its
# skill (session-coordination) is archived, so "claim, then post on the board"
# has no live target any more — and docs/claude/coordination-board.md (MEASURED
# 2026-08-20) records this guard as inert on Claude Code on the web anyway,
# where most sessions run. This hook is kept as a local speed-bump for the
# runtimes where hooks DO fire, but the real serialization for the
# `trainer-vm-heavy-request` path now lives in `trainer-vm-diag.yml` itself
# (a flock-wrap around the same on-VM lock `python -m ml train` uses) — see
# that workflow's "Serialize on the shared heavy-job lock" step and
# docs/claude/vm-resource-management.md § 3.
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
#
# ⚠️ REWRITTEN 2026-09-21 (PI-20260921-0001). The old message here told a
# session to "read the board tail (issue #6927)" and post 🔒/🕓/🔓 comments
# there. That board is ARCHIVED (docs/claude/board-pointer.json no longer
# exists at its old path) and the session-coordination skill that
# operationalized the protocol is archived too — following the old text would
# send a session to comment on a dead, do-not-post-there issue. Do not
# resurrect that instruction.
deny_reason=$(cat <<'EOF'
VM-LANE CLAIM MARKER REQUIRED before a HEAVY trainer-VM job (trainer-vm-heavy-request).
The trainer VM is a single core shared across sessions. Do this FIRST:
1) FIRST ASK: does this job need VM-RESIDENT state? If it's CPU-only (a public-feed fetch + a backtest over it), run it on a FREE GitHub runner instead (research-symbol-p0-build / research-exit-head-build pattern) — no lane, no contention. Most heavy work belongs there. See docs/claude/vm-resource-management.md.
2) If it genuinely needs the VM: `trainer-vm-diag.yml` now wraps an issues-triggered `trainer-vm-heavy-request` dispatch in the real on-VM flock (runtime_logs/trainer/.heavy.lock, the same lock python -m ml train/build-dataset already uses) — a second heavy dispatch will itself wait or refuse (exit 75) rather than collide, no board post needed for that part.
3) This marker is only a local speed-bump proving you thought about it before dispatching: `touch /tmp/.claude-vm-lane-claim-<session_id>` and RETRY this call.
4) There is no coordination-board release step any more — the flock in step 2 releases itself when the job ends.
NOTE: this hook does not fire on Claude Code on the web (that runtime loads no project hooks — see docs/claude/coordination-board.md § "Enforcement"), so on the web this deny is never seen and step 2's flock-wrap is the only real protection.
EOF
)

# Emit a PreToolUse deny. printf-escape newlines into the JSON string.
esc=$(printf '%s' "$deny_reason" | sed ':a;N;$!ba;s/\n/\\n/g; s/"/\\"/g')
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}' "$esc"
exit 0
