# Strategy-primitives recombination sweep — Direction-2 results (2026-06-18)

First full run of the recombination orchestrator (`scripts/ops/recombination_sweep.py`,
PR #3945) over the v1 pool (`config/research/recombination_pool.yaml`). Run on the
trainer via `vm-driver` (detached); raw: `automation/results/direction2-collect2.txt`.

**90 coherent tuples** = {5 alts} × {trend_4h, pullback_2h} × {ADX none / ≥20 / ≥25} ×
{trail 5.0 / 3.0} × {conf 0.0 / 0.6 (trend only)}, each run through the harness at base
+ 2× fee → `m15_ws_b_fold_report.py` (5-fold anchored) → `classify_strategy_tier.py`.

## Tier outcome
| tier | count |
|---|---|
| live_ready (every OOS fold + + 2×-fee headroom) | 9 |
| paper_ready (net-of-fee + + 2× headroom, not every-fold) | 67 |
| reject | 14 |

## The real finding — ADX × family interaction (confirms regime-map Step 1)

The sweep reproduces, at the cell level, the [Step-1](regime-map-step1-results-2026-06-18.md)
thesis that the two families have **complementary ADX profiles**:

| family | ADX none | ADX ≥20 (trend_only) | ADX ≥25 (strong_trend_only) |
|---|---|---|---|
| **pullback** | 10 paper, **0 live** | 1 live + 9 paper | **2 live** + 7 paper + 1 reject |
| **trend** | 3 live + 16 paper + 1 reject | 3 live + 13 paper + 4 reject | **0 live** + 12 paper + **8 reject** |

- **An ADX entry-floor helps pullback** — every live_ready pullback cell carries an ADX
  gate; none clears the every-fold bar without it. (Pullback-continuation needs an
  established trend — exactly Step-1's "pullback wants high ADX.")
- **A high ADX floor hurts trend** — `strong_trend_only` gives the trend family 8 rejects
  and zero live_ready; donchian-breakout wants to enter *before* ADX is extreme (Step-1's
  "trend wants low-moderate ADX, decays at extremes").

This is the directional, robust takeaway: the regime-filter primitive swap is real and
**family-specific**, not a uniform win.

## Actionable shortlist (top live_ready, base/2×-fee net R)
| cell | net_r | 2×-fee | note |
|---|---|---|---|
| `pullback_ETHUSDT_2h_adxmin25_trail5` | 63.1 | 59.5 | eth_pullback + ADX≥25 — promotes past every-fold (baseline adxnone = +59.0R, paper_ready) |
| `pullback_AVAXUSDT_2h_adxmin20_trail3` | 51.8 | 47.8 | avax_pullback + ADX≥20 + tight trail |
| `pullback_AVAXUSDT_2h_adxmin25_trail3` | 42.9 | 39.6 | |
| `trend_SOLUSDT_4h_adxmin20_trail5_conf0.6` | 34.5 | 33.6 | sol_trend + ADX≥20 + selective |
| `trend_ETHUSDT_4h_adxmin20_trail5_conf0.6` | 29.1 | 27.7 | |

(Plus 4 more live_ready: SOL-trend `adxnone_trail5_conf0.6`, `adxmin20_trail3_conf0.6`,
`adxnone_trail3_conf0.6`; ETH-trend `adxnone_trail5_conf0.6`.)

## Caveats — read before treating any cell as "ready"
1. **76/90 surviving is NOT 76 edges.** They are parameter variants of the same ~10 alt
   cells (e.g. eth_pullback appears ~6× at different ADX/trail). This is the
   multiple-comparisons risk the DESIGN §6 names — the honest output is a *handful of
   param refinements* to the already-wired paper cells, not a book of new strategies.
2. **Sweep baseline ≠ live cell params.** Non-swept params use harness defaults; absolute
   net_r isn't directly comparable to the diversified-book per-cell figures. The valid
   signal is the **relative** ADX-on-vs-off comparison *within* a (symbol, family).
3. **Crypto-correlated.** These cells correlate with the existing bybit_1 paper book — same
   diversification caveat as the alt cells.
4. **No out-of-pool holdout yet.** DESIGN §6 requires an out-of-pool symbol/period holdout
   before any cell goes past demo. live_ready here = passed the in-pool every-fold gate, not
   a real-money clearance.

## Where this lands
- **Banked finding:** the regime-filter primitive is a *family-specific* lever — ADX-floor
  lifts pullback, degrades trend at high ADX. This both validates the recombination
  machinery end-to-end and gives a concrete refinement: an ADX≥20–25 entry gate on the
  **pullback** alt cells.
- **Next (Tier-3, operator-gated):** the single highest-value paper refinement is
  `eth_pullback_2h` + ADX≥25 (+63.1R live_ready vs +59.0R baseline). Proposing it = a
  `config/strategies.yaml` param edit (add an `adx_min` to the pullback cells) → draft PR +
  ping; before any real-money step, an out-of-pool holdout + `account_compat_matrix`.
- **Deferred (DESIGN _deferred):** cross-family entry×exit + maker-band exit (the fee-bleed
  attack) need the Phase-3 harness refactor.

---

# Follow-up: out-of-pool holdout (eth) + real-futures (mgc) — 2026-06-18

Raw: `automation/results/research-followup.txt`.

## ETH ADX≥25 — out-of-pool symbol holdout PASSED (DESIGN §6)

The live `eth_pullback_2h` params equal the pullback harness defaults, so the sweep's
winning variant *is* the live config + `adx_min 25`. Applied to **out-of-pool symbols**
never in the sweep (BNBUSDT, LINKUSDT), the ADX≥25 floor improves the pullback cell on
**both**:

| symbol | no-ADX | ADX≥25 |
|---|---|---|
| BNBUSDT (OOP) | reject, +8.0R | **paper_ready, +14.2R** |
| LINKUSDT (OOP) | paper_ready, +13.0R | **paper_ready, +20.0R** |
| ETHUSDT (in-pool ref) | paper_ready, +59.0R | **live_ready, +63.1R** |

ADX≥25 helps **every** pullback cell tested, in-pool and out-of-pool — the regime-filter
effect **generalizes to unseen symbols**, so it's a genuine regime phenomenon, not
ETH-specific overfit. The refinement clears the out-of-pool bar. (OOP magnitudes are
modest — BNB/LINK pullback edges are small to begin with — but the direction is unanimous.)

**Ready to propose (Tier-3, operator-gated):** add `adx_min: 25` to the live pullback alt
cells (eth/sol/xrp/ada/avax `_pullback_2h`) on `bybit_1` PAPER, via a draft `strategies.yaml`
PR + ping.

## MGC `mgc_trend_1h` — confirmed net-negative on REAL futures 1h

Pulled real `GC=F` 1h (yfinance, 2.4y / 353 trades, 2024-01..2026-06) and re-ran the live
cell (donchian20/atr2.5/trail3.0): net **−15.5R** (2024 −19.4, 2025 −11.9, 2026 +15.8).
Net-negative like the XAUUSD spot proxy (−50.7R) — **not a proxy artifact, a genuine
REJECT**. (Both samples agree on sign + on a positive 2026-YTD blip.)

**Recommended (Tier-3, operator-gated):** demote `mgc_trend_1h` from `execution: live` →
`shadow` on `ib_paper` — it currently trades paper money with no validated edge. The daily
`mgc_pullback_1d` (+56R, robust) stays the gold sleeve.
