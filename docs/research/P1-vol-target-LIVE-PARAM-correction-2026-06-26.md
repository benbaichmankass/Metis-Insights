# P1 vol-target — LIVE-PARAM re-run CORRECTION (2026-06-26)

**Correction to `P1-vol-target-result-2026-06-26.md`.** The original P1 "KEEP /
graduate to shadow" verdict was computed on a **proxy book** (T0.1 flagged it:
trend at the wrong TF without its conf gate, a shadow strategy included, two
non-existent cells). Re-running on the **live-param** book reverses the headline:
**vol-targeting adds almost nothing to the actual (healthy) live book.** Do NOT
graduate the overlay on current evidence.

## The re-run
Crypto core of the live book, **live params**, on real candles (trainer relay #4686,
after the registry fix #4685):
- trend_donchian BTC/ETH/SOL **1h, min_conf 0.6**; trend ETH/SOL/ADA/AVAX **4h, min_conf 0.6**
- squeeze_breakout_4h BTC **kc_mult 1.0**
- fvg_range_15m BTC
- `*_pullback_2h` ETH/SOL/ADA/AVAX (the real live cells; tp_r/trail live-faithful)
- fade DROPPED (it's `execution: shadow`, not live); BTC/MES pullback DROPPED (don't exist live)
- **3,931 trades, 2021-03-23 → 2026-06-25.**

(Omitted for missing data / lower weight: XRP, the 1d futures/ETF + 1h ETF/FX cells,
ict_scalp_5m, htf_pullback_trend_2h. The crypto core is the bulk of the book's activity.)

## Result — the reversal

| Book | Baseline Sharpe | Overlay 0.8× weekly | Overlay 0.8× monthly | Vol effect | DD effect |
|---|---|---|---|---|---|
| **Proxy** (orig P1) | 0.27 | 0.44 (**+0.17**) | 0.47 (**+0.20**) | ~flat/down | down (75→69) |
| **Live-param** (this) | **1.33** | 1.41 (**+0.079**) | 1.32 (**−0.01**) | **up** (62→64–66) | **no improvement** (104→98–109) |

- The live book is **much healthier** (Sharpe 1.33, +506R, every year positive) — the live
  params (min_conf 0.6 trend filter; real per-symbol pullbacks; no fade drag) matter a lot.
- On that healthy book the overlay's benefit collapses to **+0.08 Sharpe (weekly) / ~0
  (monthly)**, **raises** realized vol, and does **not** cut drawdown.
- Interpretation: vol-targeting's gain scales **inversely with book quality** — it rescues
  a mediocre, vol-clustered return stream (the proxy) but adds little to an already
  risk-efficient one. The original +0.17–0.20 lift was ~2–3× overstated by the proxy.

Per-sleeve (live params): trend BTC/ETH/SOL 1h carry the book (+80/+72/+71R); the alt
pullbacks are strong (sol +77, ada +64); **`fvg_range_BTC` is still the lone drag (−41R)**
— corroborates `PB-20260626-003`.

## Recommendation (revised)
- **Do NOT graduate the P1 vol-target overlay to live sizing.** The risk/complexity of a
  live order-path sizing multiplier is not justified by ~+0.08 Sharpe (weekly, fragile —
  monthly is ~0) with no drawdown benefit. `PB-20260626-002` downgraded accordingly.
- Optional, low priority: a pure **observe-only shadow soak** of the would-be multiplier
  (no resize) if we want live confirmation, but the backtest says the ceiling is small.
- The standing wins from this re-run are independent of the overlay: the **live book is
  genuinely good (Sharpe 1.33)**, and **`fvg_range_BTC` is a confirmed drag** worth a
  TUNE/DEMOTE review (`PB-20260626-003`).
- Caveat: crypto-core only. Adding the low-vol 1d ETF/futures cells could shift the book's
  vol-clustering, but is unlikely to turn a ~0 monthly / +0.08 weekly result into a
  graduation-worthy one.

## Honesty note
The original P1 result was reported as "the session's one validated win." That was based
on the proxy book and is **corrected here** — the live-param evidence does not support
graduation. This is the live-param re-run (`PB-20260626-005`) doing its job: catching an
overstated conclusion before it reached live money.
