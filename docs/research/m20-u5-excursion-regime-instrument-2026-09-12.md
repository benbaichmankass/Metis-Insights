# U5 — the favourable:adverse excursion instrument, in ATR units, validated on a known event

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Unit:** MI-278 U5 · `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**Closes:** `BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES` (filed by MI-275, unowned) — **the computation half. The cadence half is a decision, and it is put rather than taken.**
**Reproduce:** `python3 scripts/research/m20_u5_excursion_regime.py --packages <p.json> --trades <t.json> --interval 15 --legs <csv> --validate`

---

## 0. The verdict, in four sentences

**The instrument exists, and it reproduces the known 2026-08-30 event at 4 of 4 windows.** On MI-275's own population its favourable:adverse ratio falls **2.889 → 0.548** at 48 h (MI-275: 4.585 → 0.542), with MFE matching closely at every window (48 h pre **6.564** against its **6.690**) and the post-era ratio matching to within 1%. It needs **no exit price, no PnL provenance, no stop and no knowledge of which lever closed the trade** — which matters more now than when the row was filed, because U2b established that **R is contaminated too** (the break-even ratchet amends `order_packages.sl` on winners only), so ATR units are the only basis in this workstream immune to both defects. **I am not shipping a cadence**: the backlog row warns that *"two cadences landing in the same window is how they come to disagree"* with MI-276's now-merged `losing_streak_alert`, and choosing between the carriers is a decision for the operator, put in §5.

---

## 1. Population, and the population error I made first

| | |
|---|---|
| **Packages** | `/api/diag/journal?table=order_packages&limit=1000`, pulled 2026-09-12T05:2xZ |
| **Restricted to** | MI-271's **decision population** — closed, non-backtest, `pnl NOT NULL`, pairs excluded — joined by `order_package_id`; **216** package ids |
| **Validation legs** | the 7 crypto e35 legs carrying an `atr_stop_mult` change (MI-275's own restriction), derived by grepping the `e35` comment in `config/strategies.yaml` rather than retyped |
| **ATR** | `order_packages.meta.atr`, the **entry-frozen** ATR. Coverage is **100%** on every non-pairs leg (671 of 1000 packages carry it; all 329 without are the pairs sleeve) |
| **Candles** | `data.binance.vision` at 15 m, via the existing `fetch_backtest_candles.fetch_klines_binance_vision` |

⚠️ **I graded ALL packages first and it silently changed the answer.** Without the trades join the pre-era 48 h F:A read **0.865 on n=49**; with it, **2.889 on n=17** against MI-275's 4.585 on n=18. Rejected and never-opened packages have no trade behind them, and including them is a **different** population, not a wider one. The script now **refuses to be quiet about it**: `--trades` is optional, and omitting it prints a warning saying exactly this.

### 1.1 Costing the fetch, because the row demanded it before any cadence is promised

* **`api.bybit.com` is unreachable from these containers** — measured: HTTP **403**, *"the Amazon CloudFront distribution is configured to block access from your country."* `fetch_backtest_candles.py` already knows this for the runner path and falls back.
* **`data.binance.vision` works** — measured: HTTP **200**, 5,217 bytes for one ETHUSDT 15 m day, 0.82 s.
* **Cost of the validation run:** 6 symbols, ~2,100 candles each, well under a minute. A weekly per-leg run over the crypto book is **minutes on a free runner**, not a trainer job.
* ⚠️ **Crypto perps only.** The equity/futures legs (`GLD`, `SPY`, `MGC`, `TLT`, …) are not on Binance Vision and are **not covered**; `fetch_backtest_candles.py` has `yfinance`/`dukascopy` sources that were **not exercised here**. Saying "per leg per week" without that carve-out would be a coverage claim I have not earned.

---

## 2. The method, taken from MI-275 §8 rather than invented

Excursion is measured over a **fixed number of hours from entry** — 4 / 12 / 24 / 48 — because measuring over each trade's own **lifetime** manufactures the finding: a tighter stop closes a trade sooner, and a shorter observation window mechanically lowers MFE and caps MAE. The fixed window depends on neither the geometry, nor when the trade closed, nor which lever closed it. **The lifetime variant is not offered at all.**

A window that runs past the end of the candle data returns **nothing**, rather than a partly-covered reading — a partial window understates both excursions and would be the same confound by another route.

---

## 3. Validation — the known event

`--validate` grades 2026-08-30 and **refuses a clean bill** if the move is not reproduced (exit 1 when no window moves, exit 2 when validation could not run at all).

| window | pre n | pre F:A | post n | post F:A | | MI-275 §8 |
|---|--:|--:|--:|--:|---|---|
| 4 h | 17 | 0.384 | 14 | **0.199** | ✓ | 1.037 → 0.406 (18/15) |
| 12 h | 17 | 0.804 | 11 | **0.219** | ✓ | 1.756 → 0.327 (18/14) |
| 24 h | 17 | 1.240 | 11 | **0.329** | ✓ | 2.969 → 0.414 (18/14) |
| 48 h | 17 | 2.889 | 10 | **0.548** | ✓ | 4.585 → 0.542 (18/11) |

**4 of 4 windows move down.** The n's line up (17/14 vs 18/15 etc. — my pull is ~4 h later), the **MFE** figures track closely (48 h pre **6.564** vs **6.690**; post **1.401** vs **1.283**), and the **post-era F:A matches to within 1%** (0.548 vs 0.542).

⚠️ **One delta I could not resolve and am not smoothing over: my pre-era MAE reads HIGHER than MI-275's** (48 h **2.272** vs **1.459**), which is what makes my pre-era F:A lower (2.889 vs 4.585). MFE agrees and MAE does not, so it is a difference in how the adverse side is bounded — plausibly the window anchor (`meta.entry_time`, the signal bar, vs `created_at`) or a cap at the trade's close. **The direction, the post-era level and the magnitude class all reproduce; the pre-era MAE does not, and that is stated rather than averaged away.**

---

## 4. What the instrument shows beyond the validation

Run on the **`ict_scalp` 15 m legs** — the family MI-277 measures as carrying two-thirds of the loss, and one e35 never touched:

| window | pre F:A (n=65) | post F:A | |
|---|--:|--:|---|
| 4 h | 0.886 | 0.787 (29) | ✓ down |
| 12 h | 0.540 | 0.489 (29) | ✓ down |
| 24 h | 0.551 | 0.544 (25) | ✓ down |
| 48 h | 1.004 | 1.022 (23) | ✗ **up** |

**The excursion collapse is an e35-leg phenomenon far more than a scalp-family one.** On the scalp legs the move is slight at 4–24 h and **absent at 48 h**, against a 5.3× fall at 48 h on the e35 legs. That is a real difference between two families over the same calendar window, and it is **not** what MI-275's *"reaches legs across the book"* framing would predict.

⚠️ Read the n before the ratio, and note the ATR denominators are not comparable across families: a 15 m ATR is far smaller than a 4 h ATR, so the scalp legs' excursions are larger in ATR units at every window. **Compare a leg to itself over time, never one leg to another.**

---

## 5. The cadence is a DECISION, and I am putting it rather than taking it

The backlog row's `resolution_criteria` is *"computed per leg per week on a durable surface **and a session is OBSERVED reading it**"*, with an explicit warning: **do not build it on this row alone without checking MI-276**, which owns the losing-streak detector for the same incident, *"because two cadences landing in the same window is how they come to disagree."*

**Checked: MI-276's detectors are MERGED** — `src/runtime/losing_streak_alert.py` and `src/runtime/starved_account_alert.py` are on `main`. (⚠️ Its work object still reads `lifecycle: dormant` with `owner: null`, which is register lag, not my finding to fix.) They are **complements**: a streak detector says **THAT** the book is bleeding; this says **WHY**, and needs no provenance-clean PnL — which is exactly what made the streak question hard.

So the open question is the carrier, and there are three, with different failure modes:

| carrier | pro | con |
|---|---|---|
| **a scheduled workflow** (free runner, fetches candles) | $0, no VM, already the proven path for candle work | this repo has **many** open items of the form *"the cron has never fired"* — a schedule is a weak carrier here |
| **a VM systemd timer** | the VM's clock demonstrably fires | the VM has the candles but this is a Tier-2 deploy, and the trainer/live split matters |
| **fold into an existing review** (`/performance-review`) | a session reads it by construction, which is the row's actual criterion | weekly at best, and reviews already carry a lot |

**I am not choosing.** Picking one silently is how a second cadence lands beside MI-276's and they drift. **Routed to the manager as a Tier-1 decision** with the cost measured (§1.1) and the computation done.

---

## 6. What this does NOT establish

* **That the ratio is a leading indicator.** The row calls it one; this unit shows it *moved on a known event*, which is necessary and not sufficient. Leading-ness needs it to move **before** a PnL surface does, on an event it was not derived from — and this window is the event it *was* derived from.
* **Any equity or futures leg.** Crypto perps only (§1.1).
* **A per-leg-per-week reading at usable n.** At 24 h most leg-weeks have **n = 3–15**; the table in §4 is per-family, not per-leg-week, for that reason. A weekly per-leg cadence will often be too thin to grade, and the carrier decision should account for it.
* **That my pre-era MAE is right and MI-275's is wrong**, or the reverse. The delta is unresolved (§3).
* **That anything is wired.** Per the `exit-refinement` definition of done: there is a **runner** (`# wiring: manual-only`, declared in the file), a **consumer** (this memo, and `--validate`'s refusal), and **observation on real data** (§3, §4) — but **no detector**, because no cadence has been chosen. Stated, not implied.

## 7. Rows

`BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES` — **left OPEN, deliberately.** Its criterion has two halves and this closes one: the computation exists and is validated, but *"a session is OBSERVED reading it"* cannot be satisfied by the session that wrote it. An `observation` is appended recording what was built, what it measured, and the carrier decision that is now the only thing outstanding.
