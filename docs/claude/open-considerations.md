# Open considerations (NOT canonical — under evaluation)

This file records design questions that are being **evaluated, not decided.**
Nothing here is a directive. It is deliberately **not** part of the canonical
doc set (see `docs/CLAUDE-RULES-CANONICAL.md` § Document Priority). Do **not**
action anything here without explicit operator direction in chat — treat each
entry as "we're still thinking about this," never "do this."

---

## Whether to remove the Claude comms/ping bot (`@claude_ict_comms_bot`)

**Status: UNDECIDED — do not scrub. No teardown is mandated.**

During the 2026-05-24 rules-cleanup session we discussed removing the
`@claude_ict_comms_bot` / `ict-claude-bridge` pipeline — the "Claude telegram
pings" flow: the session-complete ping (`Stop` hook → `notify_session.py`), the
deploy / system-action / rotate-keys transparency notify (`notify_run.sh` →
`send_ping --target claude`), and the bridge's interactive Claude chat +
recurring-session commands (`/audit`, `/improve_strategy`, `/train_model`,
`/roadmap`). It turned out to be deeply interwoven with the **live deploy
enumeration**, notification routing, and ~6 tests, so **nothing was removed.**

The operator intends to **re-evaluate / redesign this area in a dedicated
session.** Until an explicit decision is made:

- The bot and its pipeline **stay as-is and are the current reality.** Docs that
  describe them are correct — do **not** "reconcile" them toward a removed state.
- Do **not** delete `src/bot/claude_bridge.py`, `deploy/ict-claude-bridge.service`,
  `scripts/notify_session.py`, `scripts/ops/notify_run.sh`, `scripts/send_ping.py`,
  the `Stop` hook, or the `system-actions` transparency-notify step on the basis
  of this consideration.
- If the operator decides to proceed, that teardown is a **Tier-2 production
  change** (it touches the live deploy/notify path) requiring explicit approval
  and its own scoped plan.

**There is no canonical instruction anywhere that this must happen.** If you find
a doc asserting the Claude bot "must be removed/scrubbed" as a rule, *that* is the
bug — this file is the source of truth that the question is undecided.
