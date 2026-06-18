# Regime-conditional performance map — Step 1 results (2026-06-18)

First probe of the regime-conditional strategy-weighting initiative
([DESIGN](regime-conditional-strategy-weighting-DESIGN.md)). Run on the trainer
via `vm-driver`; tool: `scripts/ops/regime_performance_map.py` (per-trade edge
bucketed by ADX(14) band, no-lookahead, over the full WS-C alt history).

## Verdict: the thesis holds — edge is regime-concentrated and economically sensible

Every cell has a **predictable regime where it is reliably +EV**, with healthy
sample sizes (n = 40–170 per band) and meaningful per-trade means (+0.1 to
+0.67 R). The two families have **opposite, complementary** ADX profiles:

**Pullback (`htf_pullback_trend_2h`) → edge in HIGH ADX (trending):**

| cell | <15 | 15-25 | 25-35 | >35 | best band (share) |
|---|---|---|---|---|---|
| pullback_ETH_2h | +0.04 | **−0.13** | **+0.67** | +0.12 | 25-35 (101%) |
| pullback_XRP_2h | −0.30 | −0.17 | +0.24 | +0.23 | 25-35 (112%) |
| pullback_AVAX_2h | +0.02 | −0.02 | +0.14 | +0.18 | >35 (63%) |
| pullback_ADA_2h | −0.08 | +0.15 | +0.25 | +0.24 | 25-35 (40%) |
| pullback_SOL_2h | −0.59 | +0.20 | −0.03 | +0.43 | >35 (70%) |

Pullback-continuation **makes money in trends (ADX ≥ 25) and loses/flatlines in
chop (ADX 15-25)** — textbook, and consistent across 4/5 cells.

**Trend/breakout (`trend_donchian`) → edge in LOW–MODERATE ADX, decays at extremes:**

| cell | <15 | 15-25 | 25-35 | >35 | best band (share) |
|---|---|---|---|---|---|
| trend_ETH_4h | +0.37 | +0.17 | +0.12 | **−0.10** | 15-25 (66%) |
| trend_AVAX_4h | +0.51 | +0.13 | **−0.06** | **−0.08** | 15-25 (75%) |
| trend_ADA_4h | +0.26 | +0.09 | +0.10 | +0.22 | 15-25 (39%) |
| trend_SOL_4h | −0.01 | +0.06 | +0.13 | +0.19 | 25-35 (44%) |
| trend_XRP_4h | −0.15 | +0.14 | +0.15 | +0.20 | 15-25 (55%) |

Donchian-breakout **catches moves early (low ADX); ETH/AVAX trend actually lose
once ADX is extreme (>35)** — the move is already exhausted / mean-reverting.

## Why this matters

1. **The strategies aren't uniformly mediocre** — their edge is concentrated in
   identifiable regimes, with several cells *losing* in their wrong regime
   (pullback in chop, trend in extreme-ADX). A "know-when-to-listen" weight that
   zeroes those cohorts removes real losers.
2. **The two families are regime-complementary** — pullback wants high ADX,
   breakout wants low-moderate ADX. A regime-weighted portfolio would route to
   pullback in trends and breakout pre-trend, covering the ADX spectrum — a
   genuinely promising portfolio structure, not just per-cell cleanup.

## Caveats (carry into Step 2)

- **In-sample.** This is the full history. The regime→edge map must be validated
  **out-of-sample** (fit on train, evaluate on a held-out period) — the same
  rigor that rejected the ADX-threshold candidates. A sensible regime structure
  is encouraging that it's signal not noise, but not proof.
- ADX is **no-lookahead** (computed from bars closed at/before entry) — good —
  but Step 2 must show that weighting by the **current** regime improves the
  **portfolio** net PnL on held-out data, not just that hindsight buckets differ.

## Next — Step 2

Build a v0 weight map `w_s(regime)` from this map (e.g. clamp each cell's size
by its regime's sign/magnitude), run `scripts/backtest_system.py` un-weighted vs
regime-weighted over a **train period**, and evaluate net PnL / drawdown /
Sharpe on a **held-out period**. If the weighted book beats the un-weighted one
out-of-sample, graduate toward the regime-router soft-weight phase.

---

# Step 2 results — regime-weight overlay MATRIX (2026-06-18)

Tool: `scripts/ops/regime_weight_overlay.py`. Matrix `regime_def {adx, adxvol} x
scheme {baseline, hard_sign, graded, winrate}`, weights **fit on train, scored
on holdout**, at two cutoffs (2025-01-01, 2024-07-01). 3,147 trades across the
10 cells.

## Two findings

**1. The un-weighted 10-cell portfolio is already robustly net-positive OOS:
holdout +140.0R (cutoff 2025-01-01) / +198.3R (cutoff 2024-07-01).** This is the
overall-P&L-positive book — achieved by *diversification* across 10 cells x 5
symbols x 2 complementary families, NOT by regime weighting. None of these cells
passes the every-fold gate standalone, yet the aggregate is strongly +OOS.

**2. Regime weighting — in this per-(cell, regime-band) point-estimate form —
does NOT beat the un-weighted baseline out-of-sample, at either cutoff.** Every
scheme's holdout `vs base` is negative; the gating schemes show high
train->holdout degradation (0.3-0.76) and several have HIGHER train net-R than
baseline but LOWER holdout — the overfit signature. `hard_sign` came closest
(~-19R, with lower drawdown — roughly a wash); `graded`/`winrate` over-zeroed.

## Interpretation

Step 1's regime concentration is real in-sample but **does not generalize via a
naive per-cell-per-band weight** — it moves the overfit up a level (the DESIGN's
named risk). The variation matrix is what caught it (a single in-sample
hard_sign config looked like a +44R train win).

## Where this leaves the initiative

- **Banked win:** the diversified un-weighted alt book is the realistic
  overall-P&L-positive portfolio. That stands on its own.
- **Regime layer — not dead, but the bar is now "beat the already-strong
  +140-198R OOS baseline."** The next variations to test (fewer parameters =
  less overfit): (a) a **family-level** rule (one weight for "pullback in chop",
  not per-cell), (b) the **regularized regime classifier** (`btc-regime-*`)
  rather than raw bucket means, (c) **reductive-only, gentle** down-weighting.
  If none beats diversification OOS, the honest answer is "diversification is
  the edge; regime weighting doesn't add value here" — and we stop.
- **Methodology:** the existing `btc-regime` classifier predates the
  variation-matrix discipline; it should be re-validated the same way
  (matrix + train/holdout) before being trusted as a weighting input.

---

# Step 3 — bank the diversification win + family-level regime variant (2026-06-18)

Two trainer runs on the cached 10-cell map (`results/m15_regime_map`), via
`vm-driver` (`automation/results/portfolio-robust.txt`, `regime-family.txt`):

1. `scripts/ops/portfolio_robustness.py` — multi-angle robustness of the
   un-weighted book, the gate before it becomes a Tier-3 portfolio proposal.
2. `scripts/ops/regime_weight_overlay.py --group family` — the fewer-parameter
   regime-weight variant Step 2 flagged, vs the per-cell form, at both cutoffs.

## 3a. Diversified book is robust on every axis except a flat 2026-YTD

3,147 trades, **2021-03 .. 2026-06**. Full book **net +409.8R**, mean +0.130R,
**Sharpe 4.03**, max drawdown 96.2R.

| Robustness axis | Result | Pass? |
|---|---|---|
| Per-year net-R | 2022 +32 · 2023 +181 · 2024 +56 · 2025 +142 · **2026-YTD −1.7** (316 tr, mean −0.005R) | all but 2026-YTD |
| Holdout sweep (5 cutoffs) | +341.7 / +196.0 / +198.3 / +140.0 / +123.4R — **all positive** | ✅ |
| Leave-one-cell-out (10) | worst (pullback_ETHUSDT) still leaves **+341.2R**; all 10 positive | ✅ |
| Leave-one-family-out | trend-only **+169.7R**, pullback-only **+240.1R** — both families independently strong | ✅ |
| Added-cost headroom | breakeven **+0.130R/trade**; at +0.05R/trade extra cost still +252R | ✅ |
| Block bootstrap (3000×, monthly) | **P(net>0)=0.984**, 5th-pct **+98.1R**, median 406.6R | ✅ |

The tool's single composite flag reads **NOT fully robust** — but that is driven
**entirely** by the `all_years_positive` check failing on **2026-YTD = −1.7R**, a
dead-flat partial year (mean −0.005R over 316 trades), not a losing one. Every
other axis — cutoffs, per-cell, per-family, fees, bootstrap — passes strongly.

**Honest read:** the diversified book is genuinely robust; the one blemish is
that **2026-YTD is flat** while 2025 was strongly +142R. That is a real
**watch-item** (early alpha decay vs partial-year noise — can't distinguish yet
on 5.5 months) but does **not** sink the thesis. The win is bankable, with the
2026 flatness called out as the thing to resolve before any real-money step.

## 3b. Family-level weighting kills the overfit but is only a risk-adjusted wash

`--group family` fits one weight per (family × regime-band) — ~2 params/band
instead of one per (cell × band). The overfit signature **collapsed**:
train→holdout degradation fell from the per-cell **0.30–0.76** to **0.08–0.31**
(fewer parameters generalize, exactly as predicted).

Best variant — **`adxvol / hard_sign / family`** (drop a family's net-negative
regime bands only):

| cutoff | baseline holdout | family hard_sign holdout | vs base | Sharpe (base→fam) | maxDD (base→fam) |
|---|---|---|---|---|---|
| 2025-01-01 | 140.0R | **157.2R** | **+17.2** | 2.33 → **2.91** | 27.3 → **19.7** |
| 2024-07-01 | 198.3R | 185.7R | −12.6 | 2.96 → **3.05** | 24.3 → **20.1** |

So on **net-R it's a wash** (+17 / −13 across the two cutoffs — does not reliably
beat diversification), but it improves **Sharpe and lowers max-drawdown at both
cutoffs**. Every other scheme (graded, winrate; per-cell anything) still loses
OOS, often badly.

**Verdict:** regime weighting still does **not** deliver a clear PnL win over the
un-weighted book — diversification remains the edge. The family-level reductive
`hard_sign` is the **only** variant that isn't a loser: net-neutral but
risk-adjusted-better (higher Sharpe, ~25% lower drawdown). That makes it a
legitimate **future soft-weight candidate** (Phase-4 of the regime router, as a
drawdown-reducer rather than a return-enhancer) — but not something to promote
now. The classifier-driven variant (`btc-regime-*`) stays on hold for a later
session, to be validated under this same matrix discipline.

## Where this lands

- **Bank:** the diversified 10-cell un-weighted book is the realistic
  overall-P&L-positive portfolio — robust across cutoffs, cells, families, fees,
  and bootstrap. Watch-item: 2026-YTD is flat (resolve before a real-money step).
- **Regime layer:** parked as a **drawdown-reduction** soft-weight idea (family
  reductive hard_sign), not a return booster. Re-open with the classifier under
  the matrix discipline in a dedicated session.
