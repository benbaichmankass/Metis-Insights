# PROPOSAL (Tier-3, HELD): make the Alpaca close TRADE-scoped, and stop grading Alpaca protection by side

> **Doc status:** `live` · category `plan` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> ## ⚠️ THIS IS A PROPOSAL. NOTHING HERE IS APPLIED.
>
> `landing: "hold"`. No order-path file is edited by the PR carrying this
> document. MI-173 was dispatched **measurement-and-proposal-only** and did not
> close, cancel, repair or re-arm anything.
>
> Evidence: [`docs/research/alpaca-close-blast-radius-mechanism-2026-09-08.md`](../research/alpaca-close-blast-radius-mechanism-2026-09-08.md)
> Defect row: `BL-20260907-ALPACA-CLOSE-OF-ONE-TRADE-LIQUIDATES-ITS-SIBLINGS`

---

## 1. What is being proposed, in one line

Give `AlpacaClient.close` a quantity, and give the Alpaca protection sweep a
quantity — in that order, as **two separable units**, neither of which is
started before the operator answers § 5.

## 2. Why this is not #11279, and must not be merged into it

PR #11279 (MI-168) makes IB's **confirmation** trade-scoped. IB's *operation*
already is. This proposal makes Alpaca's **operation** trade-scoped; Alpaca's
*confirmation* already matches its (symbol-scoped) operation and is correct
today.

**Applying #11279's change to Alpaca would install a false SUCCESS on a
real-money-capable path** — confirming a 16-share reduction while the venue
liquidated 72. #11279's own body declines to do this and is right to.
Independently verified by MI-173.

Neither blocks the other. Both sit behind the MI-140 coupling: `alpaca_live` is
`mode: live` / `real_money` and is immune today **only** because it cannot place
an order at all. **Remediating MI-140 arms this defect on real money.**

## 3. Unit A — the operation (the actual fix)

### A0. Establish the venue capability FIRST. This gates everything else.

**Do not write code before this is answered.** The whole design turns on whether
Alpaca's `DELETE /v2/positions/{symbol}` accepts a quantity parameter. This repo
already sends one query parameter on that endpoint (`?cancel_orders=true`,
`alpaca_client.py:1012`), but **nothing in this repo has ever sent a `qty`**, and
MI-173 did **not** verify from Alpaca's documentation that one exists or that it
behaves as needed. *We did not look.*

Three outcomes, and they lead to different designs — say which was established:

| finding | design |
|---|---|
| a qty-scoped close exists and is honoured | A1 below: send it |
| no qty parameter, but a plain opposing order works | A2 below: place a reduce-sized order instead of using the liquidation endpoint |
| neither | **A3: record whole-symbol close as the intended semantics** — see § 4 |

### A1 / A2. The change

`AlpacaClient.close(self, symbol)` → `close(self, symbol, qty=None)`.

* `qty=None` preserves today's behaviour **byte-for-byte** — this is the rollback
  path and the reason the signature is widened rather than replaced.
* `execute.close_open_position`'s alpaca branch stops discarding its `qty` and
  passes it through. Its comment (*"the qty argument is informational here"*)
  and its success log (which currently prints a quantity the operation never
  used — see the memo § 1.4) are corrected in the same change, because a log
  that names the trade's qty while flattening the symbol is what would hide a
  regression.
* `_close_extended_hours` takes the same qty rather than reading the live
  position.

### A3. The pre-cancel must be scoped too, and this is NOT optional

`_cancel_open_orders_for_symbol` cancels **every** resting order on the symbol.
A trade-scoped close that still runs a symbol-wide pre-cancel is **worse than
today**: it would reduce 16 shares and leave the sibling's 56 unprotected, with
no flatten following to tidy up. Today's version at least destroys the position
it stripped the protection from.

So Unit A is only safe if the pre-cancel is narrowed to the closing trade's own
legs at the same time. The repo has the material: `trades.sl_order_id` /
`tp_order_id` already carry per-trade leg ids on other venues, and
`close_open_position` already accepts both and forwards them to Bybit. **On the
Alpaca rows measured this session both are `NULL`** (ids 5266 and 5414), so this
sub-unit needs a writer before it needs a canceller — which is why it is called
out here rather than assumed.

⚠️ **Consequence to state rather than bury:** while `sl_order_id`/`tp_order_id`
are NULL on Alpaca rows, a trade-scoped close **cannot** identify which legs are
its own, and A3 is not implementable. **Unit A is therefore blocked on the leg-id
writer, not merely on operator approval.** Shipping A1/A2 without A3 would be a
net regression.

### A4. Confirmation must move WITH the operation, never before it

Once the operation is trade-scoped, the existing symbol-flatness confirm becomes
wrong in the direction #11279 fixed for IB: it can never be satisfied while a
sibling holds shares. At that point — **and only then** — Alpaca wants the same
trade-scoped confirmation #11279 proposes, and the `IB_CLOSE_CONFIRM_PARTIAL`
`strict` / `reduction` question (§ 5) applies to it identically.

**Order is the safety property.** Operation first, then confirmation. Reversed,
the intermediate state is a false success on a real-money-capable path.

## 4. If the operator rules whole-symbol close is intended

This is a legitimate outcome and the backlog row already anticipates it. In that
case the defect **moves rather than disappearing**: `close_open_position` accepts
a per-trade `qty`, validates it (`if qty <= 0`), and silently discards it. That
must be recorded as a **decision**, not left looking like a bug nobody fixed, and
the minimum honest change is then:

* stop accepting a parameter that is not used, or log it as discarded;
* stop printing `qty=<per-trade>` on a whole-symbol flatten;
* make the monitor **refuse** to close a row on a symbol holding other open rows,
  rather than close it and orphan them.

## 5. Operator decisions this proposal needs

**1. Does Unit A proceed at all, or is whole-symbol close the intended Alpaca
semantics?** (§ 3 vs § 4.) MI-173 has no view on which the strategies want; both
are coherent and they need different code.

**2. Ordering against MI-140.** `alpaca_live` is real money and immune only
because it cannot order. Should MI-140's remediation be held until Unit A lands,
as #11279 asks for its own coupling? MI-173 recommends **yes**, on the same
asymmetry #11279 argues: an over-close liquidates a position nobody journalled
closed, and no existing machinery looks for that.

**3. If Unit A proceeds, `strict` or `reduction` for A4's confirmation.** This is
the same question #11279 puts, and answering it there should answer it here —
**but say so explicitly**, because a default drifting across two venues is where
a decision goes missing.

## 6. Unit B — the detector (separable, smaller, and not order-path)

Independent of every decision above, and worth landing on its own merits:
**Alpaca protective coverage is graded by SIDE, not by QUANTITY**, so the live
TLT position — 16 of 72 shares covered — grades **fully protected** and the 56
naked shares are invisible to `_check_broker_naked_equity_positions`.

Bybit grades `covered_qty`; IB grades `stop_qty`/`target_qty`; Alpaca grades
neither. The data is already in hand — `/api/diag/alpaca_open_orders` returns a
`qty` on every leg — so this is arithmetic on a read that already happens, with
no new broker call.

⚠️ **Detection only. Do NOT wire it to a re-arm in the same change.** The one
time this class was auto-remediated, the repair cancelled the leg that matched
the journal (`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`).
And a partial-coverage re-arm on Alpaca would have to decide *which* trade the
uncovered shares belong to, which is the same attribution problem Unit A exists
to solve. Grade it, page it, stop.

Unit B is **Tier-2 at most** (an observability path, no order path) and does not
depend on Units A or the MI-140 ordering. It is the cheapest thing on this page
and the only one that improves the situation while every other question is open.

## 7. What this proposal deliberately does not do

* It does not touch Bybit or IB.
* It does not re-scope any confirmation before its operation.
* It does not propose closing, cancelling or repairing the live TLT rows. **The
  live 16+56-vs-72 shape is reported, not acted on** — closing either row is
  precisely the operation whose blast radius this document exists to establish.
