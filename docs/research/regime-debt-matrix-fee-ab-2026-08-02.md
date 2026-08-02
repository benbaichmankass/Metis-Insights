# Regime-Debt Matrix — fixed-window fee A/B (2026-08-02)

**Run:** [issue #8329](https://github.com/benbaichmankass/Metis-Insights/issues/8329) ·
workflow `regime-debt-matrix` (free runner, 730d, `--fee-ab 0,7.5`).
**Tooling:** [PR #8327](https://github.com/benbaichmankass/Metis-Insights/pull/8327)
(`scripts/research/regime_debt_matrix.py::--fee-ab`).
**Resolves:** `BL-20260730-FEE-AB-FIXED-WINDOW`.
**Supersedes the §1 caveat of:** [`regime-debt-matrix-corrected-cost-2026-07-30.md`](regime-debt-matrix-corrected-cost-2026-07-30.md).
**Tier:** analysis only. **No cell is authored or revoked by this document.**

## Why this run

The 2026-07-30 corrected-cost re-grade compared two *separate* matrix passes ~a day
apart. Because the matrix window is a **rolling 730 days**, every per-cell delta mixed
the fee correction with a ~1-day window slide — not separable from two runs. Its §1
caveat said so, and pointed at one row as proof the confound was real: `slv_trend_1h`
chop-**short** moved **−0.56R the WRONG way** at n=10 — a fee *reduction* cannot make a
cell worse, so that delta had to be window drift.

This run removes the confound the way §1 asked: **one pass, one fetched candle window,
two `--fee-bps-roundtrip` arms (0 and 7.5), diffed per cell.** The trade set is identical
across arms (fees do not feed back into signal generation), so `Δ = net_R(7.5) −
net_R(0)` is a **measured** per-cell fee effect, not an inferred one.

## 1. The isolation is clean — every cell moves the correct direction

**All 20 gradeable cells show a negative `net_R` delta.** A fee can only reduce R, so a
clean fee A/B must produce *only* negative deltas — and it does. The confounded
two-run comparison could not (its `slv` chop-short row went the wrong way); this one has
**zero wrong-signed cells**. The single anomaly §1 flagged is resolved below.

## 2. Measured per-cell fee drag (Δ net-R, 0 → 7.5 bps)

| Strategy · cell | Δ net-R | Δ long-R | Δ short-R | n (long/short) | Δ per trade |
|---|--:|--:|--:|--:|--:|
| `gld_pullback_1h` trending | **−6.92** | −4.11 | −2.81 | 54 / 37 | **−0.076** ✅ |
| `gld_pullback_1h` transitional | −2.17 | −1.31 | −0.86 | 14 / 10 | −0.090 ✅ |
| `gld_pullback_1h` chop | −0.68 | −0.36 | −0.33 | 4 / 4 | −0.086 ✅ |
| `slv_trend_1h` trending | −2.20 | −1.43 | −0.77 | 30 / 20 | −0.044 ✅ |
| `slv_trend_1h` transitional | −1.31 | −0.67 | −0.64 | 15 / 17 | −0.041 ✅ |
| **`slv_trend_1h` chop** | **−1.28** | −0.83 | −0.45 | 18 / 9 | −0.047 ✅ |
| `htf_pullback_trend_2h` trending | −3.09 | −1.46 | −1.64 | 42 / 54 | −0.032 |
| `htf_pullback_trend_2h` transitional | −1.31 | −0.65 | −0.67 | 17 / 17 | −0.039 |
| `htf_pullback_trend_2h` chop | −1.07 | −0.67 | −0.40 | 18 / 10 | −0.038 |
| `fade_breakout_4h` chop | −1.40 | −0.76 | −0.64 | 28 / 24 | −0.027 |
| `trend_donchian` trending (long-only) | −2.35 | −2.35 | 0.00 | 42 / 0 | −0.056 ✅ |
| `trend_donchian` transitional (long-only) | −1.88 | −1.88 | 0.00 | 38 / 0 | −0.050 ✅ |
| `trend_donchian` chop (long-only) | −3.01 | −3.01 | 0.00 | 47 / 0 | −0.064 ✅ |
| `squeeze_breakout_4h` trending | −0.27 | −0.09 | −0.17 | 3 / 6 | −0.030 |
| `squeeze_breakout_4h` transitional | −0.10 | −0.10 | 0.00 | 5 / 0 | −0.020 |
| `squeeze_breakout_4h` chop | −0.39 | −0.23 | −0.16 | 7 / 7 | −0.028 |

(`vwap` produced no gradeable cells this run — BTCUSDT 5m, no classified harness.
Absolute per-arm matrices for every cell are in the run's `results.json` artifact.)

**Δ per trade = Δ net-R ÷ (long_n + short_n).** ✅ marks a cell inside the predicted
**0.04–0.12 R/trade** band (`fee_r = (bps/1e4) × price / risk`, `risk = atr_stop_mult ×
ATR`).

## 3. What the measured numbers say

- **The predicted band holds where it should.** The commission-free **equity/ETF sleeve**
  (`gld_pullback_1h`, `slv_trend_1h` — Yahoo feed) lands **0.044–0.090 R/trade**, squarely
  inside the predicted 0.04–0.12. This is the sleeve the over-charge bug (a crypto-perp
  7.5 bps on 0-fee Alpaca venues) actually distorted, so it is the sleeve where the
  measured drag matters — and the prediction is confirmed on measured, not inferred, data.
- **BTC (Binance) cells run lower** (~0.027–0.064 R/trade). Expected and consistent: BTC's
  larger ATR makes `fee_r = (bps/1e4) × price / risk` smaller *relative to R* than on the
  lower-ATR equity/ETF instruments. Nothing anomalous.
- **The §1 anomaly is resolved.** `slv_trend_1h` chop-short read **+0.56R (wrong sign)**
  in the two-run comparison; over the identical window here the whole chop cell is a clean
  **−1.28R** (short leg −0.45R). So that row was **window drift, not a fee effect** —
  exactly what §1 argued, now confirmed by construction.

## 4. Disposition (no cell authored/revoked here — Tier-3)

- The per-cell deltas above are now **measured fee amounts** — the corrected-cost doc's
  §1 caveat ("do not treat the per-cell deltas below as measured fee amounts") is lifted
  and replaced with a pointer to this doc.
- The **fee=0 arm is the corrected grade** for the equity/ETF sleeve (those venues already
  resolve to 0 bps), so this run does **not overturn** any corrected-cost disposition — it
  *validates* that the corrected-vs-over-charged delta was the fee, not the window. The
  qqq/slv/spy withdrawals in `regime-debt-matrix-corrected-cost-2026-07-30.md` §3 stand on
  the same measured basis they claimed.
- Any live-routing cell change remains a separate **Tier-3** draft PR for operator
  approval. This document authors nothing.
