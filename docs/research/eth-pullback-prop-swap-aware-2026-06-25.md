# eth_pullback_2h on Breakout — swap-aware funded EV + a swap-robust variant

_2026-06-25. Tier-1 research (no live-path change). Driver: the
`validate_alt_prop` gate (`scripts/prop/validate_alt_prop.py`) + the system
engine (`scripts/backtest_system.py`) run on real ETHUSDT 5m candles
(2021-03-15 → 2026-06-18) against `config/prop_rulesets/breakout.yaml`. Trainer
relay runs #4514 / #4522 / #4528 / #4530 / #4532._

> **CORRECTION (2026-06-25, same day):** the first pass of this note used an
> assumed **0.09%/day** swap from a third-party review. Breakout's **own help
> centre** confirms the real holding cost is **0.033%/day (0.00033)** — the
> 0.09% figure is outdated. Every number below is now at the **real 0.033%/day**;
> the 0.09% column is retained only to show the sensitivity. The corrected cost
> changes the conclusion materially: the strategy is **solidly +EV on Breakout**,
> not marginal.

## TL;DR

- `eth_pullback_2h` is a *let-winners-run* trend-follower (`trail_mult: 5.0`,
  `tp_r: 50` sentinel — no real TP) that holds for days-to-weeks. On a daily-swap
  venue, hold time is the cost driver, so a tighter-exit variant was tested.
- **At Breakout's real 0.033%/day swap, both the live exits and the swap-robust
  variant are +EV and pass the funded-EV gate 4/4 walk-forward folds.**
- The **swap-robust variant** — `tp_r: 6.0`, `trail_mult: 3.5` (everything else
  == live) — is the better config: higher realised PnL (**+$421 vs +$166** over
  5y), half the swap drag (**32% vs 57%**), more consistent per-fold realised,
  and a **3.1× safety margin** against swap-rate error (break-even 0.10%/day vs
  the real 0.033%).
- **Recommendation:** the variant `eth_pullback_prop_2h` is a **strong live
  candidate** on `breakout_1` (Tier-3, operator-gated). It is currently committed
  as `execution: shadow`; promoting it to `live` is the operator's call. The live
  `eth_pullback_2h` exits would also be viable, but the variant is preferred for
  its larger safety margin and lower drag.

## The swap rate (corrected)

| source | rate | basis | confidence |
|---|---|---|---|
| **Breakout help centre (authoritative)** | **0.033%/day (0.00033)** | notional, per open position per day, symmetric long/short | **high** |
| proptradingvibes review (used in v1) | 0.09%/day | — | outdated; superseded |

Sources: Breakout FAQ — ["What are the trading fees and commissions"](https://intercom.help/breakoutprop/en/articles/11647195-what-are-the-trading-fees-and-commissions-in-a-funded-account)
("a daily swap fee of 0.033% per open swap position"); ["Are the rules the same
between the Breakout Prop terminal and the DXTrade terminal"](https://intercom.help/breakoutprop/en/articles/14211064-are-the-rules-the-same-between-the-breakout-prop-terminal-and-the-dxtrade-terminal)
(0.033%/day; DXTrade debits once/day at 00:25 UTC on positions open at 00:00
UTC; Breakout terminal debits ~0.0055% every 4h). Commission is **0.04%/side =
0.08% round-trip**, ≈ the harness's 7.5 bps assumption, so commissions are
already modelled. **DXTrade nuance:** only positions open at 00:00 UTC are
swapped, so an intraday trade that never crosses midnight pays no swap on
DXTrade — our `swap × notional × hold_days` model is therefore correct for the
multi-day holds this strategy takes, and at worst slightly conservative.

## Funded EV @ the real 0.033%/day (1.5% risk, breakout.yaml)

| config | trades | realised pre-swap | realised **post-swap** | swap drag | 12-mo EV | P(net>0) | ROI/fees | WF folds + |
|---|---|---|---|---|---|---|---|---|
| **variant** tp_r 6 / trail 3.5 | 344 | +$618 | **+$421** | 32% | +$603 | 75.9% | 4.20 | **4/4** |
| live exits tp_r 50 / trail 5.0 | 300 | +$384 | **+$166** | 57% | +$641 | 77.2% | 3.69 | **4/4** |

### Walk-forward (4 sequential OOS folds), realised post-swap / 12-mo EV

| fold | window | variant post | variant EV | live post | live EV |
|---|---|---|---|---|---|
| 1 | 2021-03 → 2022-07 | −$37 | +$297 | −$31 | +$360 |
| 2 | 2022-07 → 2023-10 | +$218 | +$783 | +$24 | +$628 |
| 3 | 2023-10 → 2025-02 | +$32 | +$535 | **−$192** | +$361 |
| 4 | 2025-02 → 2026-06 | +$284 | +$910 | +$532 | +$1,312 |

Both are EV-positive every fold. The variant's *realised* is positive in 3/4
folds (only fold 1 slightly red); the live exits' realised is lumpier (fold 3
−$192, carried by fold 4's fat +$532 tail). The variant's higher break-even rate
and steadier folds are why it's preferred even though the live exits show a
marginally higher headline EV (that EV leans on fold 4's right tail).

## Swap-rate sensitivity (the robustness backbone)

Realised break-even swap rate (where post-swap PnL crosses zero):

| config | break-even swap | vs real 0.033%/day |
|---|---|---|
| **variant** tp6/tr3.5 | **0.1035%/day** | **3.1× headroom** |
| live exits tp50/tr5.0 | 0.058%/day | 1.75× headroom |

So even if Breakout's real swap were double the documented figure, the variant
stays realised-positive. EV12 degrades gracefully across the whole 0 → 0.18%/day
range (variant EV12 stays +$469–$650; never crosses zero), because the prop
account economics dominate — but realised PnL is the honest floor, and the
break-even headroom is the number that matters for cost-model risk.

## Decision & routing

- The **swap-robust variant** `eth_pullback_prop_2h` (tp_r 6 / trail 3.5) is
  committed to `breakout_1` as `execution: shadow` (observe-only). At the real
  cost it is a **strong live candidate**: +$421 realised, 4/4 folds, 3.1× swap
  headroom, 32% drag, ROI/fees 4.2. **Recommend promoting shadow → live**
  (operator-gated Tier-3); the soak can run in parallel as live-faithful
  confirmation.
- `eth_pullback_2h` (the live let-winners-run exits) is **also viable on
  Breakout** at the real cost (+$166 realised, 4/4 folds) — the earlier "keep it
  off breakout" call was an artefact of the 2.7×-too-high swap assumption. It
  remains unchanged on bybit_1 (paper) / bybit_2 (real) regardless; the variant
  is the cleaner prop vehicle.
- A wider exit than tp6/tr3.5 might capture more now that swap is cheaper than
  first assumed — a future tuning note, not chased here (the variant is already
  strong and over-tightening was shown to hurt).

## Reproduce

Research-harness registration only (NOT a config/order-path change): register
the strategy name into `scripts/backtest_system.ROSTER` reusing the shared
`htf_pullback_trend_2h` unit, pin eth_pullback_2h's live base params (the unit
defaults are the 50/0.33/3.0 *scaffold*, not the live 40/0.5), apply the swept
exits via `overrides`, then `apply_funding_to_ledger(swap_rate_daily=0.00033)` →
`run_ev_montecarlo` vs `breakout.yaml` @ 1.5%. Full drivers in the relay issues
above (#4532 is the real-rate gate; #4530 the sensitivity sweep).
