# Strategy Loss-Driver Report — S-STRAT-IMPROVE-S2 (2026-05-23)

> **Sprint:** S-STRAT-IMPROVE-S2 (Strategy Improvement Program, Tier-1).
> **Status:** Evidence base for S3 (selectivity) and S4 (exit logic).
> **Method:** live, read-only diag + audit relays (autonomous per the
> AUTONOMY MANDATE). No code/config changed.
> **Caveat:** all numbers are point-in-time live pulls; re-run the relays
> before acting on stale figures.

## Evidence sources (live pulls, 2026-05-23 ~07:34 UTC)

| What | Relay | Issue | Workflow run |
|---|---|---|---|
| Live VM state snapshot | `vm-diag-snapshot` | #1779 | actions/runs/26327050769 |
| bybit_2 (real money) audit | `strategy-performance-audit` | #1780 | actions/runs/26327051057 |
| bybit_1 (demo) audit | `strategy-performance-audit` | #1781 | actions/runs/26327051441 |
| bybit_2 reconciler-close classification | `monitor-miss-analysis` | #1782 | actions/runs/26327530047 |

Live VM: `git_sha 461bcb0` (**= `main` HEAD**), heartbeat `running`
(age 52s), `bot_uptime_s 5837` (~1.6h since restart), `last_tick`
07:32:58Z, `ib_paper=true`, `ib_live=false`. Recent trade mix in the
snapshot: 84 BTCUSDT + 27 MES → **MES is actively trading** since the
2026-05-22 go-live.

## SL_STD_MULT live-vs-repo flag (S0) — RESOLVED

S0 flagged that `vwap.py:224` carries `SL_STD_MULT_DEFAULT = 0.3` with a
`# TIER-3: Ben must approve before this value is deployed to the live
bot` note, while S-TRAINER-BT-1 had deployed `0.5` on 2026-05-17.

**Resolved: `0.3` is LIVE.** Three independent confirmations:
1. Live VM SHA `461bcb0` == `main` HEAD, which carries `0.3`.
2. Trader restarted ~1.6h ago, so the running process loaded current code.
3. **Empirical R:R on real-money trades = 3.48** (issue #1780), which
   matches `ENTRY/SL = 1.0/0.3 = 3.33`, not `1.0/0.5 = 2.0`.

**Governance flag (Tier-3, for the operator — not actioned here):** the
`0.3` value is running live despite the in-code "must approve before
deploy" note. Either it was approved and the comment is stale (S0 also
flagged the stale R:R worked-example at `vwap.py:200-208` that still
says `0.5σ → 1:2`), or it reached live via `ict-git-sync` auto-pull +
restart without an explicit deploy approval. This needs an operator
ruling. **No change made** — flagged for S6 packaging / operator
confirmation.

## Per-strategy × symbol × account metrics

| Account | Strategy | Symbol | Money | N (7d) | WR | Net | Expectancy | Notes |
|---|---|---|---|---|---|---|---|---|
| bybit_2 | vwap | BTCUSDT | **real** | 167 | 25.1% | **−$35.82** | −0.21 | full breakdown below |
| bybit_1 | vwap | BTCUSDT | demo | 17 | 5.9% | −$1981* | −116* | *large notional, $ not comparable; long 0/14 |
| bybit_1 | ict_scalp_5m | BTCUSDT | demo | 1 | 100% | +$0.55 | — | **low-N** (1 trade) |
| bybit_1 | turtle_soup | BTCUSDT | demo | 0 | — | — | — | **no closed trades in 7d** |
| ib_paper | (all 3) | MES | paper | ~27 seen | — | — | — | **low-N**, ~1 day live; no per-strategy/pnl pull yet |

**Read this table with care:** vwap is the only strategy with a
statistically meaningful sample. turtle_soup and ict_scalp_5m are
**low-frequency** — near-zero closed trades in the 7-day Bybit window —
so no edge claim can be made about them yet. MES is ~1 day old.

### bybit_2 vwap (real money) — the meaningful sample

- **Overall:** 167 trades, 42W/125L, 25.1% WR, net **−$35.82**,
  mean_win +0.546, mean_loss −0.47, expectancy −0.21.
- **By direction:** LONG 110 @ **20.0% WR** (−$28.23, exp −0.257);
  SHORT 57 @ 35.1% WR (−$7.59, exp −0.133). Longs = 66% of volume and
  **79% of the net loss**.
- **By exit_reason:**
  | exit_reason | N | %trades | WR | net | exp |
  |---|---|---|---|---|---|
  | `reconciler_filled` | 123 | 74% | 17.9% | **−$34.96** | −0.284 |
  | `sl_cross` | 17 | 10% | 0.0% | −$8.63 | −0.508 |
  | `vwap_cross` | 23 | 14% | 69.6% | **+$5.84** | +0.254 |
  | `tp_cross` | 4 | 2% | 100% | +$1.93 | +0.482 |
- **R:R geometry:** mean TP dist 359.35, mean SL dist 112.51, **R:R
  3.48**, breakeven WR 22.3%, observed 25.1% (gross-of-fees positive).
- **Fees:** $47.07 paid; **gross PnL +$11.25**; net −$35.82 → **fee drag
  = 418% of gross**.
- **Slippage:** entry −3.05 bps mean (favorable), exit ~0. Not a driver.
- **Worst hours (UTC):** 23 (9.1% WR), 20 (12.5%), 22/06 (14.3%), 10
  (15.4%). Best: 21 (75%), 02 (75%), 16/08 (66.7%). The dead hours
  (~22–06 UTC + 09–11 UTC) bleed; a session filter is a candidate.

## Ranked loss drivers (vwap / BTCUSDT — the evidence-backed case)

1. **Overtrading → fee drag (DOMINANT).** The strategy has a *thin
   positive gross edge* (+$11.25/7d) that fees (−$47.07) bury 4×. At
   ~24 trades/day, fees are 418% of gross on bybit_2 and 480% on
   bybit_1. **This is the single biggest lever: fewer, higher-quality
   entries.** Selectivity (S3) directly attacks this.
2. **Exits are WORKING AS DESIGNED — not a bug** (confirmed
   2026-05-23 via `monitor-miss-analysis`, #1782). Of the 125
   `reconciler_filled` closes: **36 TP_hit + 84 SL_hit + only 5
   between_TP_SL** → **96% are native Bybit SL/TP bracket fires**
   (`execute.py` submits `stopLoss`/`takeProfit` with each entry; the
   exchange closes server-side between the bot's 60s ticks and the
   reconciler correctly records it). Only 5 trades (−$0.44, negligible)
   closed anywhere unexpected. **The technical-first question is
   answered: no monitor bug, no need for a faster monitor tape.** The
   losses are genuine strategy losses — the stop is hit 84× vs the TP
   36× (mean_R −0.788 vs +1.712). One subtle, legitimate enhancement
   for S5 (exit geometry, Tier-3): because the native stop usually
   fires within the 60s window, the bot's profitable `vwap_cross`
   exit (69.6% WR, S2) rarely gets to compete — a wider/time-boxed
   stop or a faster managed-exit cadence could convert some SL-runs
   into thesis-completions, but that is an exit-design change, not a
   bug fix.
3. **Long-side bias — but read it as REGIME, not a permanent edge
   (operator directive 2026-05-23).** Longs 20% WR vs shorts 35% WR;
   longs are 79% of the loss (monitor-miss confirms: longs 71 SL / 20
   TP vs shorts 13 SL / 16 TP). The live window has been a **down
   market**, so mean-reversion *buys* get run — this is expected in a
   downtrend and will likely flip in an uptrend. **Do NOT bake in a
   static short-bias / long-suppression**: any direction handling must
   be **regime-robust** (e.g. an HTF-trend-aware gate that
   symmetrically suppresses *counter-trend* fades in either direction),
   validated across up AND down market windows — never a fit to the
   current regime. (Corroborated on bybit_1: vwap long 0/14, same
   window.)
4. **Tight SL (0.3σ) trades WR for R:R.** R:R 3.48 means few winners
   pay for many small losers; observed WR (25%) barely clears breakeven
   (22%) *gross*, so the geometry is roughly sound but leaves no margin
   for fees. Re-examining `ENTRY/SL` jointly (with the long/short split
   the prior sprint asked for) is an S3/S4 backtest.

## Gaps / low-confidence areas (carry to S2-B)

- **turtle_soup & ict_scalp_5m have no audit-grade sample.** 0 and 1
  closed Bybit trades respectively in 7d. Need either a longer window
  (blocked: demo only live since 2026-05-18) or a journal-based pull
  (`/api/diag/journal?table=trades`) filtered by strategy across full
  history, accepting the journal's own pnl column (clean post-#1432).
- **MES (ib_paper) not yet broken down.** ~27 recent trades exist but
  the snapshot trade objects don't carry `strategy_name`/`pnl`, and the
  Bybit-only audit can't touch IB. Needs a journal pull + ~1–2 weeks of
  accumulation before any read is meaningful.
- **`deviation_std` not captured in trade notes** — the audit's
  signal-stretch bucket is blank. Capturing it (Tier-2 instrumentation
  in the signal writer) would let S3 test whether deeper stretches are
  higher-quality entries. Filed as an instrumentation follow-up.

## Handoff (re-planned 2026-05-23 per operator: technical-first)

The operator's technical-first check is **complete and clean** (driver
#2 above): the reconciler dominance is working-as-designed native
bracket fires, not a bug. So the program proceeds to strategy
improvement, with the regime caveat (driver #3) as a hard constraint.

Re-numbered sprint sequence (this report closes the diagnostic step):
- **S3 (this report's diagnostic half) — DONE:** exit-mechanism
  diagnosis → no bug.
- **S4 — selectivity / rule-tightening** (Tier-1 analysis; Tier-3 to
  ship). Highest-ROI lever against the dominant fee-drag driver.
  Concretely: add the **long/short split** to the backtest aggregate
  (the S-VWAP-POLICY-INVESTIGATION follow-up); test session gating
  (the dead 22–06 / 09–11 UTC hours), entry-threshold raises, and a
  **regime-robust** (not static-short) counter-trend gate — measured
  by *net-of-fee* expectancy + trade-count reduction, validated across
  up AND down windows.
- **S5 — exit geometry** (Tier-3 to ship): test whether a
  wider/time-boxed stop or a faster managed-exit cadence converts
  native SL-runs into `vwap_cross` thesis-completions (69.6% WR)
  without re-inflating fees.
- **S6 — validate winners on strongest/weakest symbols; S7 — package
  for approval.**

All recommendations above are **analysis only**. Every live change is
Tier-3 and stops at the operator-approval gate (comms path verified in
S1).
