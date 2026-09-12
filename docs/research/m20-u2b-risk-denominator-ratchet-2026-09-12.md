# U2b — the risk denominator is amended by the break-even ratchet, and MI-277's unattributed clamp is that ratchet

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-278 U2b · `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**Cycle priority:** `CY-20260906-TRADING-TRUTH` — *repair the measurement before acting on what it says.* This unit is that, literally.
**Corrects the BASIS of:** [`winner-size-collapse-2026-09-12.md`](winner-size-collapse-2026-09-12.md) §1.2 and §3.8 (MI-277) · and [`m20-u2-winner-close-attribution-2026-09-12.md`](m20-u2-winner-close-attribution-2026-09-12.md) §1, **my own**
**Reproduce:** `python3 scripts/research/m20_u2b_ratchet_risk_basis.py --trades <t.json> --packages <p.json>`

---

## 0. The verdict, in five sentences

**`order_packages.sl` is NOT entry-frozen. The break-even ratchet amends it in place, and MI-277's "0.0015 stop-distance floor of unattributed origin" is that ratchet — `be_offset_bps: 15` is 15 bp is exactly 0.00150000 of entry.** All **65 of 65** `ict_scalp` packages whose target-to-stop ratio departs from the declared `tp_at_r: 1.5` sit at `risk/entry == 0.00150000` to eight decimals (**66** packages sit at that value in total — the 66th has a ratio of ≈1.5, i.e. an entry stop that coincidentally landed there, so the marker is highly but not perfectly specific and that is stated rather than rounded away), **93%** of their linked closed trades are winners, and the entry risk is **exactly recoverable** from the frozen `tp` (control: `|tp − entry| / 1.5` reproduces `|entry − sl|` to a median relative error of **1.4e-10** on the 233 un-ratcheted rows). Because the ratchet fires **only on trades that reached 1R**, it shrinks the risk denominator **selectively on winners** — so every R computed off `order_packages.sl` is **inflated on winners and correct on losers**, which is precisely the asymmetry the winner-vs-loser comparison rests on. **Repairing it does NOT overturn MI-277's conclusion**: on MI-277's own population the winner-R collapse goes from **−63% to −49%** — smaller, still large, still there — and the loser control is unmoved by construction. **But the LEVELS move a lot** (pre-era winner median R 3.298 → **1.445**, an inflation of 2.3×), so any figure quoted in R off that basis, including MI-277's `mean R 7.32 → 4.61`, is not the trade's realised R.

⚠️ **I first read this as inverting the collapse and it does not.** On the `ict_scalp` family alone, across all accounts, the repaired winner median R is flat (1.216 → 1.353, **+11%**) — and that is a **different population** from MI-277's `bybit_1`-all-legs. Reported below as two populations, not one headline. Carrying the +11% forward would have been the population error this repo has now paid for three times in three memos.

---

## 1. Population

| | |
|---|---|
| **Source** | `/api/diag/journal` trades + order_packages, `limit=1000`, pulled 2026-09-12T05:2xZ; ids **4716–5715** |
| **Ratchet identification** | all `ict_scalp_*` `order_packages` rows with `entry`, `sl`, `tp` and a valid direction — **n = 299** |
| **MI-277 reproduction** | its exact population: `bybit_1`, closed, non-backtest, `pnl NOT NULL`, pairs excluded, ids 4709–5708 → **181 rows** (its memo: 180; ~4h window drift) |
| **`ict_scalp` family** | closed, non-backtest, `pnl NOT NULL`, **all accounts** — 45 pre / 27 post winners |

---

## 2. The identification

`ict_scalp.order_package` builds the bracket as `sl = entry ∓ risk` and `tp = entry ± tp_at_r × risk` **from the same `risk`**, with `tp_at_r: 1.5` on every `ict_scalp_*` leg. So `(tp − entry) / |entry − sl|` must be exactly **1.5**.

Measured straight off `order_packages` — **no trade join**, so a join error cannot produce it:

| leg | n | median ratio | max ratio | declared |
|---|--:|--:|--:|--:|
| `ict_scalp_5m` | 20 | **1.500** | 14.192 | 1.5 |
| `ict_scalp_avax_5m` | 83 | **1.500** | 36.506 | 1.5 |
| `ict_scalp_eth_15m` | 37 | **1.500** | 32.623 | 1.5 |
| `ict_scalp_sol_15m` | 41 | **1.500** | 35.814 | 1.5 |
| … (all 8 legs) | | **1.500** | | 1.5 |

**The median is exactly the declared value and the tail reaches 36×.** Splitting on the tail:

| | n | median `risk/entry` | of which at `0.00150000` exactly | linked closed trades that are WINNERS |
|---|--:|--:|--:|--:|
| ratio ≈ 1.5 | 234 | 0.00790694 | **1 (0%)** | 7 of 75 (**9%**) |
| ratio > 1.51 | **65** | **0.00150000** | **65 (100%)** | 50 of 54 (**93%**) |

(66 packages sit at `0.00150000` in total; the 66th carries ratio ≈1.5 and is a coincidental entry stop, counted in the top row.)

**100% / 0% and 93% / 9%.** `be_offset_bps: 15` → `_base.monitor_breakeven_sl` returns `{"sl": entry × (1 ± 15/10000)}`, i.e. `|entry − sl| / entry = 0.0015` **exactly**, and it fires only once price reaches `one_r_threshold = 1.0`. Identification complete.

### 2.1 The entry risk is exactly recoverable, and the control proves it

`tp` is **not** amended, so `|tp − entry| / 1.5` is the entry risk.

* **Control — 233 un-ratcheted rows:** relative error median **1.382e-10**, max 3.590e-06; **231 of 233 within 1e-6**.
* **Ratcheted rows:** `|entry − sl|` understates the entry risk by a median **5.61×** (max 24.3×); recovered `risk/entry` median **0.008412** against the ratcheted 0.001500.

---

## 3. What it does to the measurements

### 3.1 MI-277's exact population — the collapse SURVIVES

`bybit_1`, 181 rows, of which **48 sit at 0.00150000** (MI-277 independently measured 48 of 180 — the same rows):

| | n | median R (MI-277 basis) | median R (repaired) |
|---|--:|--:|--:|
| pre winners | 42 | 3.298 | **1.445** |
| post winners | 34 | 1.220 | **0.738** |
| pre losers | 36 | −0.981 | **−0.981** |
| post losers | 69 | −0.970 | **−0.970** |

**Winner median R, post/pre: −63% on MI-277's basis, −49% repaired.** The loser rows are **bit-identical** under both bases — they never ratchet — which is the control that shows the difference is the ratchet and not the arithmetic.

**MI-277's §3.4 conclusion holds:** losses realise ≈1R in both eras, only the winner side moved, and it moved down. What changes is the **size** of the move and the **levels**: its pre-era winner median R of 3.263 is, in the units the trade actually risked, about **1.45**.

### 3.2 The `ict_scalp` family alone — a different population, and it does not fall

All accounts, `ict_scalp_*` only:

| | n | median R (pkg sl) | median R (repaired) |
|---|--:|--:|--:|
| pre winners | 45 | 4.386 | **1.216** |
| post winners | 27 | 1.446 | **1.353** |

**−67% on the contaminated basis, +11% repaired.** ⚠️ **This is NOT the headline and must not be quoted as one** — it is a different population from §3.1 (all accounts vs `bybit_1`; one family vs all legs) at n=27, and the two disagree. What both agree on is that the contaminated basis **overstates the fall**.

### 3.3 Mean and median disagree, and that is itself the diagnosis

On the repaired basis, the `ict_scalp` winners' **mean** R falls hard (4.119 → 0.868) while the **median** is flat. The exact identity `mean(pnl|win) = mean(risk_usd)·mean(R) + cov`, residual asserted zero inside the transform:

| basis | | mean risk_usd | mean R | cov | = mean win | R effect | size effect |
|---|---|--:|--:|--:|--:|--:|--:|
| pkg sl | pre | 182.30 | 14.992 | 3,698.32 | 6,431.39 | | |
| | post | 130.03 | 5.121 | −379.09 | 286.79 | 29.3% | 12.8% |
| **repaired** | pre | 928.94 | 4.119 | 2,605.38 | 6,431.39 | | |
| | post | 393.96 | 0.868 | −55.02 | 286.79 | **49.1%** | **35.9%** |

On **either** basis the **covariance** term dominates on this family (−4,077 and −2,660 against a −6,145 total), which is a different shape from MI-277's `bybit_1`-wide *"R is 97.4%"*. **The typical winner's R did not fall; the big winners stopped happening.** That is a materially different diagnosis from *"winners get smaller"*, and it points a remedy at the right tail rather than at the median trade.

---

## 4. Why MI-277's inference about the clamp was wrong, and it is instructive

MI-277 §3.8: *"**INFERRED** that it is a floor rather than a cap … a cap binds more as volatility rises, a floor binds less — volatility rose and the share at the value fell 34.2% → 20.8%."*

The reasoning is sound and the premise is wrong: it is **neither** a floor nor a cap, and it is not applied at entry. The share fell because **fewer trades reached 1R in the post era** — i.e. the share is a *proxy for the winner rate*, and its fall **is the collapse**, not evidence about a clamp. Marking it INFERRED is exactly what made it cheap to re-check; the mark did its job.

---

## 5. What this does NOT establish

* **That MI-277's conclusion is wrong.** It is not. The winner-R collapse survives at −49% on its own population, its dollar headline (`$1,020.79 → $283.08`) never depended on the denominator at all, and its loser control is untouched.
* **That the `+11%` `ict_scalp` reading generalises.** It is one family, all accounts, n=27 post. §3.1 and §3.2 disagree and both are reported.
* **That non-`ict_scalp` legs are unaffected.** The recovery uses `tp_at_r = 1.5`, which is `ict_scalp`-specific. `vwap` and `turtle_soup` also call `monitor_breakeven_sl` (U1 asserted this) and are **not** graded here; `trend_donchian` and `htf_pullback_trend_2h` do **not** call it. Whether the ratchet writes back to `order_packages.sl` for the other two callers is **not measured** — their legs did not appear in this window's ratcheted set, which is not the same as their being immune.
* **The mechanism of the write-back.** I established *that* `order_packages.sl` carries the ratcheted value, not *which* code path persists it. That is a source question this unit did not chase.
* **That `trades.stop_loss` is safe instead.** It is the trailed stop and is worse (MI-277 §1.2). The repaired `tp`-derived risk is the only basis here that is entry-time.

---

## 6. Consequence for U3, and for anyone quoting an R

**Any R figure in this workstream computed off `order_packages.sl` is inflated on winners.** That includes MI-277 §3.1/§3.4's R levels, my own U2 §4 achieved-vs-declared-`tp_r` ratios, and the exit-refinement matrix if any cell was graded from live rows on that basis. The repair is one line — `|tp − entry| / tp_at_r` for `ict_scalp` — and it is now in `scripts/research/m20_u2b_ratchet_risk_basis.py` with its control.

**U3 must run on the repaired basis**, and the §3.3 result changes what U3 should test: not the median winner, but the **right tail** — which is a target-geometry and trail question, and is what U3 was already reshaped toward.

## 7. Rows filed

| id | register |
|---|---|
| `BL-20260912-ORDER-PACKAGES-SL-IS-AMENDED-BY-THE-BREAK-EVEN-RATCHET-SO-EVERY-R-OFF-IT-IS-INFLATED-ON-WINNERS` | health |
| `BL-20260912-MI-277-S-UNATTRIBUTED-0-0015-CLAMP-IS-THE-BREAK-EVEN-RATCHET-AND-ITS-FLOOR-INFERENCE-IS-WRONG` | performance |
