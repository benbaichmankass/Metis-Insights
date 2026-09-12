# MI-278 U16 — the twelve rows U9 could not match cannot be adjudicated by quantity at all, and the obvious extension is unsound

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U16 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Closes out option (b) of** `BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED` **as unreachable for the remainder**, which leaves the row resting on option (a).
> **Tier-1.** One new `scripts/research/` module, read-only over the journal and one diag endpoint. No `src/`, no config, no order path, nothing enacted.

---

## 0. The answer, in six sentences

MI-278 U9 discharged that row's option (b) for the matched half — 38 of 50 closed `netting_attributed` rows — and left **10 `venue_closed_different_qty`** and **2 `venue_no_rows_in_window`** unexamined; those 12 are the whole reason the row is still open. The obvious reading is that U9's matcher keys on ONE venue close per journal row while netting reduces a position in slices, and trade 5079 looks exactly like that: journal 0.656 against venue closes of 0.65 + 0.004 + 0.002. **Enumerated properly, subset-sum adjudicates none of the twelve — `sum_matched_unique` is 0.** Eight have no subset-sum at any window, the venue is silent on two, and the two that *do* have sums have **many**: trade 5079 admits 5 distinct PnL answers and trade 4930 admits **74**, spanning $37.01. **The more useful result is that the extension is unsound rather than merely unhelpful** — a subset-sum matcher returns a different answer depending on a search window that is not part of the question, so it generates defensible-looking numbers instead of measuring one. **Option (b) is therefore unreachable for this remainder by quantity matching of any kind**, and the row's remaining path is option (a): land `OI-20260908`'s root cause and re-measure.

---

## 1. Population, stated first

| | |
|---|---|
| **Journal** | `/api/diag/journal?table=trades&limit=1000`, pulled 2026-09-12T16:0xZ |
| **Label** | `exit_reason = netting_attributed`, closed rows → **50** |
| **Venue** | `/api/diag/bybit_raw_closed_pnl` over **27 windows / 12 (account, symbol) pairs**, 7-day cap per call; **0 truncated, 0 errored, 0 load problems** |
| **U9's split, reproduced exactly** | `venue_close_matched_qty` **38** · `venue_closed_different_qty` **10** · `venue_no_rows_in_window` **2** |
| **Graded here** | the **12** U9 left |
| **Tolerance** | `QTY_TOL` **imported** from U9, not restated — the two files cannot disagree about what "same size" means |

⚠️ **This is a newer tail than the 39 rows the backlog row originally measured.** They are not the same rows, and its numbers are not restated here as though they were.

---

## 2. The hypothesis, and why it fails

Under netting a position is reduced in slices, so `venue_closed_different_qty` ought to mean *the matcher's 1:1 key cannot express a journal row that corresponds to several venue closes.* Trade 5079 is the poster case.

**Result: `sum_matched_unique` = 0 of 12.**

| state | n |
|---|---|
| `sum_matched_unique` | **0** |
| `ambiguous_multiple_subsets` | 2 |
| `no_subset_sums` | 8 |
| `venue_silent` | 2 |

⚠️ **An earlier exploratory pass of mine reported "2 of 10 explained", and that was an artifact of taking the FIRST combination found.** Enumerating every subset instead shows both are ambiguous. The correction is recorded rather than quietly replaced, because the wrong version is the one a hurried search naturally produces.

---

## 3. The finding that matters: subset-sum is under-determined

| trade | symbol | journal PnL | distinct venue-PnL answers | spread |
|---|---|---|---|---|
| 5079 | BTCUSDT | +$93.81 | **5** | $0.77 |
| 4930 | SOLUSDT | +$1,082.66 | **74** | **$37.01** |

Trade 4930 is the clean demonstration. At a ±360-minute window the search finds `2429.2 + 8.4 + 11.5`; at ±1440 minutes it finds `2429.2 + 5.2 + 14.9`. **Both sum to the journal quantity inside tolerance, and they attribute different venue PnL — $199.09 against $180.53.**

**A matcher whose answer changes with a parameter that is not part of the question is not an adjudicator.** So this file grades such a row `ambiguous_multiple_subsets` — a refusal — and publishes `n_alternatives` and the PnL spread beside it.

⚠️ **Do not "fix" this by fixing the window.** That picks one arbitrary answer and hides the spread, which is strictly worse than the honest refusal: the number would look adjudicated and would not be.

---

## 4. What the other ten look like

**`no_subset_sums` (8)** — searched across all three windows and no combination of up to 4 same-side venue closes reaches the journal quantity. The shapes go both ways, so there is no single story:

- **The journal row is far LARGER than anything nearby.** Trade 4962 (`bybit_1`/ETHUSDT): journal 43.28 against a nearest same-side venue close of 0.53, 858 minutes away.
- **The journal row is a small SLICE of a much larger venue close.** Trade 5245 (`bybit_1`/SOLUSDT): journal 15.0 against venue closes of 336.9 and 655.9, both at +34.0 minutes.
- **Near-misses just outside tolerance.** Trade 5438: journal 440.9 against 451.1 (2.3% off) — close enough to tempt a widened tolerance, which would be choosing the answer.

**`venue_silent` (2)** — the venue answered with **no rows at all on the closing side** for that pair. This is *we asked and it had nothing*, kept distinct from *we searched and found no sum*. ⚠️ **One of them carries the largest unadjudicated figure in the population**: trade 5451, `bybit_portfolio`/BTCUSDT, journal **−$2,250.34**.

---

## 5. What this means for the row

**Option (b) is unreachable for the remainder.** U9 discharged it for 38 of 50; quantity matching of any kind — 1:1 or sum-aware — cannot reach the other 12, and the sum-aware route is additionally unsound. **The row's remaining path is option (a)**: land `OI-20260908`'s root cause (book selection in `_bybit_position_protection`) and re-measure over a post-fix window.

That is a narrowing, not a discharge. The row stays **open**, and its final caveat still binds: *no winner-size magnitude computed over a population containing `netting_attributed` rows should be quoted without it.*

⚠️ **A candidate explanation this unit did NOT establish.** A `netting_attributed` row's `position_size` is assigned by `_reconcile_netting_partial_closes`, not read from a venue fill, so there is no reason it must correspond to any venue close or sum of them — which would explain the whole remainder in one line. It is consistent with everything measured here and **it is not tested**; establishing it needs a read of the attribution path against these specific rows, which is a different unit.

---

## 6. What this does NOT establish

- **It does not show any of the 12 is a false close.** That still needs the venue-side read `OI-20260908` specifies. Nothing here contradicts U9's finding that the closes it *could* match are real.
- **It does not adjudicate the 8.** `no_subset_sums` is a measurement of a search, not a verdict on the trade.
- **It says nothing about the 38 U9 matched** — those keep U9's grading, including its 5 sign disagreements and 3 late venue bookings.
- **It does not widen the tolerance to rescue the near-misses**, deliberately: 5438 at 2.3% would match at a 2.5% tolerance, and choosing a tolerance that admits the row you want is choosing the answer.
- **n is 12.** Every count here is a small cell and none supports a rate.

---

## 7. Reproducing

```
python3 scripts/research/netting_unmatched_remainder.py --self-test    # 12 controls, no network
python3 scripts/research/netting_close_venue_adjudication.py \
    --trades trades.json --emit-fetch-plan > plan.sh && bash plan.sh   # 27 venue windows
python3 scripts/research/netting_unmatched_remainder.py \
    --trades trades.json --venue-dir venue --out u16.json
```

The venue fetch runs directly from a research container via `scripts/ops/diag_fetch.sh` — 27 calls, no relay round-trip needed.
