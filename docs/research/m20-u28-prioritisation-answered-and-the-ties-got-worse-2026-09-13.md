# MI-278 U28 — the prioritisation study already ran; and the tie rate it was blocked by has risen from 51% to 78%

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Rows:** `BL-20260831-TRADE-PRIORITISATION-IS-UNPROVEN-CONFIDENCE-IS-A-START-NOT-A-RESULT` (performance, `open`) · `BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT` (health, `open`)
**Module:** [`scripts/research/prioritisation_row_discharge.py`](../../scripts/research/prioritisation_row_discharge.py) — 22 controls, 0 failures

---

## 0. The unit began as "run the study" and the study had already run

`BL-20260831-TRADE-PRIORITISATION-IS-UNPROVEN-CONFIDENCE-IS-A-START-NOT-A-RESULT` reads as an open build. `docs/research/trade-prioritisation-research-DESIGN.yaml` carries `status: answered`, `answered_on: '2026-09-08'`, `answered_by: WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS`. **Building the harness again would have been `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`** — the second time in this lane that the existence check paid for itself today (the first: `BL-20260909-UNKNOWN-STRATEGY-PRIORITY-NOW-BEATS-45-OF-50-DECLARED-LEGS-AND-THE-CONTENTION-IS-LIVE`'s two invariants are already built in #12070).

So the deliverable is the four criteria checked one at a time, not another study.

## 1. The four criteria, graded against the artifacts

| criterion | state | evidence |
|---|---|---|
| (1) ranking-key arm + a workflow | **`met_by_another_route`** | built on `scripts/research/nbook_portfolio.py`, **not** `scripts/backtest_system.py` as the row specifies; fired by `.github/workflows/ranking-key-ab.yml` |
| (2) four arms, net PnL / expectancy R / maxDD / **achieved** n | **`met`** | 1130 / 1467 / 1543 / 1429 contested trades |
| (3) stratified by `decided_by` | **`met`** | every arm carries `contested_by_decided_by` |
| (4) contested subset only | **`met`** | `population_note`: *"Every number is the CONTESTED subset."* |

Four states, never collapsed: `met` · `met_by_another_route` · `unmet` · **`ungradeable`** (*the artifact could not be read* — never `met`, and never `unmet`). Criterion (1) is graded as a **deviation** rather than smoothed to `met`, because a reader following the row to `backtest_system.py` will not find it there.

**Verdict `criteria_met_row_should_close`**, and the row's own text is what makes that right: *"⚠️ A NULL RESULT CLOSES THIS ROW — it is an answer, not a failure to be re-run until something wins."*

## 2. ⚠️ But the pooled headline hides what criterion (3) was written to expose

The design's `answer` is *"NO. Arbitration ORDER does not measurably drive outcome."* That is a **pooled** conclusion. Stratified, `confidence_first` is two very different populations:

| stratum | trades | expectancy R | net PnL |
|---|---|---|---|
| decided by **confidence** | 864 | **−0.1361** | −$747.54 |
| fell through to **declared_priority** | 266 | **−0.739** | **−$3,171.98** |
| *pooled* | 1130 | −0.278 | −$3,919.52 |

**81% of the arm's entire loss comes from the 24% of trades confidence could not decide.**

⚠️ **This is a within-arm composition note and NOT an effect, and the module marks every such reading with that flag.** The two strata are different contests — confidence ties on the saturated ones — so this does **not** say confidence is a good key. The between-arm null stands. What narrows is what the null may be quoted as meaning: it is a statement about *pooled* arms, and the pooled `confidence_first` number is a blend in which the minority stratum carries most of the damage.

## 3. ⚠️ THE TIE RATE HAS RISEN — 190/370 = 51.4% THROUGH 2026-08-30, 90/115 = 78.3% SINCE — AND IT IS NOT A MIX EFFECT

`BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT` measured **186/371 = 50.1%** exact ties through 2026-08-30T23:34Z and asks (criterion 4) for a re-measure on the **same** population definition. Same definition (≥2 non-flat contenders, one symbol, one tick), same soak, **disjoint** slices:

| slice | n contests | exact ties | rate |
|---|---|---|---|
| the row's own window (≤ cut) | 370 | 190 | **0.5135** |
| **disjoint, since the cut** | 115 | 90 | **0.7826** |
| lifetime | 485 | 280 | 0.5773 |

**Positive control:** my probe reads **190/370 = 0.5135** on the row's own window against its published **186/371 = 0.501** — a 1.1pp difference on 4 rows, from a minute-precision cut boundary. Reported rather than smoothed; it does not touch the direction.

⚠️ **The obvious explanation is refuted, which is what makes this worth reporting.** If more saturating legs were contending, the rate would rise with no score changing. The opposite happened:

| | pre | post |
|---|---|---|
| saturating family's share of observations | **0.482** | **0.267** |
| that family's own `confidence == 1.0` rate | **0.420** | **0.677** |

**Fewer saturating legs are contending, and they are saturating harder.**

⚠️ **State the population:** the post slice is **115 contests / 232 per-intent observations / 62 family observations**, about 13 days. Small. And it straddles the 2026-08-31 confidence-key go-live, so *which* contests get logged could have shifted in a way the family-share measurement does not cover.

## 4. Criterion (1) of the saturation row is PAID — the 1.0s are attributable, and localised

That row says *"ATTRIBUTE IT FIRST … A fix proposed before this is a guess"*, and that the attribution is computable from the existing rows with no new instrumentation. It is:

| strategy | obs | `== 1.0` | share |
|---|---|---|---|
| `trend_donchian_eth_4h` | 43 | 27 | **0.628** |
| `trend_donchian_eth` | 133 | 67 | 0.504 |
| `trend_donchian_sol` | 55 | 24 | 0.436 |
| `mgc_trend_1h` | 40 | 17 | 0.425 |
| `trend_donchian_sol_prop` | 53 | 22 | 0.415 |
| `trend_donchian_eth_prop` | 95 | 36 | 0.379 |
| `ict_scalp_xrp_5m` | 5 | 1 | 0.200 |

**7 of 26 strategies ever emit 1.0, and six of the seven are `trend_donchian` legs (four plus two prop mirrors) or `mgc_trend_1h`.** 194 of 983 per-intent observations (19.7%).

**The saturation is family-localised, not fleet-wide.** That points criterion (2) — *is 1.0 a clamp, an unset default, or a real reading?* — at **one builder**, which is a far smaller question than "the confidence score".

⚠️ **No fix is proposed here.** Criterion (2) is unpaid, and the row is explicit that proposing before attributing is a guess — and re-scoring is **Tier-3**, since confidence is the primary live routing key.

## 5. ⚠️ And the soak has gone degenerate, exactly as its row predicted

Monthly agreement between the conviction winner and the actual winner: **0.277** (Jun) → **0.607** (Jul) → **0.496** (Aug) → **1.000** (Sep, **114 of 114**).

The prioritisation row says *"now that conviction IS the live key its agreement figure largely degenerates, so nothing currently measures the open question at all."* **That is now measured rather than predicted.** A September reading of this soak's agreement rate is a tautology; nobody should quote it as evidence. The **tie** rate, which is a property of the scores and not of the ranking key, keeps working — which is why §3 uses it.

## 6. Verification

`--self-test` → **22 controls**, 0 failures. Seven defects planted, **all seven caught**:

| planted defect | control(s) |
|---|---|
| a missing arm reads `unmet`, not `ungradeable` | 2, 3 |
| partial stratification counts as met | 6 |
| an unreadable REPORT counts as met | 7 |
| `ungradeable` does not block the close | 3 |
| an empty soak returns a `0.0` tie rate | 19 |
| a one-candidate row counts in the tie denominator | 20 |
| the largest-loss share is taken against the stratum, not the arm | 13 |

---

## Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-…` is `loud: true` and unchanged: **17 days, −$38,851.81, onset 2026-08-27**. ⚠️ Note the overlap without overclaiming it: `trend_donchian_eth` / `_sol` are directional legs and are the same legs saturating here. **Nothing in this unit establishes a link** between the saturation and the bleed; they share a name and nothing more has been measured.
