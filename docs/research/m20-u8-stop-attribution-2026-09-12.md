# MI-278 U8 — a stop-out graded against `trades.stop_loss` is graded against the wrong number, on ~1 in 5 closed trades

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U8 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Answers** `BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT` (performance backlog, `high`, opened by MI-275).
> **Tier-1.** Research tooling plus one read-only diag pull. No `config/`, no `src/runtime/`, no unit file, no order path. Nothing here changes a live parameter.

---

## 0. The answer, in five sentences

`trades.stop_loss` is overwritten in place by a trailing amend, so an analysis that reads it and calls it "the declared stop" is attributing the exit to a level that did not end the trade. MI-275 measured that on 2 of its 7 post-e35 stop-out packages and asked for the split to be made wherever stop-outs are attributed to geometry. Measured here over a much wider population — **113 of 599 closed package-linked trades in the newest 1000-row `/api/diag/journal` tail, i.e. 18.9%, exited at an amended stop**, and among the exits whose own label claims a declared stop (`sl` + `sl_cross`) it is **21 of 78 = 26.9%**, which is MI-275's 29% reproduced at eleven times the sample size. The obvious repair — read `order_packages.sl` instead — **does not work and inverts the finding**: that field is trailed too, on **118 of the same 599 (19.7%)**, and on **2 of MI-275's own 2 named exhibits** it equals the final stop exactly, so a naive comparison grades both of them clean. The remedy shipped here is one owner, `src/research/stop_attribution.py`, which reads the entry-frozen `order_packages.exit_plan.stop.price`, refuses to grade when only a trailable level is available, and is imported by MI-275's own script so the two can never disagree.

**What this does NOT establish.** It does not say any trade was a stop-out — that adjudication stays with the caller, and `exit_reason` alone does not settle it. It does not re-grade MI-275's verdict, which was *do not revert e35* and is untouched. And it does not claim a lifetime rate: every number here is over a **tail**, stated below.

---

## 1. Population, stated first

| | |
|---|---|
| source | `/api/diag/journal?table=order_packages&limit=1000` and `…&table=trades&limit=1000`, live VM, read **2026-09-12** |
| packages | 1000 rows, `created_at` **2026-07-28T16:31:52Z → 2026-09-12T12:01:21Z** |
| trade rows | 1000 |
| closed trades | 599 |
| ...linked to a package present in the **same** tail | **599** (0 unlinked) |
| gradeable by the owner module | **599 of 599** — every package carries `exit_plan.stop.price` |

⚠️ **This is a TAIL, not the fleet lifetime.** `/api/diag/journal` serves the newest N rows, so every rate below is *"over the last ~6.5 weeks of closed trades"*. The reproducing script prints the unlinked count beside the linked one for exactly this reason: an analysis that silently drops unlinked rows and then prints its own subtotal as the denominator is the unasserted-denominator failure, and here it happens to be 0 — which is a fact worth printing, not a reason to stop printing it.

⚠️ **It is also NOT a stop-out population.** These are all closed trades. The per-`exit_reason` table below is what lets a caller cut its own.

---

## 2. The measurement

Reproduce with:

```bash
bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=order_packages&limit=1000' > pkgs.json
bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=trades&limit=1000'         > trades.json
python3 scripts/research/stop_attribution_fleet.py --packages pkgs.json --trades trades.json
```

**Headline, over the 599 closed package-linked trades defined in §1:** 486 exited at the entry-declared stop, **113 at a stop amended tighter, 0 at a stop amended wider**, 0 ungradeable — so **18.9% of that population of 599** was graded against a level a lever had already moved.

### 2a. By `exit_reason` — the same 599 trades

| `exit_reason` | n | at the declared stop | amended tighter | amended wider | amended % |
|---|--:|--:|--:|--:|--:|
| `reconciler_filled` | 145 | 117 | 28 | 0 | 19.3% |
| `pairs_revert` | 79 | 79 | 0 | 0 | 0.0% |
| `pairs_stop` | 70 | 70 | 0 | 0 | 0.0% |
| `pairs_half_open_cleanup` | 59 | 59 | 0 | 0 | 0.0% |
| **`sl`** | 53 | 39 | **14** | 0 | **26.4%** |
| `netting_attributed` | 50 | 32 | 18 | 0 | 36.0% |
| `exchange_flat_reconciled` | 28 | 12 | 16 | 0 | 57.1% |
| **`sl_cross`** | 25 | 18 | **7** | 0 | **28.0%** |
| `intent_reduce_executed` | 23 | 23 | 0 | 0 | 0.0% |
| `tp` | 19 | 4 | 15 | 0 | 78.9% |
| `stuck_strategy_watchdog` | 12 | 11 | 1 | 0 | 8.3% |
| `tp_cross` | 10 | 1 | 9 | 0 | 90.0% |
| `stale_stop` | 6 | 6 | 0 | 0 | 0.0% |
| `exit_head` | 6 | 5 | 1 | 0 | 16.7% |
| `operator_flatten_reconciled` | 6 | 6 | 0 | 0 | 0.0% |
| `intent_reduce` | 3 | 2 | 1 | 0 | 33.3% |
| `giveback_stop` | 3 | 0 | **3** | 0 | **100.0%** |
| `pairs_timeout` | 2 | 2 | 0 | 0 | 0.0% |

**The row this backlog item asks about:** `sl` + `sl_cross` together are **21 amended of 78 = 26.9%**, against MI-275's 2 of 7 = 29% on the e35 post-era. The rate survives an eleven-fold increase in sample size.

### 2b. Two controls the data supplied, which I did not plant

These are what make the table above evidence rather than a plausible-looking histogram:

- **`giveback_stop`: 3 of 3 amended, 100%.** That lever's entire job is to move the stop. A classifier that reported any of them as `entry_declared` would be broken.
- **The four `pairs_*` reasons: 0 of 210 amended, 0.0%.** The market-neutral pairs sleeve runs its own isolated executor (`src.units.strategies.pairs_executor`) and attaches no trailing lever, so it should be exactly zero — and it is, over the largest single block in the population.

A rule that fired on the pairs sleeve, or stayed silent on `giveback_stop`, would be wrong in a way no synthetic fixture would have caught.

### 2c. Every amendment is TIGHTER — 113 of 113

Not one stop in this population was moved **away** from the entry. That is a statement about the levers in service (`monitor_breakeven_sl`, the donchian trail, `trail_decay`, `stale_stop`, `giveback_stop` — all ratchets) and it is worth knowing before anyone proposes a widening lever: there is no observed precedent for one in the last ~6.5 weeks of closed trades. `amended_wider` is kept as a first-class state anyway, because measuring zero and being unable to express it are different things.

---

## 3. ⚠️ The obvious repair inverts the finding, on 2 of 2

*"Read the package instead of the trade"* is the fix a reader reaches for first, and it is wrong: `order_packages.sl` is overwritten in place too.

**Measured over the same 599:** `order_packages.sl` differs from `exit_plan.stop.price` on **118 of 599 (19.7%)**.

**On MI-275's two named exhibits it is worse than merely unreliable — it equals the final stop exactly:**

| package | leg | `exit_plan.stop.price` (frozen) | `order_packages.sl` | `trades.stop_loss` | a `pkg.sl` comparison would say |
|---|---|--:|--:|--:|---|
| `pkg-65f02cffa856451f` | `trend_donchian_avax_4h` | 7.27296429 | 7.25560714 | 7.25560714 | **`entry_declared`** — clean |
| `pkg-27b4c7d12e794bcc` | `trend_donchian` | 77044.07142857 | 77776.37857143 | 77776.37857143 | **`entry_declared`** — clean |

So an analysis using that fallback would compare a trailed level against itself, read a **zero** move, and report **2 of 2** of the amended exhibits as clean — the exact inversion of MI-275's finding. `src/research/stop_attribution.py` therefore returns `declared_stop_source` and **refuses to grade** (`ungradeable_declared_stop_not_entry_frozen`) rather than falling back, because *we could not look* and *we looked and it was the declared stop* are opposite statements.

---

## 4. The owner, and what it refuses to do

`src/research/stop_attribution.py` — pure, stdlib-only, dicts in and a dict out, so a research script, a test and a future CI check all reach the same answer.

**Three graded states:** `entry_declared` · `amended_tighter` · `amended_wider`.

**Six ungradeable states, none of which is a clean answer:** `ungradeable_no_declared_stop` · `ungradeable_declared_stop_not_entry_frozen` · `ungradeable_no_final_stop` · `ungradeable_no_anchor` · `ungradeable_no_direction` · `ungradeable_zero_declared_distance`.

Deliberate refusals, each with a reason:

1. **It never recomputes the stop as `atr × mult`.** MI-275 lost 15 hours to that: the declared stop is anchored to the **package** entry, not the fill (`pkg-65f02cffa856451f` declares `entry 7.128` against a trade `entry_price 7.099`), and some packages carry a clamped or rounded level. Reading the frozen level preserves whatever anchor, clamp and rounding the bot actually used. `anchor_source` names it when the fill is used as a fallback.
2. **It never guesses a direction.** *Tighter* means *toward the entry*, which is a fact about the **position's** side, not about which number is larger; an unknown direction is `ungradeable_no_direction`. Naming the wrong side here is the mistake `src/runtime/bybit_position_mode.py` refuses to make with `positionIdx`, for the same reason.
3. **Every magnitude is `None`, never `0.0`, when it could not be computed.** A zero move is a real reading.
4. **`format_split` cannot omit the ungradeable count.** The backlog row is explicit that silently excluding trailed stop-outs is *also* not sufficient — the count is load-bearing — so it is rendered by the shared formatter rather than left to each caller to remember.

### 4a. Why the tolerance is a fraction of the declared width, not ATR

MI-275 graded `|final − declared| / atr ≤ 0.02`. `meta.atr` is entry-frozen and present on only **322 of the 599 trades defined in §1 (53.8%)**, so an ATR tolerance cannot grade the other 46% at all. The basis here is `|final − declared| / |anchor − declared| ≤ 0.01`.

**They are not identical, and the difference is stated rather than hidden:** 1% of the declared width equals 0.02 ATR *exactly at a 2.0-ATR declared width*, is proportionally **stricter** below it (0.015 ATR at a 1.5-ATR width) and looser above. Stricter means it reports *more* amendments, never fewer — the safe direction for this question, since an amendment wrongly called `entry_declared` is what the row is about.

**Run side by side over the 322 of 599 trades that carry an entry-frozen ATR — the only rows where both rules can be evaluated — they produce 0 disagreements.** That is why rewiring MI-275's script (§5) does not move its published verdicts, and it is a measurement rather than an inference. Pinned by `tests/test_stop_attribution.py::TestAgreesWithMI275sOwnTolerance`, which also asserts the exact boundary where the two rules *do* diverge, so a later change to either tolerance fails there instead of quietly re-grading a memo.

---

## 5. The consumer — MI-275's own script now imports it

`scripts/research/stop_width_counterfactual_2026_09_11.py` computed this classification inline. It now calls the owner and maps the result back onto its own published vocabulary (`stop_is_entry_declared` / `stop_amended_tighter` / `stop_amended_wider`), so the memo's committed text stays valid while there is only one definition in the repo — the discipline `m20_corpus_union.py` uses when it imports `measurement_key` **by name** rather than re-deriving it.

Two things are asserted rather than hoped for:

- `scripts/research/stop_width_counterfactual_2026_09_11.py --self-test` still reports **OK**.
- `TestTheMI275VocabularyMapIsTotal` fails if the owner ever adds a state the published script cannot name — otherwise a new state would `KeyError` mid-run on a real package — and separately fails if any *ungradeable* state is ever mapped onto a *graded* published name.

**The positive control is the load-bearing test.** `tests/test_stop_attribution.py::TestMI275Exhibits` carries the two packages' real journal rows verbatim and asserts the owner reproduces MI-275's published `1.320` and `0.276` ATR figures to three decimals. It also checks, as a by-product, that each package's declared width lands on its own era's multiplier — **1.5 and 2.0 ATR exactly** — which independently re-confirms the e35 deploy reached the running trader.

All **42** tests pass, and five mutations were each caught: grading on the trailed fallback (2 failures), flipping tighter/wider (6), defaulting `moved_atr` to `0.0` (1), dropping the ungradeable clause from `format_split` (2), and defaulting `amended_frac` to `0.0` (1).

---

## 6. What this changes for MI-278 and for the e35 question

- **MI-275's verdict is untouched.** It excluded its 2 amended packages before computing the counterfactual, so its *do not revert e35* recommendation already rested on the right 5. This unit generalises the split; it does not re-open the decision.
- **It bounds any future exit-geometry work, including MI-278's own.** MI-278 U3/U4's two surviving proposals are `ict_scalp_xrp_15m` `tp_at_r 1.5 → 1.25` and `atr_sl_buffer_mult 0.20 → 0.10`. Both were measured in a **backtest**, where no trailing amend exists, so their harness numbers are unaffected. But the moment either is judged against live outcomes, this split applies, and at ~27% of labelled stop-outs it is not a rounding error.
- **`netting_attributed` at 36.0% amended (18 of 50) is worth a second look by someone else.** That path is already the subject of `BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED` and `OI-20260908`; that its exits also sit disproportionately on amended stops is an observation this unit makes and does **not** interpret.

---

## 7. What is NOT done

- **No CI guard.** A guard that failed any diff reading `trades.stop_loss` near the word "declared" would be a keyword test, and this repo has already paid for presence-only guards (`new-table-wiring-guard`). The honest enforcement today is that the one owner exists, is imported by the one prior offender, and is documented at the point of use. If a second analysis re-derives the rule, that is the moment a guard earns its cost.
- **No lifetime rate.** Everything is over the tail in §1. The journal holds more; reading it needs a windowed pull rather than `limit=1000`.
- **No stop-out adjudicator.** Deliberate: `exit_reason` does not settle whether a trade was a stop-out, MI-275 built a price-path adjudicator for its own population, and folding a second one in here would put two answers to that question in the repo — the thing this unit exists to prevent.
