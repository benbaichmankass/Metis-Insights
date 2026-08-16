# Sprint Log: S-SYSREV-EXIT-COVERAGE-AND-CANCEL-2026-08-16

## Date Range

2026-08-16 (single session, continued through one context compaction).

## Objective

Started as a `/system-review` session. Became operator-escalated P0 work when a
routine order-state read surfaced that **both live `ib_paper` positions were
target-naked** — a stop at the broker, no take-profit, and zero limit orders on
the whole account. Operator directive, verbatim: *"There's no such or should
never be any trades that don't have to take profits… we're completely negligent
regarding the brackets… Nothing is done until that is fixed."*

A second operator directive ran in parallel: the absence of a per-order IB
cancel is *"a huge gap in capabilities that shouldn't exist and needs to be
rectified immediately."*

## Tier

Tier-1 (tooling, tests, docs, read surfaces) + **Tier-2 executed with operator
authorization on record**: one `cancel-ib-order` APPLY against live `ib_paper`
order 6. No Tier-3 change. No `config/` change. One comment-only edit to
`src/runtime/order_monitor.py` (AST-verified identical with docstrings
stripped).

## Starting Context

- `BL-20260816-IB-CLOSE-ABANDONS-ITS-OWN-ORDER` open, with a stranded `ib_paper`
  MGC order 6 (`MKT SELL 105`, tif DAY) that no existing path could cancel.
- A prior session had recorded a proposed fix — "set a gateway Master API client
  id" — which this session was asked to verify.

## Repo State Checked

`main` at session start; three PRs merged during the session (#9700, #9723, and
the merges feeding them). `claude/attach-ib-target` (#9717) left green and
**draft, deliberately unmerged** — see Deferred.

## Files and Systems Inspected

- `src/units/accounts/ib_client.py` — `_locked_cancel`, `_locked_protection_coverage`,
  `_locked_list_open_orders`, `has_protective_orders`.
- `src/runtime/order_monitor.py` — `_check_broker_naked_ib_positions`,
  `_check_broker_naked_equity_positions`, `_bybit_position_protection`,
  `_PENDING_CLOSE_RETRY_COOLDOWN`.
- `src/units/accounts/alpaca_client.py` — `has_protective_orders`,
  `_open_orders_for_symbol`.
- `scripts/ops/bybit_bracket_audit.py`, `.github/workflows/system-actions.yml`,
  `tests/ops/test_system_actions_workflow.py`.
- Live state via `/api/diag/ib_open_orders`, `/api/diag/exchange_positions`, and
  the `bybit-bracket-audit` Tier-1 action.

## Work Completed

**1. The IB protection grade was ONE-SIDED — fixed** (`BL-20260816-COVERAGE-IS-ONE-SIDED`).
`protection_coverage` classified legs with a single membership test
(`"STP" in t or "LMT" in t or "TRAIL" in t`), so a stop and a take-profit were
interchangeable and a stop-only position reported fully covered. Split via a
shared `_protective_leg_side`, returning `stop_qty` / `target_qty` beside the
back-compat `covered_qty`; the sweep gained a `target_naked` counter that
**alerts without re-arming** (a missing stop is a safety gap to close blind; a
missing take-profit is decision-time geometry a repair must read, not invent).
The classifier tests the stop family FIRST because `"STP LMT"` contains `"LMT"`
— a naive LMT-first test would *manufacture* target coverage, strictly worse
than the bug being fixed. Pinned by `tests/test_ib_protection_two_sided.py`.

**2. Per-order IB cancel — built, and the recorded premise refuted.**
`scripts/ops/cancel_ib_order.py` + the `cancel-ib-order` action. Verified from
the TWS API docs that an order is bound to its submitting clientId, so the only
per-order path is to connect AS the owner. **The prior session's "Master API
client id" fix would not have worked** — that role is documented as
*visibility* (order-status callbacks), with no cancellation authority anywhere
in the API reference. Correction recorded on the originating backlog row.

**3. A workflow wiring gap, and the guard generalised.** `cancel-ib-order`'s
first dispatch died with `ACCOUNT_ID: ACCOUNT_ID required` — allowlisted,
tier-classified, script-mapped, validated, registered, documented, **353 guards
green**, and no branch ever forwarded the parameter. Fixed, then generalised:
the pre-existing guard for this class hardcoded one variable name (`ENV_KEY`,
from the get-env incident) and so could not see the same bug one name to the
left. The replacement asks the **wrapper** what it requires via `${VAR:?…}` and
asserts the workflow forwards it — future actions covered on arrival, no test
edit. Carries a positive control; reproduces the live failure from static
analysis alone.

**4. `attach-ib-target`** — places the DECLARED `trades.take_profit_1` INTO the
stop's existing OCA group and cancels nothing, so the stop stays armed
throughout and IBKR cancels it when the target fills. Four refusals, each a
hazard measured on this account. **Not merged** (see Deferred).

**5. Live action executed.** `cancel-ib-order` APPLY on order 6 — see
Contradictions/Drift for what happened.

## Validation Performed

- `tests/ops/test_system_actions_workflow.py` 354 pass; the new guard verified
  **non-vacuously** (resolves `attach-ib-target` → `{ACCOUNT_ID, ACTION_SYMBOL}`,
  observes both forwarded) and verified to FAIL on the pre-fix workflow with
  exactly `{'cancel-ib-order': ['ACCOUNT_ID']}`.
- The docstring edit in `order_monitor.py` proven comment-only by AST
  comparison with docstrings stripped, not by reading the diff.
- Live: `bybit-bracket-audit` (run 31953868225) and three `ib_open_orders`
  reads, each confirmed `read_state: orders_read` before being believed.

## Documentation Updated

- `CLAUDE.md` — two corrections (below) + the two-sided coverage contract.
- `src/runtime/order_monitor.py` docstring — accessor + return shape.
- `docs/claude/system-actions.md` — `cancel-ib-order`, `attach-ib-target`.
- `docs/claude/health-review-backlog.json` — five new rows, one corrected.

## Contradictions or Drift Found

**A. `CLAUDE.md` named the wrong accessor, in two places, and contradicted
itself.** Both the `IB_BROKER_NAKED_CHECK_SECONDS` row and the naked-protect
bullet said the IB sweep calls `IBClient.has_protective_orders`; the body calls
`protection_coverage`. The `/api/diag/ib_open_orders` row **in the same file**
already drew the distinction correctly. Not pedantic: the boolean answers
`True` for a stop-only book, which is the reading that let this incident sit.
Fixed both.

**B. The sweep's own docstring** named `has_protective_orders` and documented a
five-key return while the function returns ten — omitting `target_naked`, the
one it exists to surface. Fixed.

**C. `cancel-ib-order` APPLY: IB ACCEPTED the cancel and the order did not go
away.** `retCode 0 "OK"`, `orders_on_account` 5 → 5, order 6
`PreSubmitted` → `PendingCancel`. The script refused to call that a success —
verified the post-state, reported `cancel_not_effective` / `still_present`,
exit 1. It **reproduces**: order 378 has been in that state since ~12:33Z.
Filed as `BL-20260816-IB-CANCEL-ACCEPTED-BUT-NEVER-COMPLETES-VENUE-CLOSED`
with the venue-closed explanation recorded explicitly **as a hypothesis** —
two observations and plausible timing, not a confirmed mechanism.

**D. Three of my own errors, all caught before they landed as fact.** (i) The
missing env forwarding was mine. (ii) I filed that Bybit target-nakedness was
"unmeasurable"; `impossibility-claim-guard` rejected the row for asserting an
impossibility with no `checked:` annotation — `bybit_bracket_audit.py` already
existed and already collected `tp_legs`. (iii) I ran that guard locally
pre-commit and read the pass as evidence; it diffs **committed** content, so
the probe was invalid.

## Risks and Follow-Ups

- **`ib_paper` remains target-naked.** Both positions. Not fixed this session.
- Orders 6 and 378 hold unhonoured cancels and are DAY market sells that
  activate at the COMEX reopen — a stray filling after anything else flattens
  the long opens a **naked 105-contract short**.
- MES: 30 contracts of stop against a 15 long in two **disjoint** OCA groups
  (`BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS`).
- Alpaca: 13 live positions whose target side nothing can measure
  (`BL-20260816-TARGET-NAKEDNESS-UNDETECTABLE-ON-ALPACA-AND-BYBIT`).
- `bybit_portfolio` ETHUSDT: 13.96 of unbacked journal qty, dead stop leg
  (`BL-20260816-BYBIT-PORTFOLIO-ETH-DEAD-LEG-UNBACKED-JOURNAL-QTY`).
- MGC 4487's declared TP is operator-approved and **blocked** on the strays.

## Deferred Items

- **#9717 left green and draft on purpose.** Its own guard refuses to attach a
  target while a non-protective order rests on the symbol, which is exactly the
  live state — merging it changes nothing until the strays clear, and the
  refusal must not be relaxed to get a target on.
- Bybit measured clean (5/5 symbols carry a TP leg), so its detector gap is
  latent, not live exposure — severity lowered rather than closed.

## Next Recommended Sprint

Observe the COMEX reopen (Sunday 22:00Z) and record which of three happened to
orders 6 and 378 — cancelled (hypothesis confirmed), still `PendingCancel`
(refuted, escalate), or FILLED (check for a naked short first). That
observation gates the target attach, and therefore the operator's P0.

## Wrap-Up Check

Docs reconciled via `/doc-freshness` (this log is one of its outputs — the
session had no sprint log or ROADMAP record until the sweep found the gap).
`canonical-doc-coherence` passes. Backlog rows carry resolution criteria;
`backlog-criteria`, `claim-basis`, `impossibility-claim` and `json-notes-cap`
guards all pass.
