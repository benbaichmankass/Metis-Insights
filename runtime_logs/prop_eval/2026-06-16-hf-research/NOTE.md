# HF prop-pass research — two NEW candidates, IS/OOS + prop gate (2026-06-16)

Research run for `docs/research/hf-prop-strategy-research-plan-2026-06-16.md`:
find a **high-frequency, net-profitable** BTC 5m strategy that can drive a
fast + safe Breakout 1-Step pass (median days-to-pass ≤ 60 AND P(survive 6mo)
≥ 95%), solo AND combined with the clean incumbents (`fvg_range_15m`,
`squeeze_breakout_4h`). This continues the negative `ict_scalp_5m` finding in
`../2026-06-16-expanded/NOTE.md`.

> **BOTTOM LINE — clean NEGATIVE result. Neither candidate clears the bar.**
> Both displacement-continuation (family A) and VWAP mean-reversion (family B)
> are net-negative on BTC 5m at any usable frequency. A's edge is negative
> both IS and OOS. B is negative IS at the frequency the HF mandate needs
> (≥ ~1.7 trades/day → E_R −0.39 to −0.47); it only crawls marginally positive
> OOS (+0.084 R) by collapsing to **0.1 trades/day** (band_k 3σ), which is both
> below the +0.11 edge bar AND ~40× too infrequent for a *fast* prop pass, and
> is *negative in-sample* at that same config. **Recommendation: SHELVE both**
> with the backlog note below. No HF edge in these two families survives on
> BTC 5m. The fastest durable pass remains `fvg_range_15m @ 1.0` (~249-day
> median) from the base run — speed-at-survival is still not available.

Tier-1 research only. NOTHING here touches `config/strategies.yaml`,
`config/accounts.yaml`, or the live order path. The two candidate modules are
registered in `scripts/backtest_system.py::ROSTER` **for the research harness
only**.

## What was built

| Module (RESEARCH-ONLY) | Family | Idea |
|---|---|---|
| `src/units/strategies/hf_displacement_cont.py` | A — selective displacement-continuation | ict_scalp's sweep→displacement→FVG geometry PRUNED: hard 1h-EMA HTF trend-alignment gate (fails closed), London/NY killzone-only, steeper ATR-relative displacement, ATR-scaled SL/TP. Aim: lift WR 37%→≥45%. |
| `src/units/strategies/hf_vwap_revert.py` | B — VWAP/band mean-reversion | Fade ≥`band_k`-σ excursions from a rolling intraday VWAP back to the anchor, ONLY in low-ADX chop, with a wick-rejection trigger + a floored stop. Aim: ~55-60% WR at R~1.0. |

Both expose the engine's `order_package(cfg, candles_df)` + `monitor(cfg,
candles_df, open_pkg)` contract. `hf_displacement_cont` takes the same per-bar
1h-EMA HTF-bias injection as `ict_scalp_5m` (`generate_signal_stream` was
extended to special-case it).

## Method / anti-overfit discipline

- Feed `~/ict-trader-data/btc_5m.parquet` (332,624 5m bars, 2023-01-01 →
  2026-02-28). **IS = 2023-01-01 → 2025-02-01** (design/tune). **OOS =
  2025-02-01 → 2026-02-01** (held out, untouched until the config froze).
- Coarse, low-param grids on IS only; froze the **least-negative robust** cell
  per candidate (a plateau, not a single peak).
- A fast vectorized signal generator + R-replay (`scripts/research/
  hf_vectorized.py`) was used to sweep grids in seconds; it is **validated
  byte-faithful** against the canonical `order_package` per-bar scan
  (`--validate`: B 287/287 signal overlap, A 11/11) before any conclusion was
  drawn, and the frozen configs were re-verified through the **REAL engine**
  (`scripts/backtest_system.py`) for the solo numbers below.
- Solo backtests at `--risk-pct 0.5 --clock-tf 5m --flip-policy hold
  --reentry-policy suppress` (5m clock so a 5m signal fills fresh, not stale).
- The frozen configs are baked into each module's `_DEFAULTS` so the prop
  scripts (which call the engine with `overrides={}`) gate the frozen config.

## Frozen configs (IS-tuned)

- **A** `displacement_atr_mult=1.3, min_displacement_body_to_range=0.65,
  htf_filter_ema_period=50, killzone_windows="7-10,12-16", atr_sl_buffer_mult=0.25,
  tp_at_r=1.0`.
- **B** `band_k=3.0, adx_max=16, atr_stop_buffer=1.0, tp_anchor_frac=0.7,
  min_tp_r=1.0, vwap_lookback=144, band_std_lookback=144, min_stop_atr=0.75,
  min_stop_pct=0.003`.

## Candidate A — displacement-continuation: NEGATIVE (IS and OOS)

Real-engine solo, frozen config:

```
A IS  (2023-01→2025-02): bal 10000 -> 7733   net -22.67%  maxDD 22.69%
                          trades=148  WR=44.59%  exits={tp:72, sl:76}
A OOS (2025-02→2026-02): bal 10000 -> 8574   net -14.26%  maxDD 16.99%
                          trades=63   WR=49.21%  exits={tp:33, sl:30}
```

- **Clears the bar? NO.** Negative net in BOTH windows. No IS→OOS overfit
  cliff — it's just a consistently negative edge (the same structural problem
  that sank `ict_scalp_5m`).
- WR DID lift to 44.6% IS / 49.2% OOS (the plan's ≥45% target), **but it still
  loses money**, because the realized losers exceed 1R: the tight ICT stop sits
  just past the swept extreme, and the next-bar fill (engine + live both fill
  at the bar after the signal) repeatedly **gaps through** that stop, so a
  "1R" loss is bigger than 1R while a TP win is capped at exactly `tp_at_r`.
  Across the IS grid the least-negative cell was **E_R −0.31** — every cell
  lost. Raising `tp_at_r` to 1.5/2.0 lowered WR faster than it raised payoff
  (the gap-through dominates), so `tp_at_r=1.0` was the least-bad freeze.
- **Frequency: ~0.17-0.20 trades/day** (63-148 trades over a year/two) — the
  hard HTF + killzone + steep-displacement pruning that was supposed to lift WR
  also pruned frequency *far* below the 3-5/day HF target. So even if the edge
  were positive, it could not drive a *fast* pass.

## Candidate B — VWAP mean-reversion: NEGATIVE at usable frequency

Vectorized R-replay (validated faithful), per-trade R net of 7.5bps round-trip:

```
FROZEN (band_k=3.0, 3σ stretch):
  B IS  : n=67  (0.09/day)  WR=49.25%  E_R=-0.131   (NEGATIVE in-sample)
  B OOS : n=45  (0.12/day)  WR=66.67%  E_R=+0.084   (positive but BELOW +0.11 bar)

HIGHER-FREQUENCY cells (the frequency the HF mandate actually needs):
  band_k=2.0:  IS n=1282 (1.68/d) WR=45.7% E_R=-0.467 | OOS n=702 (1.92/d) WR=50.1% E_R=-0.388
  band_k=2.5:  IS n=730  (0.96/d) WR=47.1% E_R=-0.360 | OOS n=410 (1.12/d) WR=47.8% E_R=-0.346
```

- **Clears the bar? NO.** The only positive cell (frozen, band_k=3.0) is
  +0.084 R OOS — **below the +0.11 edge bar**, on only 45 OOS trades, and
  **negative on IS** (the window it was tuned on). At the ~1.7-1.9 trades/day
  the HF mandate wants, B is strongly negative both IS (−0.47) and OOS (−0.39).
  Frequency and (marginal) edge are inversely related: B only stops losing by
  becoming so selective (3σ) that it trades ~0.1×/day — useless for a *fast*
  prop pass.
- **Why it loses:** the revert-to-VWAP target produces small winners (~0.6R
  median) while a band-break stop produces full-size losers; at 45-50% WR that
  nets negative. Engine cross-check on the default (band_k=2.0, Q4-2024) solo:
  −85% / 37% WR / 105 SL vs 68 TP — consistent with the replay. A min-stop
  floor (`min_stop_atr`/`min_stop_pct`) was added because raw wick-rejection
  entries sat micro-bps from the SL (fee-dominated, gap-fragile, and they
  blow up the live fixed-fractional sizer); it tamed the tail (worst loss
  −106R → bounded) but did not create an edge.

## Prop gate — OOS (Breakout 1-Step Classic $5k), solo AND combined

`scripts/prop/montecarlo_prop.py` + `scripts/prop/evaluate_prop.py` over the
held-out year, `--clock-tf 1h --flip-policy hold` (same method as the
`../2026-06-16-expanded/` run), risk grid {0.3,0.5,0.6,0.75,1.0}, 5000
block-bootstrap paths. Solo (`hf_displacement_cont`, `hf_vwap_revert`) and
combined with the clean incumbents (`+fvg_range_15m,squeeze_breakout_4h`),
versus the incumbent pair alone as control.

Artifacts: `montecarlo.{json,md}` + `matrix.{json,md}` in this directory.

<!-- PROP_GATE_RESULTS -->
**Single-path eval matrix (`matrix.md`, OOS, $5k, risk 0.5, clock 1h):**

| Roster | Eval pass | Off-start DD (rule) | First breach | Net $ |
|---|---|---|---|---|
| `hf_vwap_revert` (solo) | ❌ target not reached | 0.0% | — | $0 (0 trades on 1h clock) |
| `hf_displacement_cont` (solo) | ❌ **max_drawdown breach** | **13.5%** (> 6% floor) | 2025-11-17 | **−$677** |
| `hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h` | ❌ **max_drawdown breach** | **14.4%** | 2025-10-27 | **−$719** |
| `hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h` | ❌ target not reached | 3.5% | — | $9 |
| `fvg_range_15m,squeeze_breakout_4h` (control) | ❌ target not reached | 3.5% | — | $9 |

- **A breaches the 6% static floor SOLO** (off-start DD 13.5%, net −$677) and
  **POISONS the clean incumbent pair** — the pair alone sits at 3.5% off-start
  DD, but adding A pushes it to 14.4% and into a max_drawdown breach (−$719).
  Identical failure mode to `ict_scalp_5m` in `../2026-06-16-expanded/`.
- **B contributes nothing** on the 1h prop clock (its 0.1-trades/day, 3σ-stretch
  config produced 0 engine trades over the OOS year) — so `B+incumbents` ==
  the incumbent pair, which itself never marks +10% in the OOS year. No help.
- **No cell passes**, let alone fast+safe.

**Monte-Carlo survival/speed (`montecarlo.md`, 5000 block-bootstrap paths/cell):**

| combo | risk | P(pass) | days→pass (med) | P(breach) | P(surv 6mo) | end ret (med) |
|---|---|---|---|---|---|---|
| `hf_displacement_cont` | 0.3 | **0%** | — | **100%** | 73.8% | −6.2% |
| `hf_displacement_cont` | 1.0 | **0%** | 141.6 | **100%** | 14.1% | −6.6% |
| `hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h` | 0.3 | **0%** | — | **100%** | 67.5% | −6.2% |
| `hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h` | 1.0 | **0%** | 118.1 | **100%** | 10.5% | −6.6% |
| `hf_vwap_revert` (all risk) | — | **0%** | — | 0% | 0% | — (0 trades) |
| `hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h` | 1.0 | 26% | 316.4 | 47% | 85.9% | −3.6% |
| `fvg_range_15m,squeeze_breakout_4h` (control) | 1.0 | 26% | 316.4 | 47% | 85.9% | −3.6% |

- **A: P(pass)=0% and P(breach)=100% at EVERY risk level**, solo and combined —
  median end-return pins to the −6.3% static-DD floor. Adding A to the clean
  pair drags P(surv 6mo) down (e.g. 0.3 risk: pair-with-A 67.5% vs pair-without
  in the control row) — A poisons it, exactly like `ict_scalp_5m`.
- **B: 0 trades on the prop clock** (3σ config too sparse) → `B+incumbents`
  is **byte-identical to the incumbent control** (same P(pass)/days/survival).
  B neither helps nor hurts because it does not trade at the prop frequency.
- **Control `fvg_range_15m,squeeze_breakout_4h`:** best cell (risk 1.0)
  P(pass) 26%, **median days-to-pass 316**, P(surv 6mo) 85.9% — still NOT
  fast+safe (needs median ≤ 60 AND P(surv 6mo) ≥ 95%), and the only durable
  cells (risk 0.3-0.5: P(surv 6mo) 100%) take 477-505 median days to pass.

**No candidate cell — solo or combined — meets `median days-to-pass ≤ 60 AND
P(survive 6mo) ≥ 95%`.** The fast+safe frontier is unchanged from the base
run; the two HF candidates do not move it.

## Verdict + recommendation

| Candidate | IS edge | OOS edge | Freq | Clears bar? | Disposition |
|---|---|---|---|---|---|
| A `hf_displacement_cont` | E_R −0.31 (net −22.7%) | net −14.3% | ~0.2/day | **NO** (negative both) | **SHELVE** |
| B `hf_vwap_revert` | E_R −0.13 (frozen) / −0.47 (HF cell) | +0.084 (frozen, sub-bar) / −0.39 (HF cell) | 0.1/day frozen; 1.7/day HF=negative | **NO** (sub-bar + wrong-frequency) | **SHELVE** |

**SHELVE both — do NOT propose for live wiring.** Neither reaches the +0.11
R/trade net edge at ~3-5 trades/day the prop math requires, and neither passes
the OOS prop gate solo or combined. This strengthens the standing conclusion:
a *fast* Breakout pass is not reachable from a BTC-5m HF strategy in the ICT
displacement or VWAP-fade families — the 5m BTC tape is too efficient for a
tight-stop continuation or a band fade to carry a positive net edge after fees
+ next-bar-fill gap-through. The durable path remains the slow `fvg_range_15m`
/ `squeeze_breakout_4h,fvg_range_15m` pass (~250-430-day median) from the base
run; "fast + safe" should be pursued via a different venue/timeframe, not more
BTC-5m HF tuning.

Backlog entry (for `docs/claude/performance-review-backlog.json`, sibling of
the ict_scalp `PB-20260616-002`):

> **PB-20260616-003** — HF prop research candidates A (`hf_displacement_cont`)
> and B (`hf_vwap_revert`) both net-negative on BTC 5m IS+OOS; shelved
> 2026-06-16. A is negative both windows (gap-through on the tight ICT stop > 1R
> defeats the WR lift). B only reaches a marginal sub-bar +0.084R OOS at 0.1
> trades/day (and is negative IS there); at HF frequency (≥1.7/day) it is
> −0.39 to −0.47R. No fast+safe prop cell solo or combined. Modules + tooling
> retained under `src/units/strategies/hf_*` + `scripts/research/hf_*` for any
> future re-test on a different venue/timeframe. Evidence: this NOTE.

## Reproduce

```bash
# faithful vectorized grid + IS/OOS R-replay (seconds)
python3 scripts/research/hf_vectorized.py --cand A --grid --start 2023-01-01 --end 2025-02-01
python3 scripts/research/hf_vectorized.py --cand B --grid --start 2023-01-01 --end 2025-02-01
python3 scripts/research/hf_vectorized.py --cand B --validate --start 2024-10-01 --end 2025-01-01

# canonical-engine solo verification of the frozen configs
python3 scripts/backtest_system.py --data ~/ict-trader-data/btc_5m.parquet \
  --start 2023-01-01 --end 2025-02-01 --roster hf_displacement_cont \
  --risk-pct 0.5 --clock-tf 5m --flip-policy hold --reentry-policy suppress

# OOS prop gate (solo + combined)
python3 scripts/prop/montecarlo_prop.py --data ~/ict-trader-data/btc_5m.parquet \
  --start 2025-02-01 --end 2026-02-01 --clock-tf 1h --flip-policy hold \
  --combos "hf_displacement_cont;hf_vwap_revert;hf_displacement_cont,fvg_range_15m,squeeze_breakout_4h;hf_vwap_revert,fvg_range_15m,squeeze_breakout_4h;fvg_range_15m,squeeze_breakout_4h"
```
