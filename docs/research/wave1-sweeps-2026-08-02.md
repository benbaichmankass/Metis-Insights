# Wave 1 free-runner sweeps — GLD walk-forward + ETF fee-A/B residual (2026-08-02)

**Plan:** [`WORK-PLAN-2026-08-02.md`](WORK-PLAN-2026-08-02.md) Wave 1 (Tier-1 evidence,
free GitHub runners, $0, no VM lane).
**Runs:** #8410 (regime-cell-walkforward, `gld_pullback_1h`) · #8411 (regime-debt-matrix
fee A/B, 17 ETF/equity legs).
**Tracking:** `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` · M36 Track B ·
RESEARCH-PROGRAM R2 (tool, separate PR #8412).
**Tier:** analysis only. **No cell is authored, revoked, or changed by this document.**

---

## 1.7 — `gld_pullback_1h` trending cell, corrected-cost WALK-FORWARD (#8410)

The one **live, shipped** GLD Tier-3 cell (`config/regime_policy.yaml`:
`trending.gld_pullback_1h { long: on, short: off }`) was authored 2026-07-29 (#7923)
from a walk-forward run under the **~25× venue-fee over-charge** (7.5 bps roundtrip on a
commission-free Alpaca ETF). The corrected-cost **matrix** re-audit (A1, #7962) already
re-confirmed it full-sample.

> **Novelty note (honest reconciliation).** A corrected-cost GLD walk-forward was in fact
> **already run 2026-08-01** (#8272, recorded on `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE`),
> returning the identical pooled short −12.8889R@36 / long +37.0874R@54. This session's #8410
> run **reproduced it bit-for-bit** — an independent determinism confirmation, not a new
> measurement (the WORK-PLAN listed 1.7 without reconciling against #8272). The genuinely
> new Wave-1 work is **1.4** (the ETF fee-A/B *residual*, below), **1.3** (the R2 cut-point
> sweep tool + result, §1.3), and the A1 runner scope. #8410 is retained here because the
> exact reproduction is itself worth recording.

`faithful` row, GLD 1h pullback, 730d (window 2023-10-06..2026-07-24), 4 folds:

| fold | dates | n | long-R (n) | short-R (n) | net-R |
|---|---|--:|--:|--:|--:|
| 1 | 2023-10-06..2024-06-27 | 23 | +6.24 (12) | −6.60 (11) | −0.36 |
| 2 | 2024-07-08..2025-04-08 | 22 | +16.18 (15) | −2.88 (7) | +13.31 |
| 3 | 2025-04-14..2025-11-13 | 23 | +15.32 (16) | −2.23 (7) | +13.09 |
| 4 | 2025-11-14..2026-07-24 | 22 | −0.66 (11) | −1.18 (11) | −1.84 |
| **pooled** | | 90 | **+37.09 (54)** | **−12.89 (36)** | +24.20 |

**Cell verdict (FOLD_PANEL 3/4/5, fold-count invariant):**
- **`short_stable_drag: TRUE`** — short negative under every panel fold-count (3→3/3,
  4→4/4, 5→4/5) AND pooled short-R = **−12.89 < 0**. A short-side OFF cell is justified.
- **`long_stable_drag: False`** — long positive; the +37.09R engine is correctly left ON.

### Disposition — **KEEP the cell. Confirmed on both axes at corrected cost.**

| axis | short-R (n) | verdict |
|---|--:|---|
| over-charged walk-forward (#7920, 2026-07-29) | −15.68 (36) | authored the cell |
| corrected **matrix** (A1, #7962) | −13.88 (37) | survives full-sample |
| corrected **walk-forward** (#8410, this run) | **−12.89 (36)** | **`short_stable_drag: TRUE`** |

The fee correction shrank the short drag by ~2.8R (−15.68 → −12.89), exactly the direction
predicted (removing an over-charge makes a short look *better*) — and it is **still a stable
OOS drag** at −0.358 R/trade. This resolves **operator decision #2** of RESEARCH-PROGRAM
(*"keep or revert `trending.gld_pullback_1h { short: off }` once corrected evidence lands"*):
**keep — no revert, no Tier-3 PR needed** (the live cell already matches the confirmed
verdict). The predicted long-side *understatement* also holds: +37.09R/54 = **+0.687 R/trade**.

### M36 Track B — routing the GLD-1h **book** into the portfolio (separate, +EV)

Distinct from the cell above: the *whole* `gld_pullback_1h` book is an M27 batch-3 **STRONG
PASS** (`M27-P0-repull-followups-2026-07-21.md`: +5.92R net OOS, +0.49R/trade, 4/4 k-fold
folds — the strongest non-crypto cell M27 found). M36 Track B is "fit already-passing cells
into the EXISTING portfolio, NO new integration": GLD already trades on `alpaca_paper`
(`mode: live`, paper money). Routing it further (shadow-first toward `alpaca_portfolio` /
eventual `alpaca_live`) is a **Tier-3** decision gated on `account_compat_matrix` + a shadow
soak + its own regime cells — see the packet in §3. The +5.92R healthy book is the reason to
route it in; the −12.89R trending-short sub-cell (already gated) is the reason the routing
keeps the `short: off` cell with it. **These are different actions on different populations
and must not be conflated** (regime-selectivity: an all-folds-profitable book is not gated;
only its proven-losing sub-cell is).

---

## 1.4 — ETF/equity fee-A/B residual (#8411, 17 legs)

The fixed-window fee A/B (`regime-debt-matrix-fee-ab-2026-08-02.md`, #8329,
`BL-20260730-FEE-AB-FIXED-WINDOW`) measured the per-cell fee drag for 6 strategies but not
the rest of the commission-free Alpaca ETF/equity debt roster. This run completes it: one
pass, two `--fee-bps-roundtrip` arms (0 vs 7.5), identical trade set → `Δ = net_R(7.5) −
net_R(0)` is a **measured** per-cell fee effect. **Every one of the 17 legs' cells shows a
negative delta** (a fee can only reduce R — a clean isolation, zero wrong-signed cells).

**The over-charge is concentrated in the high-frequency 1h ETF legs** (many trades × 7.5 bps):

| leg · cell | Δ net-R (0→7.5) | n (long/short) | Δ per trade |
|---|--:|--:|--:|
| `tlt_pullback_1h` trending | **−9.62** | 43 / 53 | −0.100 |
| `spy_pullback_1h` trending | **−4.55** | 31 / 25 | −0.081 |
| `qqq_pullback_1h` trending | **−4.44** | 39 / 40 | −0.056 |
| `uso_trend_1h` chop | −1.28 | 29 / 0 | −0.044 |

The daily (`*_1d`) legs move only −0.02..−0.20 net-R per cell (few trades). Per-trade drags
land in the predicted **0.04–0.12 R/trade** band throughout. `splg_trend_long_1d` returned
**no gradeable cells** — the same vacuity instance flagged in the corrected-cost §3e (a live
strategy producing ~no trades in 730d; already tracked).

### Disposition — asset-class venue-fee re-read is now **complete**

Because these are commission-free Alpaca instruments, the **correct** fee is **0 bps**, so
the over-charged evidence understated each cell by the deltas above. **Any OFF cell on the
1h ETF legs (qqq/spy/tlt) must be read at 0 bps, not the venue-blind 7.5** — a multi-R
apparent drag there is up to ~40% phantom fee at the 1h cadence. No such cell is currently
live on these legs; this is the corrected baseline for any future cell proposal on them.
Combined with #8329, the venue-fee re-read spans the full commission-free ETF/equity roster,
so `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` §4/§5-follow-up-5 (the asset-class
re-read) is now evidenced end-to-end. **No `config/regime_policy.yaml` edit is proposed.**

---

## 1.3 — R2 ADX cut-point sweep on `gld_pullback_1h` trending (#8413)

First dispatch of the R2 tool (shipped #8412). Grades the trending cell at each
`(chop_max, trend_min)` in chop∈{15,18,20,22} × trend∈{25,28,30,32} (live = 20/25),
re-bucketing the **same** 123 emitted trades at each pair (attribution axis, not entry):

- **`short_stable_drag: TRUE` at 16 of 16 gradeable cut-point pairs → short verdict ROBUST.**
- `long_stable_drag: False` at 0 of 16 → long verdict robust too.
- At the live 20/25: pooled short −13.88R@91-regime-trades, long +37.18R — matches the A1
  matrix exactly (same bucketing).

Across the grid the pooled short-R stays −13.7..−17.5 and the verdict never flips. **The
confirmed `trending.gld_pullback_1h { short: off }` cell is NOT fragile to the two un-swept
global ADX constants** — it rests on real regime structure, not the specific 20/25 choice.
This is the R2 payoff for GLD: no cut-point retune is warranted for this family (a flip
would have flagged the cell as resting on the attribution threshold). Widening `trend_min`
(28→32) narrows the trending bucket (91→60 trades) but the short drag only deepens — further
evidence the gate is picking up a genuine trending-regime short weakness. **No cell
changed.** (R2's per-family cut-point question stands open for the *other* live-celled
families — `htf_pullback_trend_2h`, `ict_scalp_5m`, the crypto donchians — each a cheap
follow-up dispatch of the same tool.)

## 3. Prepared Tier-3 packet — GLD-1h Track B routing (DRAFT, do NOT enact)

Pinged to the operator; not enacted. Two independent decisions, kept separate:

1. **Keep `trending.gld_pullback_1h { short: off }`** — *already live, confirmed this
   session on both the corrected matrix and the corrected walk-forward.* No action; recorded
   so the cell is no longer "un-re-checked evidence."
2. **Route the GLD-1h book deeper into the portfolio (M36 Track B).** Gate before any
   `alpaca_portfolio`/`alpaca_live` routing: `scripts/prop/account_compat_matrix.py
   --strategy gld_pullback_1h --symbol GLD --fee-bps-roundtrip 0` (net-of-fee performance +
   survival vs each account's ruleset) + a shadow soak + carrying the confirmed `short: off`
   cell. **This is the operator's call** — it puts a paper-money winner onto (eventually)
   live capital. Recommendation: run the compat_matrix on a free runner next, then decide.

---

## Follow-ups

| # | Item | Owner |
|---|---|---|
| 1 | `account_compat_matrix` for `gld_pullback_1h` (Track B routing gate) — free runner | this session / next |
| 2 | ✅ **DONE** — R2 ADX cut-point sweep on `gld_pullback_1h` trending (#8413): verdict robust 16/16, cell not fragile to 20/25 (§1.3). Run the same tool on the other live-celled families (`htf_pullback_trend_2h`, `ict_scalp_5m`, crypto donchians) | free runner |
| 3 | `splg_trend_long_1d` all-zeros vacuity — diagnose feed/params | open (corrected-cost §3e) |
