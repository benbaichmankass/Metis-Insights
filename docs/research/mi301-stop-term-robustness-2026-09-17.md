# MI-301 — the 2026-08-30 attribution's market half is established; its e35-on-top half is not decidable on the population that is still reachable, and the cell it rests on is shrinking

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-301** · RESEARCH lane · Tier-1 · session `session_014ZKXKoPUbHgANGdDmRRjHz`

Instrument: [`scripts/research/mi301_stop_term_robustness.py`](../../scripts/research/mi301_stop_term_robustness.py)
— 31 self-test controls · 19 pytest controls in
[`tests/test_mi301_stop_term_robustness.py`](../../tests/test_mi301_stop_term_robustness.py),
**RUN** (`19 passed`), not assumed.

---

## 0. What was already established, and is NOT re-derived here

This unit was dispatched to run "the discriminating measurement: per-leg stop-out
rate and MFE-at-stop, e35 legs vs NON-e35 legs, before vs after 2026-08-30".

**That measurement has already been run.** [PR #12205](https://github.com/benbaichmankass/Metis-Insights/pull/12205)
(MI-278 U41, 2026-09-13) ran it and verdicted **`both_contribute`**, with a stated
population and a robustness gate on its own excursion term. ⚠️ **It is OPEN,
UNMERGED and `mergeable_state: dirty`.** Measured on the branch: the conflicts are
on **six shared registers** (`CLAUDE.md`, `docs/DOCUMENT-INDEX.md`,
`OPEN-ITEMS.json`, `health-review-backlog.json`,
`WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-*`, `RESEARCH-CAPABILITY-INDEX.md`) and
on **no code at all** — the 559-line instrument, its 224-line test file and the
memo are clean additions. Re-deriving its verdict would be the duplicated work
this lane keeps paying for, so this unit does not.

`m20-u15-stop-integrity-both-arms-2026-09-12.md` (on `main`) established that the
declared-only stop-rate DiD ranges **4.3pp to 28.2pp on identical data** across two
analyst choices, and that **the sign is positive under all six combinations**. That
range is established and is not re-derived either.

## 1. The verdict

**The excursion (market) half stands. The stop-out (e35-on-top) half is
`cannot_discriminate` on the population reachable today, and its sign is
`knife_edge`.**

`both_contribute` is not refuted. It is **under-evidenced in one of its two terms**,
and the distinction matters because that term is the whole difference between
`both_contribute` ("e35 geometry is implicated on top of the market") and
`market_regime` ("the market moved") — i.e. between a Tier-3 revert being on the
table and not.

⚠️ **THIS IS NOT "it was the market."** `OI-20260911` forecloses that answer by
name without a non-e35 control, and the control here is *fine* (n=47/103,
gradeable). A fragile sign forecloses **both** answers on this term; it does not
choose between them.

## 2. Why the term is decidable only by its sign, and why that is the exposure

Read in the source, not inferred:
`m20_u41_break_attribution_verdict.verdict()` branches on

```python
stop_rose = stop_did_pp is not None and stop_did_pp > 0
```

and `stop_did_pp` is **not computed by that instrument**. It enters as a hand-typed
`--stop-did-pp 11.6` CLI scalar, sourced from a *different* unit
(`stop_integrity_both_arms`). Its population and its provenance mix appear nowhere
in the artifact that consumes it. U15 tested that sign against **analyst choices**.
Nobody had tested it against **sampling** — and U15 also records that the treated
pre-era baseline holds **2 stop-outs in total**. Those are different questions: a
number can be stable across every way of slicing it and still be one observation
from changing sign.

## 3. Population, stated

| | |
|---|---|
| **Source** | `/api/diag/journal` `trades` + `order_packages`, `limit=1000` each, pulled 2026-09-17 |
| **Trade ids** | **4887–5886** (#12205 used 4728–5727) |
| **Population rule / split** | `bleed_attribution_2026_09_11.population`, split on `created_at` vs `2026-08-30T08:53:19+00:00` — both **imported**, not restated |
| **Split basis** | `created_at` (OPEN time) — a leg carries the geometry it was opened under |
| **Graded units** | 311 `BA.population` rows → e35 **8 pre / 20 post** packages; `untouched_control` **47 pre / 103 post** |
| **Skipped** | **0 in both arms** — every package graded |
| **Declared floors** | cell ≥ 8 **and** stop-outs ≥ 5. Basis stated, not tuned: U15 calls a DiD on a 2-observation baseline "a direction, not an estimate". The binding quantity is the **numerator** — 2/8 and 4/44 are equally undecided — so both floors are declared and a test pins that the numerator floor binds independently |

⚠️ **`/api/diag/journal` cannot be paginated.** It takes `table` + `limit` only —
**no `offset` parameter exists and no date filter** — so `limit=2000` clamps to 1000
and an `offset` is silently ignored, exactly as MI-298 measured. The Data Explorer
(`/api/bot/db/table/*`) *does* carry a real SQL `offset` (max 500/page) but returns
`{"detail":{"error":"invalid_session"}}` to this lane's bearer. So the newest 1000
rows are the whole reachable population.

## 4. Provenance mix, beside every figure

Classified through **`src/runtime/provenance.py`** — the canonical module, never a
bespoke predicate — on the row `adjudicate_exit` actually **graded** (`trade_ids[0]`,
already sorted by `closed_at`), because a package's siblings can carry different exit
provenance and the term is computed from the graded row.

| arm | era | total | measured | estimated | **fabricated** | unverified | coverage |
|---|---|---|---|---|---|---|---|
| `e35` | pre | 8 | 1 | 7 | **0** | 0 | **0.125** |
| `e35` | post | 20 | 7 | 13 | **0** | 0 | 0.350 |
| `untouched_control` | pre | 47 | 21 | 26 | **0** | 0 | 0.4468 |
| `untouched_control` | post | 103 | 73 | 30 | **0** | 0 | **0.7087** |

**The fabrication concern is REFUTED, and that is a positive finding.** I expected
fabricated exit prices to corrupt this term — `adjudicate_exit` decides
`reached_stop` by comparing **`row["exit_price"]`** to the stop, and a
mark-substituted exit price compared to a stop level is a coin toss wearing a
verdict. There are **zero FABRICATED rows in any of the four cells**. The raw
sources are `candle_at_close`, `exchange_fill`, `bybit_closed_pnl`, `exchange`,
`ib_execution`, `recorded_exit_price`, `verdict` — every untrusted one is
**ESTIMATED**, none FABRICATED. The 2026-07-30 exit-anchoring fix and the
2026-08-24 `recorded_exit_price` reclassification removed the class from this
population. `notes_unparseable` is **0** in all four cells, so no row's provenance
was lost to a truncated `notes` blob.

⚠️ **The coverage ASYMMETRY is real and large, and it is measured-vs-ESTIMATED, not
measured-vs-fabricated.** e35 pre is **12.5%** MEASURED against the control post's
**70.9%** — a **5.7×** spread, same direction as the 6× `OI-20260911` records on its
own (different) population. So the treated arm's baseline is the least
broker-attested cell in the study, and it is also the smallest.

⚠️ **Two published population filters disagree, and one is mislabelled.**
`bleed_attribution_2026_09_11.population` — the one that feeds this term — filters
**no provenance at all**. `e35_break_attribution.population` filters exactly one
`exit_reason` value (`netting_attributed`) and names that census bucket
**`fabricated_close`**, which reads as "the fabricated rows are gone" and is not what
it tests. On this population it happens to cost nothing (there is nothing to
remove), so this is a latent label defect, not a live error — filed, not fixed.

## 5. The two bases, graded

Both use the row selection `stop_integrity_both_arms` actually implements
(**earliest close**), so they are directly comparable to U15's `earliest close` row.

**`all_stop_outs`**

| arm | era | stops/n | rate | 95% CI (Wilson) | state |
|---|---|---|---|---|---|
| `e35` | pre | 2/8 | 25.00 | **(7.15, 59.07)** | `insufficient_n` |
| `e35` | post | 11/20 | 55.00 | (34.21, 74.18) | `measured` |
| `untouched_control` | pre | 22/47 | 46.81 | (33.33, 60.77) | `measured` |
| `untouched_control` | post | 59/103 | 57.28 | (47.64, 66.40) | `measured` |

DiD **+19.53pp** (treated +30.00, control +10.47) · state `ungradeable_cell` ·
**sign flips at +2** stop-outs · fragility `fragile`.

**`declared_only`** — the basis #12205's headline claims to use

| arm | era | stops/n | rate | 95% CI (Wilson) | state |
|---|---|---|---|---|---|
| `e35` | pre | 2/8 | 25.00 | **(7.15, 59.07)** | `insufficient_n` |
| `e35` | post | 9/20 | 45.00 | (25.82, 65.79) | `measured` |
| `untouched_control` | pre | 15/47 | 31.91 | (20.40, 46.17) | `measured` |
| `untouched_control` | post | 48/103 | 46.60 | (37.26, 56.18) | `measured` |

DiD **+5.31pp** (treated +20.00, control +14.69) · state `ungradeable_cell` ·
**sign flips at +1** stop-out · fragility **`knife_edge`** · TERM
**`cannot_discriminate`**.

**U15 declined to compute the interval, saying that at n=2 it "would span
everything". Computing it is strictly better than asserting it:** the treated pre
interval is **(7.15, 59.07)** and it **contains the control's pre rate on both
bases** (46.81 and 31.91). The two arms are not distinguishable in the pre era at
all — which is the baseline the entire difference-in-differences is measured from.

## 6. Three robustness axes, and the term fails the two nobody had tested

| axis | tested by | result |
|---|---|---|
| **Analyst choice** (declared/amended split × which fan-out row) | U15, established | sign positive under **all 6** combinations — **robust** |
| **Sampling** (how many observations to flip it) | **MI-301, new** | **ONE** additional declared stop-out in the treated pre cell flips the sign — `knife_edge` |
| **Repull** (the same basis on a later pull) | **MI-301, new** | **not stable** — see below |

**Repull instability, same basis and same row selection:**

| unit | pull | trade ids | e35 pre | e35 post | DiD all | DiD declared-only |
|---|---|---|---|---|---|---|
| U15 | 2026-09-12 | ~4728–5727 | **18** | 17 | 28.2 | **13.2** |
| #12205 | 2026-09-13 | 4728–5727 | not stated | not stated | not stated | **11.57** |
| MI-301 | 2026-09-17 | 4887–5886 | **8** | 20 | 19.53 | **5.31** |

⚠️ **These are three overlapping populations, not a time series of one quantity** —
do not read the 13.2 → 11.57 → 5.31 as a trend in the market. It is what a figure
resting on an 8-package cell does when the window rolls. But the direction of the
*cause* is not ambiguous: **the e35 pre cell fell 18 → 8 packages (−55.6%) while the
post cell grew 17 → 20**, which is precisely the pull-width trap #12205's own memo
documents — rolling the 1000-row window forward strips the **pre** era
preferentially, because ids are monotonic in time.

⚠️ **This is NOT the U15 reproduction discrepancy U21 found and U22 closed.** U21
observed U15's own code returning `control_pre` 30 where U15 published 31, and U22
established that the published 31 is exactly that cell's row-selection **band-high**,
so it "needs no data change to explain". That one is resolved and is a different
thing. What is measured here is a **55.6% reduction in the treated pre cell** driven
by the id window rolling forward — 18 packages to 8 — which no band artifact
accounts for.

**So the evidence for this Tier-3 question is decaying, and no reachable route
recovers it** (§ 3). Every future re-run of this measurement will have a smaller
treated baseline than the last.

## 7. Per-leg: the row's demand is not answerable, 13 of 13 cells

`OI-20260911` asks for **per-leg** stop-out rate. Every published artifact reports
per-**arm**, and a per-arm number can be one leg. It is:

**e35, pre era** — the entire treated baseline

| leg | symbol | n | stops | state |
|---|---|---|---|---|
| `ada_pullback_2h` | ADAUSDT | 2 | **2** | `insufficient_n` |
| `trend_donchian` | BTCUSDT | 1 | 0 | `insufficient_n` |
| `trend_donchian_ada_4h` | ADAUSDT | 1 | 0 | `insufficient_n` |
| `trend_donchian_avax_4h` | AVAXUSDT | 1 | 0 | `insufficient_n` |
| `trend_donchian_eth_4h` | ETHUSDT | 1 | 0 | `insufficient_n` |
| `trend_donchian_sol_4h` | SOLUSDT | 2 | 0 | `insufficient_n` |

**Both of the two stop-outs in the treated pre-era baseline are the same leg on the
same symbol** (`ada_pullback_2h`/ADAUSDT, both at the entry-declared stop). The
other 6 packages across 5 legs produced none.

**e35, post era**: `ada_pullback_2h` 2/4 · `trend_donchian` 1/2 ·
`trend_donchian_ada_4h` 1/2 · `trend_donchian_avax_4h` 2/5 ·
`trend_donchian_eth_4h` 2/3 · `trend_donchian_sol_4h` 0/3 ·
`trend_donchian_xrp_4h` 1/1.

**All 13 e35 leg×era cells grade `insufficient_n`; the largest holds 5 packages.**
Not one e35 leg has a gradeable stop-out rate in either era. The per-leg half of
`OI-20260911`'s criterion is **not answerable on this population**, and saying so
with the denominator is the honest close.

## 8. What this changes, and what it does not

**It does not overturn `both_contribute`** and #12205 should still land — its
excursion term is provenance-immune by construction (verified in source: candles
over a fixed window from entry, no exit price, no stop, no `pnl`), its control arm
is gradeable, and a market-wide deterioration in an arm e35 never touched is a real
finding. **The market half is established.**

**It does change what may be said about the other half.** `both_contribute` reads as
two established terms. One is established; the other is a **direction on a
2-observation baseline whose sign turns on one row**, and U15 already said in terms
that it "is not a number to make a Tier-3 decision on". #12205's memo does not carry
that caveat forward — it states `+11.6pp` as a magnitude with neither the cell sizes
nor the 4.3–28.2pp family it comes from.

**Recommendation (no change proposed; geometry is Tier-3 and the operator's):**

1. **Do not revert e35 on this evidence.** Not because the market half exonerates it
   — it does not — but because the term that implicates it cannot be distinguished
   from zero on any reachable population, and reverting would leave the control arm
   and `ict_scalp_*` (which e35 never touched) unexplained, exactly as
   `OI-20260911` warns.
2. **Land #12205** — the conflicts are six registers and no code, and its instrument
   plus verdict are otherwise stranded.
3. **If the e35 question is to be decided at all, the pre-era population has to be
   recovered first**, and that is a *read-surface* problem, not a research one
   (§ 3). It gets harder every day.

## 9. Filed, not fixed

- `BL-20260917-THE-BREAK-VERDICT-CONSUMES-A-HAND-TYPED-STOP-DID-SCALAR-WHOSE-POPULATION-AND-PROVENANCE-ITS-OWN-ARTIFACT-NEVER-STATE`
- `BL-20260917-E35-BREAK-ATTRIBUTION-POPULATION-NAMES-A-CENSUS-BUCKET-FABRICATED-CLOSE-WHILE-TESTING-ONE-EXIT-REASON`
- `BL-20260917-THE-E35-PRE-ERA-BASELINE-IS-ERODED-BY-THE-1000-ROW-JOURNAL-CLAMP-SO-A-TIER-3-QUESTION-GETS-LESS-ANSWERABLE-OVER-TIME`

## 10. Reproduce

```
python3 scripts/research/mi301_stop_term_robustness.py --self-test
python3 scripts/research/mi301_stop_term_robustness.py \
    --trades trades.json --packages order_packages.json
```

⚠️ **The numbers above are a dated snapshot of a rolling window.** A later pull will
give a smaller treated pre cell and a different DiD (§ 6). Re-run it rather than
quoting these, and state the trade-id range you got.
