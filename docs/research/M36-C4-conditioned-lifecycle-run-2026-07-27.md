# M36 Track C · C4 — conditioned-lifecycle backtest RUN on 21yr history (2026-07-27)

## What this is

The **decisive C4 gate** of `M36-macro-intelligence-and-crowding-DESIGN.md`: does
the **conditioned macro/value thesis lifecycle** — C2 progress-exit ("priced-in
early → move the exit up") + optional C3 crowding conditioner, walked over the
realized price path — beat the **value-only hold-to-horizon baseline** on net
return, calibration, and max-drawdown, net-of-cost, out-of-sample? Nothing in
Track C graduates until it beats the baseline here.

**Runnable NOW on history — the "backtest history first" rule in action** (not
accrual-gated): the committed 21yr point-in-time snapshots
(`comms/macro/valuation_snapshots_backfill.jsonl`, 10,125 rows) + real daily
candles fetched off-VM (SPY/TLT/GLD/SLV/IEF, 5,091–5,423 closes each) score the
whole thing in one run. Run via the trainer-VM relay (issue #7760); scorecard
committed at `comms/macro/thesis_c4_scorecard.json`.

**Harness** (all pure + point-in-time, layer-safe):
- `src/units/strategies/macro_thesis/thesis_conditioned.py::conditioned_exit_on_path`
  — walks the injected daily path and drives the **shipped** C2
  (`thesis_progress`) + C3 (`crowding_read`/`conditioned_exit`) to decide when the
  conditioned lifecycle exits. It can only ever exit **earlier** than the baseline
  (never extends a hold), so it cannot manufacture look-ahead.
- `thesis_backtest.py::equity_and_maxdd` — the risk axis (cumulative-net-return
  equity + max drawdown).
- `scripts/macro/thesis_c4_run.py` — scores the baseline + a **full grid** over
  `expected_move_pct × {crowding on/off}`. The whole grid is reported; **no cell
  is selected in-sample**.

Config-exact with the P4 baseline: same as-of former, same rebalance cadence
(30d), same horizon (30d), same fee (0.001 round-trip), same 1,104 theses.

## Result — the numbers

**Baseline (value thesis, hold to horizon)** — reproduces the committed P4
scorecard exactly (n=1,104, mean_net +0.0018, calib_rank −0.0038):

| arm | n | win_rate | mean_net | calib_rank | maxDD |
|---|---|---|---|---|---|
| **baseline** | 1104 | 0.497 | **+0.00179** | −0.0038 | **1.3345** |

**Conditioned grid** (Δ vs baseline):

| move% | crowd | win_rate | mean_net | Δnet | calib_rank | maxDD | ΔmaxDD | mean_hold_d |
|---|---|---|---|---|---|---|---|---|
| 0.01 | off | 0.726 | 0.00129 | −0.00051 | +0.069 | 1.434 | **+0.099** | 14.3 |
| 0.01 | on | 0.768 | 0.00086 | −0.00093 | +0.061 | 1.315 | −0.020 | 12.3 |
| 0.02 | off | 0.620 | 0.00118 | −0.00062 | +0.042 | 1.260 | −0.074 | 19.5 |
| 0.02 | on | 0.674 | 0.00081 | −0.00098 | +0.063 | 1.462 | **+0.128** | 16.7 |
| 0.03 | off | 0.572 | 0.00171 | −0.00008 | +0.013 | 1.204 | −0.130 | 22.6 |
| 0.03 | on | 0.618 | 0.00144 | −0.00035 | +0.041 | 1.207 | −0.128 | 19.7 |
| 0.05 | off | 0.530 | **0.00218** | **+0.00039** | +0.021 | **1.078** | **−0.257** | 26.0 |
| 0.05 | on | 0.555 | 0.00181 | +0.00001 | +0.013 | 1.278 | −0.057 | 23.7 |

## Verdict — NULL on net edge; a modest reductive drawdown benefit only

1. **Net return: NULL — the conditioned lifecycle does NOT beat the baseline.**
   Δnet spans −0.0010 → +0.0004, all inside the noise of zero. The only positive
   cells (0.05 targets) are the *least-conditioning* ones — a 5% target with
   ~26/30-day mean hold is nearly the baseline itself. **Nothing graduates:** the
   decisive P4-style gate (beat the baseline net-of-cost OOS) is not cleared.
   This is expected — the **baseline value sleeve is itself OOS-null**
   (`M28-P4-value-gate-run-2026-07-27.md`), so there is no edge for the exit
   conditioner to amplify.

2. **Drawdown (Track C's actual reductive thesis): a real but modest win.** 5 of
   8 cells reduce maxDD vs the 1.3345 baseline; the widest-target cell (0.05/off)
   cuts maxDD ~19% (1.078) **while slightly improving net** (+0.0004), and the
   0.03 cells cut ~10% at flat net. So "move the exit up when the move is
   priced-in early" **does reduce risk without hurting return** — the reductive
   contract holds. But it is a risk reduction on a sleeve with **no edge to
   protect**, so it is not a graduation trigger on its own.

3. **Calibration: a weak positive shift.** Every conditioned cell nudges the
   conviction→net-return rank from the baseline's −0.0038 to slightly positive
   (+0.01…+0.07). Directionally the right sign (higher-conviction theses benefit
   modestly more from the progress-exit) but far below decision-grade magnitude.

4. **Win-rate inflation is mechanical, not edge.** The 0.50→0.77 win-rate jump at
   tight targets is take-profit-early converting small-positive paths into
   "wins" with unchanged expectancy — noted so it is never mistaken for a real
   improvement.

**Bottom line:** the crowding/progress conditioner is **validated as safe and
mildly risk-reducing**, but **pointless without an edge-positive base thesis**.
The blocker is not the exit lifecycle — it is the **thesis construction** (the
value sleeve doesn't predict direction OOS). The honest next step is the M28
value-thesis **construction iteration** (D1/D2/D3 per
`M28-signal-research-methodology.md`), NOT more work on the exit conditioner and
NOT any wait for accrual. Once a base thesis clears its own gate, re-run C4 —
the conditioner then has something worth conditioning.

## Disposition

- **C4 gate: NOT cleared** (net-edge NULL). Track C conditioner stays observe-only;
  **no live effect, no Tier-3 promotion.** The C1/C2/C3 pure modules + this C4
  harness are the reusable evidence trail.
- **Re-run trigger:** whenever an M28 value/thesis construction beats its own P4
  baseline, re-run `scripts/macro/thesis_c4_run.py` to test whether the
  conditioned lifecycle adds net edge on top of a base thesis that actually has
  one.
- Scorecard: `comms/macro/thesis_c4_scorecard.json`. Harness:
  `thesis_conditioned.py` + `thesis_c4_run.py` (10 unit tests in
  `tests/test_m36_thesis_conditioned.py`).
