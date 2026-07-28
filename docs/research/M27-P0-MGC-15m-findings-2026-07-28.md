# M27 — MGC native-15m ict_scalp findings (2026-07-28)

> Re-target of the venue-blocked XAUUSD 15m winner onto IBKR **MGC** micro-gold
> futures (a live-tradeable venue — `ib_paper` — unlike XAUUSD's shelved OANDA).
> Executed per `M27-MGC-15m-step2-scope-2026-07-28.md` (Arm A powered re-cost +
> Arm B real-instrument cross-check). **No Tier-3 here — research verdict + a
> proposal.**

## ADDENDUM (2026-07-28 later) — deep 4-fold runner re-validation tempers this

The Arm A numbers below were a re-score of one XAUUSD k-fold. A **fresh deep
4-fold walk-forward on the same Dukascopy spot XAU (2012→now, 15m) under the
identical MGC economics** was re-run on a **free GitHub runner** (not the trainer
VM — the resource-optimized path; `research-symbol-p0-build`, issue #7845,
`derive_window: prefix:0.25`). It **replicates the ungated edge but reframes the
gate and tempers the confidence:**

| Comparator | Σ net-R | mean exp-R | n | folds +ve | prior Arm A (for compare) |
|---|---|---|---|---|---|
| baseline (ungated) | **+41.09** | +0.094 | 402 | **3/4** | +47.35 (4/4, exp +0.197) |
| conf070_fixed | +21.8 | +0.121 | 171 | 3/4 | +22.36 (4/4) |
| fitted_conf_oos | **+7.94** | +0.076 | 122 | 3/4 | +36.24 (4/4) |

Per-fold baseline: **−14.08 / +8.64 / +25.87 / +20.66 R** — fold 1 (2014–17) is a
genuine drawdown regime; the year-split's "4/4 all-positive" masked it.

**What replicates (robust):** the **ungated baseline** edge (+41 vs +47R) and
**conf070_fixed** (~+22R both runs). The unfiltered ict_scalp-on-gold edge is
real and positive across both independent splits.

**What does NOT replicate (fragile) — the key finding:** the **fitted confidence
gate** swings from +36.24R (year-split) to **+7.94R** (prefix-split) — its OOS
capture is derive-window-dependent, i.e. **over-filtering that throws away most of
the edge** (gate +7.94 vs baseline +41 on this split). **Do not ship the
fitted-confidence-gate config.** If ict_scalp-gold is ever wired, it should be the
**ungated (or conf070_fixed) config**, which both runs agree captures the edge.

**Tempering:** the deep split's exp-R (**+0.094**, ~1.9σ over 402 trades) is **half**
the year-split's +0.197, with one negative regime. So the edge is **real but modest
and regime-dependent**, not the strong clean read the +47.35R/4-4 line implied.

**Updated recommendation:** the Option-1 paper-soak-on-`ib_paper` below remains
*defensible* (the baseline edge replicates positive) **but** (a) with the **ungated
/ light-gate config, never the fitted gate**, (b) with **tempered expectations**
(+0.09 exp-R, fold-1-type drawdowns expected), and (c) the soak is **mechanics-only**
(edge already decided offline here — a handful of early losing paper trades is
variance, not a demotion signal; per the soak doctrine). **The offline gate-config
question is now answered — ungated wins — so no more offline validation is needed
before a mechanics soak; the remaining decision is the Tier-3 wire itself.** Logged
to `performance-review-backlog.json` (PB-20260728-ICTSCALP-GOLD-GATE-CONFIG).

The runner-based deep validation is itself the win the M27 IBKR-cap deadlock
needed: deep gold history is fully powerable off-VM at $0, so the "can't power MGC
15m" constraint never has to block a gold-proxy read again.

## Question

Does the `ict_scalp` gold-15m edge (validated on clean Dukascopy spot XAU:
+44.35R net, k-fold 4/4, `M27-P0-batch4-xauusd-findings`) survive **MGC's
futures economics**, and does it hold on the **real MGC instrument**?

## Answer (nuanced — the two arms disagree, and that disagreement is the finding)

**Arm A — powered re-cost (gold-15m under MGC's per-contract cost): decisive
PASS, actually stronger than the bps result.** Re-scoring the exact XAUUSD 15m
k-fold with MGC's flat per-contract model (`--fee-usd-roundtrip 3.0
--contract-value-usd 10.0`) instead of 2 bps:

| cell | folds+ | n_oos | net total R | net exp R |
|---|---|---|---|---|
| **baseline (ungated)** | **4/4** | **240** | **+47.35** | **+0.1973** |
| conf070_fixed | 4/4 | 102 | +22.36 | +0.2192 |
| fitted_conf_oos | 4/4 | 208 | +36.24 | +0.1742 |
| off_cells_5m | 4/4 | 76 | +12.68 | +0.1668 |

Per-fold baseline: **+10.19 / +15.04 / +8.12 / +14.00 R — all four positive.**
The net R is *higher* than the 2 bps XAUUSD result (+47.35 vs +44.35), confirming
the scope's expectation: a flat ~$3/contract commission on gold's wide dollar
stops is a **lighter cost in R** than 2 bps of price. The setup's edge is not
cost-fragile under MGC economics.

**Arm B — real MGC native-15m data: does NOT confirm — net-negative and
underpowered.** Running the same rig on the actual IBKR MGC 15m bars
(`m27_out_fut15/MGC`, ~1yr, the existing Jul-21 emit):

| cell | folds+ | n_oos | net total R | net exp R |
|---|---|---|---|---|
| **baseline** | **2/4** | **29** | **−0.96** | **−0.033** |
| off_cells_5m | 2/4 | 14 | −3.76 | −0.269 |
| conf070_fixed | 1/4 | 19 | −3.44 | −0.181 |

Per-fold baseline: +0.69 (n=4) / **−4.06 (n=10)** / −1.64 / +4.05. The whole OOS
set is **29 trades across 4 folds (~7/fold)** — noise, not evidence. Fold-2's
−4.06R over 10 trades (20% win) drives the negative total.

## Why the arms disagree — and why Arm B is structurally un-fixable

The disagreement is **not** "the edge fails on MGC." It is **"MGC's own data
cannot power the test":**

1. **IBKR history cap.** MGC native history reaches only **~1 year**
   (`2025-07 → 2026-07`), so a 15m walk-forward yields ~29 OOS trades total. At
   that N, a single quiet fold flips the sign. This cap is **not removable** —
   IBKR simply does not serve deep MGC intraday history, so **Arm B can never be
   powered from IBKR**, no matter how clean the pull.
2. **Flat-bar contamination.** MGC's IBKR 5m series is **50.8% flat-bar
   contaminated** (`useRTH=False` overnight thinness — `M27-P0-batch2-futures-gap-
   diagnostic`); the 15m series inherits a reduced but real share, further
   starving the FVG+sweep detector.
3. **The powered evidence is the proxy.** Gold **spot** (Dukascopy, 178k bars,
   2019–2026) and the MGC underlying are the same price to a scale-invariant
   setup. Arm A is therefore the **only statistically-powered** read of this
   edge, and it passes cleanly.

## Verdict

**HOLD as a backtest promotion — the powered proxy passes, but MGC's own
instrument data is structurally too thin to confirm, and leans negative.** Per
the scope's pre-registered decision matrix (Arm A pass + Arm B contradict →
HOLD/investigate), this is **not** a clean backtest-promotable leg.

**But the deadlock has one sensible break:** because MGC 15m **cannot** be
powered by backtest (the IBKR data cap is permanent), the only way to gather
real MGC 15m evidence is to **trade it on paper**. That is exactly what the
`ib_paper` demo venue is for.

## Proposal (Tier-3 — operator decision, not executed)

**Option 1 (recommended): promote MGC 15m to a PAPER soak on `ib_paper`.** Wire
`ict_scalp_mgc_15m` at `execution: live` routed to **`ib_paper` only** (demo /
no real money), on the strength of the powered gold-proxy (+47.35R net, 4/4).
This accrues the real MGC 15m fills the backtest structurally cannot produce,
breaking the data-cap deadlock — then a real-money decision after a genuine
soak. Same low-risk shape as the M27 crypto alt legs' `bybit_1` paper soak.
Contract facts: MGC = 10 troy oz, 1.0 pt = $10 (`contract_value_usd = 10.0`),
whole-contract sizing; venue `ib_paper` is live and reachable (already runs
`mgc_trend_1h`).

**Option 2: hold entirely** until a deeper non-IBKR MGC/gold-futures 15m source
is found (none is currently wired — Dukascopy serves spot XAU + ETF CFDs, not
CME futures).

**Not recommended: real-money MGC now** — the real-instrument evidence is
net-negative and underpowered; the proxy alone doesn't justify real capital.

## Artifacts

- Arm A k-fold: trainer `/tmp/mgc_armA.json` (from `/home/ubuntu/m27_out_xau/XAUUSD/emit.json` re-scored).
- Arm B k-fold: trainer `/tmp/mgc_armB.json` (from `/home/ubuntu/m27_out_fut15/MGC/emit.json`).
- Relay run: trainer-vm-diag issue #7806.
- Data: `m27_data_xau/XAUUSD_15m.csv` (Dukascopy spot), `m27_data_fut15/MGC_15m.csv` (IBKR).
