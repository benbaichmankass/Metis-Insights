# Regime-Cell Walk-Forward — the rec #5 OOS-stability gate (2026-07-29)

**Date:** 2026-07-29
**Gate tooling:** `scripts/research/regime_cell_walkforward.py` + `.github/workflows/regime-cell-walkforward.yml` (PR #7919)
**Runs:** #7920 (gld) · #7921 (qqq) · #7922 (slv) — free GitHub runners, Yahoo feed
**Upstream:** the equity/futures regime-debt matrix — `docs/research/regime-debt-matrix-equity-futures-2026-07-29.md` (#7918)

## Why this gate exists

The regime-debt matrix grades each `(regime, direction)` cell on the **full sample**. But a full-sample losing cell — even a powered one — can be an artifact of *when* that regime happened to occur. **#7915 walk-forward-refuted the 2h-pullback long-drag on exactly that basis.** So the matrix's rec #5 rule is explicit: a losing cell becomes a Tier-3 OFF-cell only if it is negative at adequate n **AND** survives an out-of-sample walk-forward. This gate runs that check: it re-runs the strategy's exact-param harness on the Yahoo feed, keeps the target-regime trades, folds them into N contiguous time-folds, and reports **`short_stable_drag`** (short < 0 in a strict majority of folds AND pooled short-R < 0) — the same `stable_drag` test #7915 established, applied to the short leg within one regime.

The matrix surfaced one strong candidate (`gld_pullback_1h`) and two watches (`qqq_pullback_1h`, `slv_trend_1h`). All three are **faithful** (base harness models every declared lever). Verdicts:

## Verdicts

### 1. `gld_pullback_1h` (trending, short) — SURVIVES decisively → Tier-3 draft OFF cell

| fold | dates | short-R (n) | long-R (n) | net-R |
|---|---|--:|--:|--:|
| 1 | 2023-10-06…2024-06-27 | −7.76 (11) | +5.21 (12) | −2.55 |
| 2 | 2024-07-08…2025-04-08 | −3.47 (7) | +14.85 (15) | +11.37 |
| 3 | 2025-04-14…2025-11-13 | −2.79 (7) | +14.16 (16) | +11.37 |
| 4 | 2025-11-14…2026-07-24 | −1.66 (11) | −1.24 (11) | −2.89 |
| **pooled** | | **−15.68 (36)** | **+32.98 (54)** | +17.30 |

Short is negative in **4/4 folds** → **`short_stable_drag: TRUE`**. The long side is a strong **+32.98R** engine (positive in 3/4 folds). This is a durable, directionally-clean short drag — the one cell that clears the gate decisively.

**Disposition → Tier-3 DRAFT PR #7923** (operator-gated, never auto-merged): `config/regime_policy.yaml` `trending.gld_pullback_1h { long: on, short: off }`, paid down from `coverage_debt` (ceiling 35→34). The hard gate drops trending-regime short intents; longs and transitional/chop are unaffected.

### 2. `qqq_pullback_1h` (trending, short) — survives, but MODEST → operator's call

| fold | short-R (n) | long-R (n) |
|---|--:|--:|
| 1 | −3.65 (8) | +1.76 (12) |
| 2 | +0.90 (9) | +1.44 (11) |
| 3 | −0.02 (14) | −1.52 (6) |
| 4 | −0.07 (10) | −4.75 (10) |
| **pooled** | **−2.84 (41)** | −3.08 (39) |

Short is negative in 3/4 folds with pooled short-R < 0 → **`short_stable_drag: TRUE`** — it clears the mechanical gate. But the magnitude is small (~−0.07R/trade), and the drag is concentrated in fold 1. The **long** side is also mildly negative pooled but only 2/4 folds (not a strict majority) — so a *both-direction* trending skip is **not** justified; only the short leg passes. Because the edge is thin, this is **offered to the operator** in PR #7923 rather than auto-authored — add `trending.qqq_pullback_1h { long: on, short: off }` on request.

### 3. `slv_trend_1h` (trending, short) — REFUTED → stays tracked debt

| fold | short-R (n) | long-R (n) |
|---|--:|--:|
| 1 | +0.45 (7) | +3.26 (8) |
| 2 | −5.16 (5) | −1.78 (10) |
| 3 | −3.10 (5) | +6.19 (10) |
| 4 | +2.20 (6) | +10.44 (8) |
| **pooled** | −5.61 (23) | +18.11 (36) |

Short is negative in only **2/4 folds** (positive in folds 1 & 4) → **`short_stable_drag: FALSE`**. The full-sample −5.61R short drag is **regime-of-sample** — it does not persist out-of-sample. **No cell**; `slv_trend_1h` stays tracked `coverage_debt`. This is the gate doing its job: rejecting a full-sample cell that a naive read would have gated.

## Outcome

- **1 cell authored (as a Tier-3 draft):** `gld_pullback_1h` trending-short → PR #7923 (operator-gated).
- **1 offered:** `qqq_pullback_1h` trending-short — thin edge, operator's call.
- **1 refuted:** `slv_trend_1h` — regime-of-sample noise, stays debt.
- The gate replicates #7915's methodology and is now reusable for any future debt cell (dispatch `regime-cell-walkforward-request` with `strategy:`/`regime:`/`folds:`).

**Tier-1** — research evidence only. The one live-routing change it motivates is the separate operator-gated Tier-3 draft (#7923); this document authors no cell.

## Follow-up dispositions (continued 2026-07-29)

Two rec #5 follow-ups from the equity/futures matrix + the offers above, both
resolved to **no cell**:

### A. `qqq_pullback_1h` (trending, short) — RE-CONFIRMED at finer folds → REFUTED

The 4-fold gate above graded this `short_stable_drag: TRUE` but weak
(pooled −2.84R@41, drag concentrated in fold 1) → **offered**, not authored.
The operator held for a re-confirm with more data. The Yahoo 60m feed caps
history at ~730d, so *more days* is unavailable for a 1h symbol — the lever is
**finer folds**. Re-run at **6 folds** (#7927):

| fold | short-R (n) | long-R (n) | net-R |
|---|--:|--:|--:|
| 1 | −2.07 (5) | +2.71 (9) | +0.64 |
| 2 | −0.62 (5) | +3.64 (8) | +3.02 |
| 3 | +0.04 (7) | −4.24 (7) | −4.20 |
| 4 | +0.39 (10) | +1.54 (3) | +1.93 |
| 5 | −1.56 (7) | −5.57 (7) | −7.14 |
| 6 | +1.82 (7) | −1.93 (6) | −0.11 |
| **pooled** | **−2.01 (41)** | −3.85 (40) | −5.86 |

Short is negative in only **3/6 folds** (not a strict majority) →
**`short_stable_drag: FALSE`**. The mechanical verdict *flips* from TRUE (3/4)
to FALSE (3/6) once the sample is cut finer — the drag was fold-concentrated,
i.e. **regime-of-sample**, exactly the concern the operator raised. **No cell;
`qqq_pullback_1h` stays tracked debt.** (Read the per-fold n with the caveat
that 6 folds leaves ~7 short trades/fold.)

### B. `trend_donchian_sol` (chop, long) — FAITHFUL re-run (stale-exit ON) → drag was the omitted lever

The equity/futures matrix (§5) flagged `trend_donchian_sol` chop-long
**−9.26R@35** as **approximate** — its `stale_exit_bars: 12` /
`stale_exit_below_r: 0.0` exit levers were omitted by the trend harness, so the
drag could be an artifact of the missing lever rather than a real cell. The
trend harness was extended to model the stale-exit lever (#7926) — the **same**
harness the matrix already uses, so base geometry + fee model are unchanged and
the only delta is the now-modeled lever. Faithful re-run (#7928, stale-exit ON;
only the unreplayable `exit_head_*` still omitted):

| regime | net-R (approx, lever OFF) | net-R (faithful, stale-exit ON) |
|---|--:|--:|
| chop **(long)** | **−9.26 (35)** | **−2.32 (39)** |
| trending (long) | — | +1.42 (45) |
| transitional (long) | — | +3.47 (40) |
| **total** | — | **+2.56** |

Modeling the stale-exit lever **collapses the chop-long drag from −9.26R to
−2.32R@39** (~−0.06R/trade) — the lever accounts for ~75% of the measured drag
(it cuts stale underwater trades at bar 12 instead of letting them ride to a
worse exit). The residual −2.32R is small per-trade and below the confidence bar
for a cell, and the still-unmodeled `exit_head_*` (an ML exit head, action=close
— unreplayable offline) can only shrink it further (it cuts losers earlier). So
the **−9.26R "cell" was largely an artifact of the omitted exit lever, not a
real regime cell. No cell; `trend_donchian_sol` stays tracked debt.** This is the
faithful re-run doing its job — preventing a spurious OFF cell.

The other approximate trend rows (`trail_decay_*` / `vol_skip_*` / `giveback_*`
/ `skip_hours` families) carry levers the trend harness doesn't yet model; a
faithful read of those is the next lever-port follow-up.

**Net:** both follow-ups → **no cell**. rec #5 stands at **1 cell shipped**
(`gld_pullback_1h`, #7923); qqq refuted on re-confirm, sol drag explained by the
omitted lever. Still Tier-1 research — no `config/regime_policy.yaml` change.
