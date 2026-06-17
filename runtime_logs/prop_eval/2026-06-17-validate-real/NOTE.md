# Prop validation gate — trend_donchian on real Bybit perp + Breakout cost model (2026-06-17)

Re-validation of the `trend_donchian`-on-alts +EV finding (PB-20260616-004) on
**real Bybit LINEAR-PERP 5m candles** (2023-01-01 → 2026-02-28, 332,641 bars/sym)
with funding/holding cost factored in and a per-alt walk-forward — the two things
the resolution criteria required before any Tier-3 wiring. Run on the trainer VM
(the sandbox can't reach Bybit or run pandas) from a detached worktree.

Harness: `scripts/prop/validate_alt_prop.py` (full-period cost-aware EV + 4-fold
walk-forward) + `src/prop/funding.py` (per-trade holding-cost drag) over the real
per-strategy `order_package` ledger. Engine commission 7.5 bps round-trip
(≈ Breakout's ~0.04%/side). Block-bootstrap 3000 paths, clock_tf 1h, flip hold,
$5k Breakout 1-Step ruleset.

## Cost model — the venue-correct correction

The original research was on Binance SPOT with NO funding. Breakout is a
**perpetual-futures-STYLE** sim (tier-1 CEX perp liquidity, 100+ assets incl.
the alts; leverage 5× BTC/ETH, 2× other alts) but charges a **flat CFD-style
daily swap (~0.09%/day per public reviews, UNCONFIRMED in-terminal)** rather than
Bybit's directional 8h funding — ~3× heavier. The **GATE below uses that
daily-swap model**; a lighter Bybit perp-funding pass (also all PASS) is kept in
`bybit-funding/` for comparison.

> Swap rate 0.0009/day is a third-party-review figure — confirm per symbol from
> the DXTrade instrument spec before sizing real capital. The model takes it as
> a knob (`--swap-rate-daily`).

## Result (GATE = Breakout daily-swap)

| symbol | verdict | pre-swap gross | post-swap gross | swap drag | 12-mo EV @1.5% | P(net>0) | WF folds + |
|---|---|---|---|---|---|---|---|
| **SOLUSDT** | **PASS (robust)** | +$2,150 | +$1,823 | 15% | **+$1,693** | 94% | **4/4** |
| **ETHUSDT** | **PASS (marginal)** | +$671 | +$285 | **57%** | +$1,050 | 92% | 4/4 |
| **BNBUSDT** | PASS (label only) | **−$37** | **−$524** | n/a | +$665 | 78% | 3/4 |

EV cells per symbol + the full walk-forward tables are in
`breakout-swap/validate_<sym>.md`.

## Honest read (label ≠ recommendation)

The script's PASS uses the EV-model 12-mo mean-net + fold positivity. That metric
inherits the engine's **realised-only optimism** (a renewable-account, bank-ASAP,
compounded block-bootstrap can show +EV even on a flat/negative realised ledger,
because winners bank before breaches). So read PASS alongside the **pre-swap
realised gross**, which is the un-modelled ground truth:

- **SOL — genuinely robust.** Strongly +EV realised (pre $2,150), light swap drag
  (15%), 4/4 OOS folds positive incl. the chop window. The real signal. **Wire.**
- **ETH — marginal.** The daily swap eats **57%** of the realised gross (pre $671
  → post $285); positive EV only materialises at risk ≥1.0%. 4/4 folds positive,
  so the edge is consistent, but thin. **Wire as shadow / watch; not a strong
  live candidate on its own.**
- **BNB — do NOT wire.** Realised ledger is **negative before and after swap**
  (pre −$37 → post −$524). The +EV is pure EV-model optimism (the exact caveat),
  and fold 2 is deeply negative. This is a label-only PASS; the underlying edge
  is absent. **Excluded.**

## Gate decision

`trend_donchian` on a **high-vol alt holds up on real Bybit-perp data with the
Breakout daily-swap cost — for SOL (strong) and ETH (marginal); it does NOT hold
for BNB.** Recommendation: promote **SOL** (and optionally **ETH** as shadow);
exclude **BNB**. PB-20260616-004 → validated (partial); proceed to Tier-3 wiring
for the surviving variant(s) via a draft PR, operator-gated.
