# M15 WS-C — Bybit Alt-Perp Generalization Matrix (2026-06-11)

> Workstream C of the M15 soak session (brief:
> [`session-handoff-m15-soak-2026-06-11.md`](session-handoff-m15-soak-2026-06-11.md)).
> Question: which existing BTC-roster strategies transfer to liquid
> Bybit alt perpetuals — same keys, same connector, zero new
> integration. Screening pass (single train/OOS split), NOT promotion
> evidence: any cell advanced from here must pass the WS-B-style k-fold
> walk-forward first. Driver: `scripts/ops/m15_ws_c_{fetch,sweep}.sh`
> (trainer-vm-diag #3350 → #3375); raw outputs on the trainer under
> `/home/ubuntu/m15-phase0/results/m15_ws_c/`.

## Method

- **Universe**: ETH / SOL / BNB / XRP / ADA / LINK / AVAX USDT linear
  perps. 15m + 5m candles from Bybit public klines, 2020-01-01 (or
  listing) → 2026-06-11. (First fetch lost 8 datasets to rate-limit
  retCode 10006 — fetcher now retries it and paces pages; merged in
  #3360.)
- **Families** (the Phase-0 pattern + the two 4h roster members):
  trend 1h + 4h (harness defaults: donchian 20 / stop 2.5 / trail 3.0,
  bidirectional, no confidence floor — deliberately NOT the BTC-tuned
  long-only/0.60 live variant), htf_pullback 2h (defaults == live),
  fade 4h + squeeze 4h (live-mirror params), fvg_range 15m (defaults ==
  live), ict_scalp 5m (defaults, exact net post-processing — the
  harness has no fee model).
- **Fees**: 7.5 bps roundtrip (Bybit perp taker), everywhere.
- **Split**: train < 2025-01-01 ≤ OOS (≈4y / 1.5y; less for late
  listings).

## Matrix — net R (7.5 bps), train / OOS

✅ = positive in BOTH windows ("robust cell").

| Symbol | trend 1h | trend 4h | pullback 2h | fade 4h | squeeze 4h | fvg 15m | ict_scalp 5m (net) |
|---|---|---|---|---|---|---|---|
| **ETH** | −39.8 / +20.0 | **+30.4 / +9.1 ✅** | **+35.4 / +33.5 ✅** | +18.9 / −7.1 | +22.1 / −6.0 | −12.0 / −7.6 | **+63.3 / +17.2 ✅** |
| **SOL** | +1.4 / −13.5 | **+13.4 / +12.2 ✅** | **+35.9 / +14.0 ✅** | −11.1 / +3.5 | +1.3 / +2.8 (thin) | −15.7 / −1.5 | −21.0 / +39.0 |
| **BNB** | −50.4 / +13.0 | +14.4 / −0.4 | +52.0 / −4.6 | −19.2 / −3.7 | −0.4 / −1.5 | +9.0 / −29.5 | +42.5 / −1.4 |
| **XRP** | −10.7 / +57.4 | **+9.5 / +25.7 ✅** | +19.0 / +4.3 ✅(thin OOS) | +15.8 / −7.4 | −1.3 / +3.2 | −9.6 / +7.4 | +98.1 / −1.7 |
| **ADA** | −16.1 / +32.4 | **+24.3 / +12.2 ✅** | **+48.2 / +18.6 ✅** | +5.2 / +1.3 ✅(thin) | +5.2 / −0.5 | −24.2 / +1.3 | **+47.3 / +19.5 ✅** |
| **LINK** | +6.6 / −12.0 | −12.4 / +0.7 | −19.0 / +11.7 | −21.4 / −13.4 | −6.6 / −1.6 | −33.9 / −9.7 | **+108.8 / +35.1 ✅** |
| **AVAX** | **+45.1 / +10.6 ✅** | **+15.3 / +13.2 ✅** | +40.2 / −6.9 | −5.9 / −28.9 | +9.4 / +4.2 ✅(thin) | −28.0 / −19.9 | **+17.3 / +33.7 ✅** |

(ict_scalp trade counts are large — 311–1,607 per window — so those
cells are well-sampled; squeeze/fade OOS cells run 16–52 trades.)

## Findings

1. **trend 4h is the family that generalizes**: positive train AND OOS
   on 5 of 7 alts (ETH, SOL, XRP, ADA, AVAX) at untuned defaults — the
   same family that carried BTC, XAU and the equity-index dailies.
   trend 1h does NOT transfer (whipsaw at alt 1h noise; 5/7 train-
   negative).
2. **htf_pullback 2h transfers to the majors-of-the-alts**: ETH
   (+35.4/+33.5 — the best OOS expectancy in the matrix, +0.36R/trade),
   ADA, SOL, XRP(thin). Same param neighborhood as the live BTC leg.
3. **ict_scalp 5m survives 7.5 bps on 4 of 7** (ADA, AVAX, ETH, LINK)
   with hundreds of OOS trades — but it's also the family most exposed
   to real-world slippage beyond the flat taker assumption on thinner
   alt books.
4. **fade, squeeze, fvg_range do not generalize** (isolated thin
   positives only) — consistent with fade/squeeze's shadow demotion on
   BTC and fvg_range's BTC-scale width filter failing on FX/equities.
5. **Symbol coherence**: ETH and ADA are robust on three families each,
   AVAX on three (incl. the only trend-1h pass), SOL/XRP on two, LINK
   on one (ict only), BNB on none.

## The caveat that bounds all of this

**Alts correlate ~0.7–0.9 with BTC.** These legs buy trade frequency
and per-strategy diversification, not portfolio diversification — in a
BTC drawdown the whole crypto book draws down together, and the
account-level daily-loss / drawdown caps on bybit_1/bybit_2 become the
binding constraint. Sizing for any adopted leg must assume concurrent
loss with the existing BTC roster.

## Tier-3 proposals (operator decision required — nothing wired)

Shadow-first on **bybit_1 (demo)** only, mirroring the xauusd_trend_1h
wiring pattern (per-symbol strategy instance + instrument profile +
account routing); promotion past demo-shadow stays a separate gate.
Ranked:

1. **`eth_pullback_2h`** — the matrix's strongest cell (+35.4/+33.5,
   OOS exp +0.36, 93 OOS trades), same family/params as the live BTC
   leg.
2. **`eth_trend_4h` + `ada_trend_4h`** (or AVAX) — the generalizing
   family on its two most coherent symbols.
3. **`ada_pullback_2h`** — second pullback confirmation (+48.2/+18.6).
4. (Research-only continuation, no wiring): ict_scalp on ADA/AVAX/ETH/
   LINK — run the WS-B k-fold harness on these four before any slot is
   considered; the 5m family needs the strictest validation given
   slippage sensitivity.

Each adopted cell must first pass the k-fold anchored walk-forward
(positive every fold + 2× fee headroom) before even a shadow slot —
this sweep is the screen, not the evidence. BNB and LINK (ex-ict) drop
from consideration; fade/squeeze/fvg_range alt variants are dead ends
at current params.

## Reproduction

```bash
# trainer VM, worktree /home/ubuntu/m15-phase0
bash scripts/ops/m15_ws_c_fetch.sh   # resumable; rate-limit-safe
bash scripts/ops/m15_ws_c_sweep.sh   # -> results/m15_ws_c/SUMMARY.md
```
