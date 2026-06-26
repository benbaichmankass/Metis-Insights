# Investigation — order packages "sizing to target_qty=0" (no_fill / reconciler artifact)

**Date:** 2026-06-26
**Trigger:** system-report `RPT-20260626-060200-since-last` flagged 10 in-window
order packages with `aggregated_target_qty: 0` + `sized_qty_by_account: {}`.
**Scope:** read-only diagnosis + an observability draft PR. Sizing/risk/order-path
is **Tier-3** — nothing here is merged without operator approval.
**Evidence pulled by Claude** via the `vm-diag-snapshot` relay (issue #4654 →
`journal?table=trades&limit=60`) + code read of `coordinator.py` / `risk.py` /
`intents.py` / `intent_multiplexer.py`.

---

## TL;DR

The two fields the report flagged are **not evidence of a sizing bug** — they are
**non-diagnostic observability artifacts**:

- `aggregated_target_qty: 0` is the **intended sentinel** ("the RiskManager decides
  qty"). The multiplexer hard-codes `target_qty_hint=0.0`
  (`intent_multiplexer.py:591`); strategies never pre-size. **By design — every
  package has this.**
- `sized_qty_by_account: {}` is **always empty in the persisted row** because the
  package meta is serialized at **creation** (`_log_new_order_package`,
  `coordinator.py:936`) — *before* the per-account sizing loop populates the dict
  (`coordinator.py:855` binds the empty dict; the loop mutates it at 1481, but the
  DB row is never re-written with the post-sizing meta). So the field designed to
  show per-account sizing is **dead in the journal for every package**, filled or
  refused.

The real signal is the **per-account rejection rows** in `trades`, which carry the
true cause. They show **four independent threads**, none of which is "the intent
layer emitted a zero target":

| Thread | What actually happened | Status |
|---|---|---|
| **(a) alpaca_live ETFs** | `zero_balance: gate_balance=0.00 USD` on **all 9** packages — alpaca_live read **$0** to size against (broken/empty creds during a brief `mode: live` window; now reverted to `dry_run`). | **Owned by the alpaca key-rotation/mode-flip session.** Diagnostic already improved by `82785fd` (today). |
| **(b) whole-unit rounding→0** | **NOT the active cause.** The same packages sized fine on `alpaca_paper`. Latent only on a <~$500–1300 real-money account; already mitigated by round-up-to-1-share. | Latent / low-risk; backlog note. |
| **(c) intent target_qty origin** | **Red herring.** `target_qty=0` is the sentinel; sizing is the RiskManager's job. | By design. |
| **(d) paper SOL +141 PnL** | `pkg-fc73141d9092479c` was an **`intent_reduce` leg** (current −382.2 → target 219.6, placed 162.5), closed `reconciler_filled` with real +141.375 realized PnL. Not a zero-sized open; an intent-layer netting/reduce artifact. | Real PnL, but a paper netting artifact (see § Paper-record buckets). |

---

## Evidence (issue #4654, trades 2847–2894, window 2026-06-24 17:13Z → 2026-06-25 22:00Z)

### Thread (a) — alpaca_live ETFs: `zero_balance`, NOT rounding

Every `alpaca_live` rejection row:

```
id=2877 alpaca_live/GLD gld_pullback_1h status=rejected ps=0
  reason: "zero_balance: gate_balance=0.00 USD (no funds available to size against)"
id=2889 alpaca_live/SPY spy_pullback_1h status=rejected ps=0
  reason: "zero_balance: gate_balance=0.00 USD (no funds available to size against)"
id=2887 alpaca_live/QQQ qqq_pullback_1h status=rejected ps=0
  reason: "zero_balance: gate_balance=0.00 USD (no funds available to size against)"
id=2893 alpaca_live/TLT tlt_pullback_1h status=rejected ps=0
  reason: "zero_balance: gate_balance=0.00 USD (no funds available to size against)"
  ... (9 total: 4×GLD, 4×QQQ/SPY, TLT)
```

`zero_balance` is emitted by `_refusal_reason` (`coordinator.py:2916`) when
`gate_balance <= 0`. `position_size` short-circuits at the same guard
(`risk.py:587`, `if gate_balance <= 0: return 0.0`) **before any whole-unit
rounding logic runs** — so on this path rounding never even executes.

**The decisive separation:** the *same packages* routed to **`alpaca_paper`**
(same symbols, same prices, same 1% `risk_pct`) sized fine — they were suppressed
only by the netting-guard / hold-policy, and one (`id=2868` GLD) opened a real
33.6-share position. If whole-unit rounding-to-zero were the cause, the equally-
small-risk paper account would round to zero on the same instruments. It did not.
→ **The alpaca_live zeros are a funding/credential problem, not a sizing-logic
problem.**

Config confirms: `alpaca_live` is currently `mode: dry_run` (reverted), but the
evidence rows carry `is_dry: false` — i.e. it was briefly flipped to `live` with
non-working keys during the window. **This is the thread owned by the separate
key-rotation session.** Today's `82785fd` (BL-20260625-ALPACA-ZB) makes an
*unreachable* Alpaca API surface as `sizing_failed: balance() returned None —
credentials missing` instead of the misleading `zero_balance` (which reads like a
reachable-but-empty account).

### Thread (b) — whole-unit rounding-to-zero (latent, NOT active)

`RiskManager.position_size` sizes alpaca in **whole shares**
(`WHOLE_UNIT_QTY_EXCHANGES={alpaca}`) and refuses sub-1 *unless* the round-up-to-1
guard fires: round up to 1 share iff `1-share risk ≤ 1.5 × (equity × risk_pct)`
(`risk.py:631-651`, operator directive 2026-06-24).

Arithmetic with the **actual** `risk_pct=0.01` and the report's per-share risks:

| Symbol | risk/share | Refuse only if equity < risk/(1.5·0.01) |
|---|---|---|
| GLD | $7.52 | **< $501** |
| SPY | $10.23 | **< $682** |
| QQQ | $19.51 | **< $1,301** |

So at any realistic real-money funding (a few $k+) the round-up fires and a
**single share is taken**, not refused. The thread-2 hypothesis (small budget ÷
large per-share risk < 1 → refuse) is real arithmetic but **only bites a
sub-$1.3k account** — and the live evidence is `gate_balance=0.00`, i.e. a *fetch*
failure, not a small-but-positive budget. **Not the active bug; low residual
risk.** Worth a one-line confirmation once alpaca_live is funded with working keys.

### Thread (c) — intent-layer target_qty origin (red herring)

- `StrategyIntent.target_qty` docstring: *"strategies emit `target_qty=0` as the
  sentinel for 'I want a long/short position, the per-account RiskManager decides
  the qty'. This is the production path — the multiplexer never pre-computes qty"*
  (`intents.py:534-538`).
- The multiplexer passes `target_qty_hint=0.0` **hard-coded**
  (`intent_multiplexer.py:591`).
- `aggregate_intents` therefore reports `max target_qty=0.0` in its
  `aggregation_reason` — that string is **displaying the sentinel**, not a fault.
- `compute_execution_delta_for_package` then uses `risk_sized_qty` as the effective
  target when `aggregated_target == 0` (`intents.py:1442-1447`).

→ The "intent already carried target_qty 0 before per-account sizing" observation
is **correct and expected**. It is not a cause of the zeros.

### Thread (d) — paper SOL `pkg-fc73141d9092479c` +141.375 (reduce artifact)

Trade `id=2894` (bybit_1 / SOLUSDT / sol_pullback_2h):

```
ps=162.5  pnl=141.375  exit=reconciler_filled  account_class=paper
notes: intent_reduce=true, intent_action="reduce",
       intent_target_qty=219.614, intent_current_qty=-382.2,
       closed_by="monitor_reconciler"
```

This package was **not** a fresh zero-sized open. The account already held a −382.2
SOL **short**; the aggregator's desired target (219.6) was *smaller* than the held
short, so `compute_execution_delta` produced a **reduce** leg (placed 162.5 units),
which the reconciler then finalized as `reconciler_filled` with a real +141.375
realized PnL. The report's `aggregated_target_qty=0 / sized_qty_by_account={}` were
again just the at-creation snapshot.

So the +141 is **real realized PnL on a paper position**, but it is an
**intent-layer reduce/netting leg**, not a clean strategy entry→exit round-trip —
exactly the class of "artifact-heavy paper record" that pollutes per-strategy
grading. Every bybit_1 paper close in the window (2847, 2854–2860, 2867, 2894) is
an `intent_reduce` / `reconciler_filled` leg, not a sized open→close pair.

---

## Proposed fixes (Tier-3 / observability — DRAFT, do not merge w/o approval)

### Fix 1 (this PR) — re-persist the post-sizing package meta (observability)

`multi_account_execute` should re-write the order-package row's `meta` **after** the
per-account sizing loop, so `sized_qty_by_account` (and the resolved per-account
state) reflects reality instead of the empty creation snapshot. Best-effort,
additive, **changes no execution decision** — it only stops the journal/report from
presenting a dead `{}` as if it were evidence. This is the change that would have
made this very investigation a one-line lookup instead of a code+diag dig.

### Fix 1b (optional, NOT in this PR) — enrich the no-fill roll-up

`no_fill_all_accounts` is a lossy package-level roll-up; the *real* cause lives in
the per-account `trades` rejection rows (`zero_balance` vs
`reentry_suppressed_netting_guard` vs `flip_suppressed_hold_policy`). Stamping the
**dominant** per-account refusal cause into the package meta (e.g.
`no_fill_detail: {alpaca_live: "zero_balance", ...}`) would let a consumer
distinguish a credential/funding refusal from a benign netting/hold no-op without a
trades join. Deferred (changes a consumed field's neighbourhood) — proposed in the
backlog.

### Thread (a) — no code change here

Owned by the alpaca key-rotation / `set-account-mode` session. The diagnostic
half is already fixed (`82785fd`). Remediation = working alpaca_live keys + the
deliberate `mode: live` flip.

### Thread (b) — no code change

Confirm-once after funding; backlog note only.

---

## Follow-up — bucketing artifact-heavy paper records (operator ask, 2026-06-26)

The bybit_1 (paper-demo) and alpaca_paper records are dominated by intent-layer
mechanics (reduce legs, netting-guard suppressions, hold-policy no-ops) and
reconciler closes — not clean entry→exit round-trips. Blending these into
per-strategy performance distorts every aggregate. Proposed taxonomy + a
reconstruction path for broker-truncated trades:

**Bucket A — gradeable round-trips.** A package that placed an `open` and reached a
genuine bracket exit (`sl`/`tp`) or a strategy `monitor()` exit. These are the only
rows that should drive per-strategy win-rate / expectancy.

**Bucket B — technical artifacts (learn-from-tech, exclude from strategy perf).**
- intent-layer `reduce` / `flip` / netting-guard / hold-policy legs
  (`intent_reduce=true`, `reentry_suppressed_*`, `flip_suppressed_*`);
- `reconciler_filled` closes with no classifiable bracket reason (genuine
  non-bracket residue — the `_classify_broker_exit → None` path);
- orphan-adopt / re-adopt flap rows (`setup_type='adopted_orphan'`,
  `reconcile_status='superseded'`);
- credential/funding refusals (`zero_balance`, `sizing_failed`).
These feed the *technical* health backlog, not the strategy scorecard.

**Bucket C — broker-truncated but reconstructable.** A trade the paper broker
closed mid-flight (reconciler/stuck-watchdog) where we *do* have entry + SL + TP.
We can keep the decision (entry quality, R:R) and **reconstruct the would-be
outcome** by replaying candles from entry forward: did price touch TP or SL first
within the bracket? → label `reconstructed_win` / `reconstructed_loss` /
`open_at_window_end`. This salvages the signal's gradeability even when the live
exit was an artifact. (`/api/bot/candles` already serves the OHLCV; a backtest-style
first-touch check is enough.)

This is a new **analysis/tooling** layer (a performance-review pre-filter +
optional reconstruction pass), **not** a live-path change.

### Built (2026-06-26)

- `src/analysis/paper_record_classifier.py` — pure stdlib classifier → bucket
  A/B/C + per-strategy split (`classify_records`).
- `src/analysis/trade_reconstruction.py` — pure first-touch SL/TP reconstruction
  (`first_touch_outcome`) + an import-lazy candle adapter (`reconstruct_record`)
  over the bot's own `fetch_candles`.
- `scripts/analysis/classify_paper_records.py` — CLI: read the journal (or a
  diag-relay trades dump via `--json`), classify, optionally `--reconstruct`,
  emit a JSON/markdown report.
- Tests: `tests/test_paper_record_classifier.py`, `tests/test_trade_reconstruction.py`.

**Validated on the real 2026-06-26 window** (issue #4654, 48 records):
**0 gradeable (A) · 46 artifact (B) · 2 reconstructable (C)** — i.e. *none* of the
per-strategy raw numbers in that window were a clean round-trip, exactly the
distortion this filter removes. The `performance-review` skill now runs this
pre-filter before computing aggregates (SKILL.md § "Bucket records before
aggregating"). Tracked as `PB-20260626-ARTIFACT-BUCKETS`.
