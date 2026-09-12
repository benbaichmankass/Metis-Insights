# MI-278 U10 — the venue names the mechanism on 11 of 17, and calls five of the "winners" stop-outs

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U10 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Bears on** `BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`.
> **Tier-1.** Research tooling plus read-only diag pulls. No `config/`, no `src/`, no unit file, no order path, nothing enacted.

---

## 0. The answer, in five sentences

That row's resolution criterion names the remedy exactly — persist the closing order's `stop_order_type` / `cancel_type` at close time and branch on it — and that persistence is a **Tier-2 write in `order_monitor`**, so the question that must be settled first is whether the venue holds the answer at all. **It does: over the 17 winners in a U2 class that could not name a mechanism, the venue answers for 11 (64.7%)** — 10 with a populated `stop_order_type` and 1 as a plain (non-bracket) order — and **`absent_from_order_history` is ZERO**, so wherever a trustworthy close exists the order history has it. What it names is not neutral: **7 of the 11 are stop fires** (6 `PartialStopLoss`, 1 `StopLoss`) against 3 take-profit fires and 1 plain order. **And five of those eleven rows — every one of them a stop fire — are LOSSES at the venue while the journal records them as winners**: journal **+$358.94** against venue **−$2,095.68**, so they are sitting in a winner population that MI-277 and U2 measure winner size over. The six unanswered rows fail at the **closed-PnL match**, not at the order history, so the bottleneck is the join and not the venue's records — which is what makes the Tier-2 build worth doing.

**What this does NOT establish.** It does not re-grade U2's headline (*no exit lever is cutting winners short*): a stop fire on a row the venue books as a loss is not a lever cutting a winner short, it is a **misclassified loss**, and those are different claims. It does not touch the 27 `attributed` or 6 `account_level` rows. It builds nothing that persists, and it proposes the Tier-2 change rather than taking it.

---

## 1. Population, stated first

| | |
|---|---|
| classifier | `scripts/research/m20_u2_winner_close_attribution.py`, re-run today at its default `--tolerance 0.05` |
| journal source | `/api/diag/journal?table=trades&limit=1000` + `…&table=order_packages&limit=1000`, read **2026-09-12** |
| U2 winner population, re-derived | **50** — `attributed` 27 · `account_level` 6 · `contaminated` 7 · `unattributable_price` 9 · `unattributable_level` 1 |
| **this unit's population** | the **17** in the three classes that name no mechanism |
| all on | `bybit_1` (paper), four symbols — SOLUSDT, ETHUSDT, XRPUSDT, AVAXUSDT |
| closes span | 2026-08-27T08:04:59Z → 2026-09-09T15:38:36Z |
| venue source | `/api/diag/bybit_raw_closed_pnl` **8 windows / 121 records** + `/api/diag/bybit_raw_order_history` **8 windows / 235 orders**; 0 truncated, 0 errored |

⚠️ **U2's memo reports 49 winners and this re-run finds 50.** Same script, same settings, a journal pull taken later in the day — one more close entered the window. The classes are otherwise identical. Neither number is restated as the other's.

⚠️ **This is a TAIL and one account.** `bybit_1` is paper. Nothing here is a real-money measurement, and the mechanism-recovery *rate* is a property of these 17 rows.

---

## 2. Two endpoints, because neither answers alone

`/api/diag/bybit_raw_closed_pnl` gives the realised close — side, qty, `avg_entry_price`, `avg_exit_price`, `closed_pnl` — and an **`order_id`**. It carries **no order type**, so it can turn an ESTIMATED price into a MEASURED one and can say nothing about *what fired*.

`/api/diag/bybit_raw_order_history` carries `stop_order_type`, `cancel_type`, `order_type`, `order_status`, `trigger_price` for that `order_id`. **The `order_id` is the entire join**, and it is why the answer needed both.

**The matcher is imported, not rebuilt.** The trade → closed-PnL match and the `avg_entry_price` grading that decides whether a match may be trusted come from `netting_close_venue_adjudication` (MI-278 U9) by name. Two copies of *"is this the same close?"* would be free to drift, and that module already records why the grading is not optional: over its own population the aggregate journal-minus-venue figure **reverses sign** between all-matches and corroborated-only.

---

## 3. Can the venue name it? — 11 of 17

| state | n |
|---|--:|
| **`named`** — a populated `stop_order_type` | **10** |
| **`plain_order`** — present, empty `stop_order_type`: an ordinary close, not a bracket leg | **1** |
| `absent_from_order_history` — *we could not look* | **0** |
| `no_corroborated_match` — no trustworthy closed-PnL row to join from | 6 |

**The venue answers for 11 of 17 (64.7%).** `absent_from_order_history` being **zero** is the load-bearing part: **the failure is never the order history.** All six unanswered rows fail earlier, at the closed-PnL match — 2 refuted on entry price, 2 weak, 2 with no qty match on the closing side. A Tier-2 writer that captured the order id at close time would not have that problem at all, because it would not be reconstructing a match after the fact.

By U2 bucket:

| bucket | in bucket | corroborated | mechanism recovered |
|---|--:|--:|---|
| `unattributable_price` | 9 | 5 | 3 × `PartialStopLoss`, 1 × `TakeProfit`, 1 × plain order |
| `contaminated` | 7 | 5 | 3 × `PartialStopLoss`, 1 × `StopLoss`, 1 × `PartialTakeProfit` |
| `unattributable_level` | 1 | 1 | 1 × `PartialTakeProfit` |

### 3a. `cancel_type: UNKNOWN` on all ten is CORRECT, not a gap

Every named row is `order_status: Filled`. Nothing was cancelled, so there is no cancel reason. **`stop_order_type` answers a FILLED close; `cancel_type` answers a leg that was CANCELLED instead** — the backlog row names both because they answer different closes. Reading the `UNKNOWN` as a missing answer would be the unprovenanced-diagnostic error one level up, and the instrument says so in its own output.

### 3b. An empty `stop_order_type` is an answer too

Trade 5420 is `order_type: Market`, `Filled`, with no `stop_order_type` — an ordinary close, not a bracket leg firing. That is reported as `plain_order` and is **never pooled with *we could not look***.

---

## 4. ⚠️ Five of the eleven "winners" are losses at the venue, and all five are stop fires

| trade | symbol | U2 bucket | journal | venue | venue calls it |
|---|---|---|--:|--:|---|
| **5515** | SOLUSDT | `contaminated` | **+30.23** | **−953.15** | `StopLoss` |
| **5437** | SOLUSDT | `unattributable_price` | **+23.20** | **−536.89** | `PartialStopLoss` |
| **5442** | SOLUSDT | `unattributable_price` | **+149.24** | **−317.33** | `PartialStopLoss` |
| **5610** | AVAXUSDT | `contaminated` | **+134.46** | **−281.99** | `PartialStopLoss` |
| **5554** | SOLUSDT | `contaminated` | **+21.80** | **−6.31** | `PartialStopLoss` |

**Those five rows: journal +$358.94 of winnings against venue −$2,095.68.** Over all 11 corroborated rows, journal **+$3,259.96** against venue **+$1,385.00**.

This is stronger than *the magnitude is estimated*. These rows are **in the winner population** — the one MI-277 measures winner size over and U2 attributes mechanisms across — and at the venue they are stop-outs. The journal's `candle_at_close` anchor did not merely blur them; on five rows it **inverted the outcome**, and the mechanism it could not name is the stop.

⚠️ **It does not follow that a lever is cutting winners short.** U2's headline — *no exit lever is cutting winners short, the whole family is 3 of 49* — is about winners being ended early. A row the venue books as a **loss** is not a shortened winner; it is a **misclassified loss**, which is a different (and, for a winner-size statistic, worse) problem. Do not cite this table as overturning that headline.

### 4a. Six of eleven were booked by the venue AFTER the journal closed the row

+248.7, +172.8, +72.4, +61.5, +54.6 and +52.5 minutes. U9 measured that shape on `netting_attributed` (3 of 25); here it also appears on `reconciler_filled`, so it is **not confined to one label**. At n = 11 that is an observation, not a rate.

---

## 5. What this means for the backlog row

Its criterion is *"the closing order's `stop_order_type` / `cancel_type` persisted at close time and BRANCHED on by a reader … re-run on a later window with both counts materially smaller"*.

- **The precondition is met and was not previously established:** the venue holds the answer, on `/api/diag/bybit_raw_order_history`, and returns it for every corroborated close in this population.
- **The criterion itself is NOT met**, and this unit does not claim it: nothing persists the field and nothing branches on it. A retrospective join is exactly the *re-grade* the criterion excludes.
- **The row stays open.** What changes is that the Tier-2 build now has a measured justification instead of a plausible one, and a known failure mode to design against: the retrospective join loses 6 of 17 at the match step, which capture-at-close-time does not have.

**PROPOSED, NOT TAKEN — Tier-2, `order_monitor`.** At close time, persist the closing order's `stop_order_type` and `cancel_type` beside the existing `exit_price_source`, and give them a consumer. Rationale from this measurement: the field is populated on 10 of 10 filled bracket closes the venue could be asked about; the one empty value is meaningful rather than missing; and capturing it at close time removes the match step that costs 35% of this population. **The operator decides; this lane proposes.**

---

## 6. Reproducing it, and what is NOT done

```bash
bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=trades&limit=1000'         > trades.json
bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=order_packages&limit=1000' > pkgs.json
python3 scripts/research/m20_u2_winner_close_attribution.py \
    --trades trades.json --packages pkgs.json --out u2.json
mkdir -p cp oh
python3 scripts/research/venue_mechanism_recovery.py --u2 u2.json --trades trades.json --emit-fetch-plan | sh
python3 scripts/research/venue_mechanism_recovery.py --u2 u2.json --trades trades.json \
    --closed-pnl-dir cp --order-history-dir oh
```

`--self-test` asserts the ten properties the report would be worthless without, including that an empty `stop_order_type` is `plain_order` rather than a gap, that an entry-price disagreement **refuses the join entirely** rather than reporting a mechanism from an untrusted match, and that a close on the wrong side is not a match.

**Not done, deliberately:**

- **No persistence and no writer.** Tier-2, `order_monitor`, proposed in §5.
- **No re-grade of U2's classes.** The buckets are U2's, unchanged; this reads them.
- **No claim about real money.** The whole population is `bybit_1`, paper.
- **No `cancel_type` finding.** Every row here filled, so the cancelled-leg half of the criterion is untested by this population — stated rather than left to look answered.
