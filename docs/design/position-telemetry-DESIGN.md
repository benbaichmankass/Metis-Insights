# Position telemetry — the missing state between entry and exit (M31)

> **Status:** DESIGN, operator-directed 2026-08-16. Nothing here is shipped.
> Phase 1 is Tier-1; phase 2 is Tier-2 (writes on the live exit loop, no order
> path); anything that *reads* telemetry to change an exit is Tier-3 and is
> explicitly out of scope for this document.
>
> **This is infrastructure, not edge.** It will not make money by itself. What
> it buys is that the exit-lever program becomes *checkable* — which is where
> M20 keeps losing weeks.

## 1. The use case, in full

A live real-money short: `xrp_pullback_2h` on `bybit_2`, trade 4163, opened
2026-07-29T12:16Z, still open ~18 days later. The operator's question was
simple — *is holding through this chop the right call?* — and answering it took
a forty-minute investigation across four diag pulls and a page of hand
arithmetic.

What the answer required, and where each piece came from:

| quantity | value | where it came from |
|---|---|---|
| entry risk | 2.523% of entry | the trade's **paper mirror** — the real-money row's stop had already ratcheted |
| ceiling (`cap_R`) | **3.92R** | hand-derived from the 9.9% TP cap |
| open profit | **+2.88R** | derived from `uPnL ÷ qty` |
| locked profit | +1.42R | derived from the ratcheted stop |
| upside remaining | 1.04R | arithmetic |
| give-back at risk | 1.46R | arithmetic |
| **reward:risk from here** | **1.04 : 1.46** | arithmetic |
| MFE (peak R) | **unknown** | *nothing records it* |

The last row is the point. **Every one of those numbers except MFE was
derivable only by hand, and MFE was not derivable at all** — despite the system
computing it, on this exact trade, roughly every thirty seconds.

## 2. The finding: the numbers exist and are discarded

`src/runtime/trail_decay.py:88–99` runs on **every exit-loop pass, for every
open position on the donchian/pullback family, declared or not.** It computes:

```python
peak    = float(lows.min())            # since-entry favourable extreme
peak_r  = (entry - peak) / risk        # ...i.e. MFE in R
bars_since_peak = (len(window) - 1) - peak_idx
```

using `risk = meta["risk_per_unit"]` — the correct, sized risk. Then:

* **declared** → collapsed to one boolean (`armed`), returns a float mult.
  `peak_r`, `bars_since_peak`, everything else: discarded.
* **undeclared** → one soak row, **only when `bars_since_peak >= 6`**, deduped
  once per package per process, into `exit_lever_soak.jsonl` — whose own
  docstring reads **"Nothing reads it back."**

`trend_donchian.py:738–746` re-derives the same `peak_r` independently for the
giveback verdict, and discards it the same way.

**This is not a missing-capability problem. It is a missing-state problem.**

## 3. What the trade exposes

| # | Gap | Evidence |
|---|---|---|
| **G1** | **No persistent position state.** Entry facts (`order_packages`), exit facts (`trades`, written at close), and transient per-pass recomputation. Nothing in between. | `peak_r` appears nowhere outside `trail_decay.py` and `trend_donchian._giveback_verdict` |
| **G2** | **Open positions are not expressed in R.** `/api/bot/positions` returns entry/SL/TP/uPnL in dollars. No `cap_R`, `open_R`, `r_to_stop`, `r_to_target`. | the whole of § 1 was hand-derived |
| **G3** | **Lever thresholds are unvalidated against the leg's own geometry.** `trail_decay_arm_r: 4.49` against a 3.92R ceiling; `gld_pullback_1d` measured **0 of 8** across its complete history. | `BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP`, PR #9588 |
| **G4** | **No time or exhaustion dimension unless a leg declares one.** `stale_exit_bars` on 3 legs; `max_hold_bars` only in the pairs sleeve. `xrp_pullback_2h` has neither — *18 days* is not a state the system can notice. | `config/strategies.yaml`, `pairs_engine.py:131` |
| **G5** | **The soaks are write-only by design.** Evidence accrues for a human Tier-3 decision; nothing closes the loop. | `exit_lever_soak.py:14` |
| **G6** | **No position-lifecycle alert kind.** Banner kinds: `trainer_down`, `account_down`, `operator_warning`, `orphan_unreconciled`, `trade_open`. | `notifications.py` — there is an alert for a trade *opening* and none for one *aging badly* |

## 4. The design — one record, many readers

**A position-telemetry record: one row per open trade, refreshed on each exit
pass, derived from the same functions the levers already use.**

```
identity     order_package_id · trade_id · strategy · symbol · account
trajectory   open_r · peak_r (MFE) · trough_r (MAE) · giveback_r
time         bars_held · hours_held · bars_since_peak
geometry     cap_r · pct_of_cap · r_to_stop · r_to_target · rr_from_here
levers       {lever: {declared, reachable, armed}}
meta         last_evaluated_at · basis · provenance
```

`rr_from_here` = `r_to_target / r_to_stop` — the single number that answered
the operator's question, and the one nothing currently computes.

### 4.1 Four constraints, each from this repo's own scar tissue

1. **Derive, do not re-derive.** Import the peak computation `trail_decay` uses;
   do not write a second one. Two probes independently re-derived *"what is the
   shadow log's `score`?"* and **both got it wrong on the same day** — the
   reason `scripts/ml/_regime_score_semantics.py` exists. `trail_decay` and
   `trend_donchian` already maintain two copies of the peak math; telemetry
   must not become the third.

2. **Bounded, and measured as such.** One small write per open trade per pass;
   the computation already happens, so the marginal cost is the write. This
   runs on the live exit loop — the June 2026 wedge class, where *every
   component was individually cheap and the sum was never watched*. It belongs
   in `tick_cost`'s `offloop_hooks` table from the first commit, not after.

3. **Never collapse a state.** `peak_r` **unmeasurable** — no `entry_time`
   anchor, or a window shorter than 2 bars, both real fail-safe paths in
   `trail_decay` today — must stay distinguishable from `peak_r = 0.0`. Same
   discipline as `exit_anchor`'s `anchored`/`deferred`/`no_anchor`; register the
   contract with `collapsed-state-guard`.

4. **Stamp provenance.** A `peak_r` from bar extremes is **ESTIMATED**, not
   MEASURED — it cannot see an intrabar excursion. Use
   `src/runtime/provenance.py`; do not invent a second vocabulary.

### 4.2 What becomes possible

* **The operator's question becomes a query.** *Rank open trades by
  `rr_from_here`.* The XRP trade surfaces itself at 1.04 : 1.46 instead of
  requiring an investigation.
* **A real position-lifecycle alert** — "past X% of its own ceiling with
  `rr_from_here < 1`", or "held > N× this leg's median hold". G6 closes.
* **Lever reachability goes live.** PR #9588's audit becomes a field rather
  than a script somebody remembers to run.
* **The exit ladder gets its input.** Rung feasibility needs `cap_R` and a live
  MFE distribution; both are in the record.
* **Backtest↔live MFE parity becomes checkable — for the first time.** This is
  the largest item. Every bug in the tp-cap family is one shape: *the harness
  measured a book production does not run.* There has never been a live
  measurement of the same quantity to check it against. The harness emits
  `mfe_r`; live would emit `mfe_r`, same definition, same leg. That converts
  this defect family from *found by accident* to *found by CI*.

## 5. Phasing and tiering

| Phase | What | Tier | Gate |
|---|---|---|---|
| **P1** | **Declare-time reachability guard** — CI fails when a leg declares an R-threshold lever unreachable under its own TP geometry. | **1** | none; makes G3 unrepeatable |
| **P2** | **The telemetry record, observe-only** — writer on the exit loop, `offloop_hooks`-instrumented, nothing reads it back. | **2** | operator OK; rollback is one env flip |
| **P3** | **Readers** — `/api/bot/positions` R-fields; a diag surface; a banner kind; the ladder input. | **1** each | P2 shipped |
| **P4** | **Backtest↔live MFE parity check** — compare harness `mfe_r` against live `peak_r` per leg. | **1** | needs P2 soak depth |
| **P5** | **Any lever that READS telemetry to change an exit.** | **3** | the same backtest gate as every other lever |

P1 is deliberately first: it is small, it is Tier-1, and it closes the gap that
produced this document.

## 6. What this design does NOT do

* **It does not change any exit.** P1–P4 are measurement and validation only.
* **It does not add a second definition of anything** — risk, peak, provenance,
  and exposure all keep their existing owning module.
* **It does not decide `arm_r`.** Every lever value stays Tier-3.
* **It is not a claim of edge.** See the header.

## 7. Open questions for the operator

1. **Storage.** A `position_telemetry` table in `trade_journal.db` (queryable,
   joins to `trades`) versus a JSONL soak (cheaper, matches every existing
   soak, but repeats the write-only pattern of G5). *Recommendation: the
   table* — the whole point is that something reads it back.
2. **Retention.** One live row per open trade, or an append-only trajectory?
   The trajectory is what makes P4 parity possible at bar resolution; the live
   row is far cheaper. *Recommendation: live row + a terminal snapshot written
   at close*, which gives P4 one honest per-trade MFE without per-bar volume.
3. **Whether P2 waits on the M20 re-sweeps** or runs alongside. They contend
   for nothing except attention.

## 8. Provenance of this document

Written 2026-08-16 from a live investigation, not from a plan. The XRP trade in
§ 1 is real and open; the code references in § 2 were read this session, not
recalled. The `gld_pullback_1d` 0-of-8 figure is entry-conditioned over that
leg's **complete** package history (relay #9599, server `count: 8`,
untruncated); the XRP 2-of-6 figure is over a **truncated, recency-biased**
sample (55,429 of 224,462 bytes) and is stated as such wherever it appears.

Related: `BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP` ·
`BL-20260816-TP-CAP-PCT-DEFAULTS-TO-LEGACY-GEOMETRY` ·
`BL-20260814-THREE-SIBLING-SWEEPS-STILL-BUILD-NO-TAKE-PROFIT-BOOKS-AND-STAMP-NOTHING` ·
PR #9588 · `docs/sprint-logs/S-M20-DISPERSION-ISOLATION-AND-QUEUE-2026-08-15.md` §§ 31–33.
