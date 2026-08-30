# e35 coverage-matrix re-check against the 2026-08-29 corpus

**Date:** 2026-08-29 · **Tier 1** (records + tooling; no `src/`, no `config/`, no order path)
**Tool:** [`scripts/research/e35_matrix_recheck.py`](../../scripts/research/e35_matrix_recheck.py)
**Corpus:** `docs/research/e35-bracket-corpus.jsonl` @ `bd08cecf` — 8,289 rows / 8,289 unique
`measurement_key` / 0 duplicates. Runs present: 08-23 (52) · 08-24 (6) · 08-26 (72) · **08-29 (8,159)**.

---

## 1. The ruling this rests on, stated before anything else

The 08-29 re-sweep's **CLEAN-leg control FAILED as specified** — 3,515 of 4,583 clean-leg
cells differ across the two corpus revisions (76.7%), 18 of 23 legs
([`e35-resweep-verdict-diff-2026-08-29.md`](e35-resweep-verdict-diff-2026-08-29.md)). The
diagnosis is that the control is **unsatisfiable** (the sweep fetches a trailing
`days=1830` window ending at run time, so both data edges move), evidenced at the fold
level over **11 of those 3,515 cells**.

**The operator accepted that fold-level substitute on 2026-08-29 and authorised this
re-check.** Every verdict below inherits that caveat. It was a ruling on *evidence*, not
an approval of any config change, and **nothing here changes `config/strategies.yaml`.**

---

## 2. The stale-ref count is 30, not 19 — and my earlier figure is not reproducible

The prior session recorded *"19 stale-ref matrix cells"*. Measured now with a stated
definition — *the corpus holds a run newer than any date the cell's `ref` cites* — the
count is **30 of 40 legs carrying corpus rows**. Of those, **18 cite 2026-08-20** and 12
cite 08-26.

I cannot reproduce 19 from what was recorded, and the derivation was not written down.
Rather than reconcile a number to a figure whose method is lost, the definition above is
now executable (`e35_matrix_recheck.py`) and **the count is 0 after this change**. Treat
"19" as superseded, not as a discrepancy to explain.

⚠️ Staleness is **three-state**, not two: a ref with no date at all, or a leg with no
corpus rows, is **undecidable** — reported as such, never as "current". **12 matrix legs
carry no corpus rows** (the 8 `ict_scalp*` legs, `fvg_range_15m`, `tqqq`/`qld_trend_long_1d`,
and the un-exploded shadow-fleet row). They are **ungraded, not clean.**

---

## 3. B4 is OUTCOME-CONFIRMED, and the check that confirms it is the one that looked alarming

**The alarming reading first:** all 8 legs PR #10419 shipped to live real money show
**zero passing cells** on the 08-29 run. Read naively that says the shipped geometry
stopped clearing the gate.

**It is the opposite, and it is checkable.** The sweep's baseline is config-exact, so once
geometry is DECLARED the base *is* that geometry and the winning cell can no longer beat
itself. The falsifiable form: **did the base rise by the improvement the shipped cell
claimed?**

| leg | shipped cell | claimed `d_net_r` | base rise 08-26 → 08-29 | ratio |
|---|---|---:|---:|---:|
| `mgc_pullback_1d` | `tp6_sm1.5` | 3.8246 | **+3.8246** | **1.00** |
| `qqq_trend_long_1d` | `tp3_sm2` | 6.1948 | **+6.1948** | **1.00** |
| `uso_trend_1h` | `tp4_sm2` | 23.6075 | **+23.6075** | **1.00** |
| `spy_trend_long_1d` | `tp2_sm1.5` | 8.1565 | +8.3934 | 1.03 |
| `tlt_pullback_1h` | `sm2` | 11.5134 | +11.8293 | 1.03 |
| `slv_trend_1h` | `sm1.5` | 46.1961 | +47.2756 | 1.02 |
| `scha_trend_long_1d` | `tp1.5_sm3` | 3.9995 | +3.7568 | 0.94 |
| `iwm_trend_long_1d` | `tp3_sm2` | 4.0387 | +3.5887 | 0.89 |

**Three of eight reproduce to four decimal places exactly**; all 8 land 0.89–1.03×, median
**1.00**. The five inexact ones differ by ±11%, which is a useful independent read on the
magnitude of the window drift that broke § 1's control.

⚠️ **STATE THE CONTROL'S DENOMINATOR.** Only **10** legs carry both runs. The 2 non-B4 legs
among them — `mgc_trend_1h` and `xauusd_trend_1h` — are **byte-identical across all 199
cells**, because `PROXY_DATA` maps *both* `MGC` and `XAUUSD` to the same `GC_F` series and
both declare `tp_r 50.0` / `atr_stop_mult 2.5`. They are **one observation wearing two leg
names**. So the control is **n=1 independent leg**, and it moved **−5.02** while all 8 B4
legs moved up. Unanimous 8/8 in the opposite direction to the control is the evidence; a
2-leg control would have been double-counting.

The MGC/XAUUSD identity is **deliberate and correct** (the proxy is the deeper series —
2,512 rows vs 940 native). It is recorded in the matrix's `known_caveats` because it
affects *reading*, not correctness.

---

## 4. The two cells that started this are worse than stale — they are unshippable

`eth_pullback_2h` and `eth_pullback_prop_2h` carried `passed_unshipped` on winners
**`tp2_sm3.5_to48`** and **`tp4_sm3_to48`**, both claimed at wf 6/6 effective on 2026-08-20.

Both carry a **`to48` timeout component**. No live trend/pullback/squeeze unit implements a
bar-count exit (`BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`),
so **no config change can ever deliver either cell.** That is decisive on its own and does
not depend on the re-measurement at all. Both are now
`blocked:no_live_bar_count_exit` — the disposition `mes_trend_long_1d` and
`tlt_pullback_1d` already carried.

Independently, the re-measurement moved both to **negative** surface improvement:
`tp2_sm3.5_to48` → **−8.4574**, `tp4_sm3_to48` → **−0.6569**. Neither leg has any shippable
passing cell on the newest run.

⚠️ **"NOT REPRODUCED" IS NOT "REFUTED".** Only the per-axis/joint argmax cells are carried
into the gate on a given run, and neither of these was an argmax on 08-29 — so there is **no
fresh walk-forward verdict** to set against the old 6/6. The honest claim is that the
surface improvement went negative and the winner was not reproduced; calling it a failed
walk-forward would assert a measurement nobody took.

**Two more of the same shape were found that the earlier note missed** — `gld_pullback_1h`
(`tp6_sm1.5_to24`) and `spy_pullback_1h` (`sm1.5_to400`) also named timeout-carrying
winners. Both **do** have shippable winners now, so they stay `passed_unshipped` with the
named winner corrected to `sm1.5` (wf 5, +46.4777 and +28.1569 respectively). Quoting an
unreachable geometry as the passing cell made a dead end read as a pending ship.

---

## 5. Direction reversed on 10 legs — and this is exactly what B9 predicted

Ten legs the matrix recorded as `honest_negative` (nine) or `blocked:no_dispersion_band`
(one) now have a **shippable** cell clearing the m20 gate:

| leg | was | shippable passing cell(s) on 08-29 |
|---|---|---|
| `trend_donchian_eth_4h` | honest_negative | `sm2` (wf 6, +28.7346) |
| `trend_donchian_sol_4h` | blocked:no_dispersion_band | `sm1.5` (wf 5, +27.7150) · `sm2` (wf 4, +18.2840) |
| `trend_donchian` | honest_negative | `sm2` (wf 5, +21.7770) |
| `htf_pullback_trend_2h` | honest_negative | `sm3` (wf 4, +20.2310) · `sm3.5` (wf 4, +14.3870) |
| `trend_donchian_eth_prop` | honest_negative | `sm1.5` (wf 5, +13.4200) · `tp6_sm1.5` (wf 5, +13.4200) |
| `ada_pullback_2h` | honest_negative | `tp4` (wf 4, +12.8230) · `tp4_sm1.5` (wf 4, +12.8230) |
| `trend_donchian_avax_4h` | honest_negative | `sm1.5` (wf 5, +12.4090) |
| `trend_donchian_ada_4h` | honest_negative | `sm2` (wf 5, +11.2510) |
| `trend_donchian_xrp_4h` | honest_negative | `tp3_sm2` (wf 5, +5.2070) · `sm2` (wf 4, +3.0030) |
| `avax_pullback_2h` | honest_negative | `sm2` (wf 4, +1.0080) |

**This is the B9 mechanism, not a surprise.** Every prior verdict was measured on a harness
that force-closed each trade at `timeout_bars` (default 200) while live implements no time
stop at all. The re-sweep measures against production's real (absent) bar-count exit, so a
leg whose edge was being truncated by a fictional exit can legitimately turn positive. It is
also precisely why reading these legs *before* the re-sweep would have been the error B9
exists to prevent.

Timeout-carrying passing cells are **dropped from every row above and never totalled with
the rest** — they are evidence about the harness, not shippable candidates.

⚠️ **The earlier note's "9 legs, `trend_donchian_sol_4h` holds 7" must not be re-quoted.**
That was measured on the *contaminated* pre-re-sweep corpus, on exactly the legs the
re-sweep re-measured. It is 10 legs, and `trend_donchian_sol_4h` holds **2** shippable (4
passing, 2 of them timeout-carrying).

**`passed_unshipped` records a measurement. It is not a recommendation to ship.** Declaring
any of this geometry is **Tier-3** and operator-gated; § 7 states what a proposal would need.

---

## 6. What changed in the matrix

| | before | after |
|---|---:|---:|
| stale refs | 30 | **0** |
| `passed_unshipped` cells naming an unshippable winner | 4 | **0** |
| `honest_negative` | 24 | 14 |
| `passed_unshipped` | 4 | 12 |
| `blocked:no_live_bar_count_exit` | 2 | 4 |
| `shipped` | 8 | 8 (untouched) |

The 16 legs whose verdict the new run **confirms** also had their refs refreshed —
a ref citing a three-generations-old run is a stale record even when its verdict still
holds, and "CONFIRMED" is written explicitly as *a fresh negative*, not a carried-forward
one. `matrix-bracket-values` passes throughout; no `shipped` cell was touched.

Two `known_caveats` were added: the MGC/XAUUSD one-measurement caveat, and the
"a shipped leg shows zero passing cells BY DESIGN" caveat with the `--b4-outcome` check
that distinguishes it from a regression.

---

## 7. Open, and explicitly not taken

1. **The 15 shippable passing cells across 10 legs are a Tier-3 proposal set, not applied.**
   A proposal needs, per leg: the selection rule stated in advance (B4 used highest
   `wf_wins_effective`, tie-break `d_net_r`), the cell traced to its corpus row, and the
   §1 caveat carried. `avax_pullback_2h` at +1.0080 is a candidate to *drop*, not ship —
   it clears the gate on a margin that will not survive costs.
2. **The §1 control still needs re-specifying** — pin the window (`--since`/`--until`, or
   record the resolved candle span per row) so a re-run is a pure function of code + data.
   Until then no sweep can be reproduced across days, and every future plan asserting
   identity will raise the same false alarm.
3. **`scripts/ci/check_matrix_bracket_values.py` reads only the backticked cell-id
   spelling.** All 8 `shipped` refs use backticks so there is no live gap, and an unreadable
   ref FAILS loudly rather than passing vacuously — but 8 non-shipped refs are bold-only, so
   a future ship of one of those would hit a confusing false CI failure. Filed, not fixed
   here: a guard change belongs in its own PR with its own test.

---

*Populations: 52 matrix rows, 40 with corpus coverage, 12 without. Corpus 8,289 rows across
41 legs. B4 outcome over the 8 shipped legs with both an 08-26 and an 08-29 run; control
n=1 independent leg. Reproduce with `python3 scripts/research/e35_matrix_recheck.py` and
`--b4-outcome`.*
