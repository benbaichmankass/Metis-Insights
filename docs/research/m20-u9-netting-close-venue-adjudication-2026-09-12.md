# MI-278 U9 — the venue DID book these closes, three of them after the journal had already closed the row

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U9 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Discharges option (b) of** `BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED` — *"the rows are individually adjudicated against venue truth"*.
> **Tier-1.** Research tooling plus read-only diag pulls. No `config/`, no `src/runtime/`, no unit file, no order path.

---

## 0. The answer, in six sentences

MI-277 found `netting_attributed` carrying the single largest share of the winner-size collapse with **100% ESTIMATED provenance — not one measured row** — and said plainly it had *not* established any row to be a false close, because a journal query cannot. Asked of the venue's own `/v5/position/closed-pnl` — a **different endpoint** from the position path whose misread produces these rows — the venue **did** book a real close of matching size on **25 rows corroborated by an independent price field**, so a blanket "these closes never happened" reading is refuted. What the venue disagrees about is the **amount**: journal minus venue is **+$4,416.65 over those 25 rows**, of which **$2,375.97 (53.8%) is a fee-basis difference** (the journal figure is the gross price move, verified against its own arithmetic on 25 of 25) and **$2,040.68 (46.2%) is genuine price error**. **Five of the 25 have the wrong SIGN** — the journal books a profit where the venue booked a loss, the largest being **+$30.23 against −$953.15**. And **three of the 25 were booked by the venue 61 to 173 minutes AFTER the journal had already closed the row**, one of which is the exact trade `OI-20260908` names — timestamped corroboration of that open item's mechanism, from the second venue endpoint, which is the thing it asks for. The dollar magnitude is **almost entirely on paper accounts**; the two real-money rows differ by **$2.12 in total**.

**What this does NOT establish.** It does not adjudicate the 10 rows with no qty match, the 8 weak matches, the 5 refuted ones, or the 2 whose window the venue answered empty. It does not prove any close was *false* — only that three were *premature relative to the venue's own booking*. And a qty match is not a 1:1 identity: netting attribution exists precisely because one venue close can cover several journal rows.

---

## 1. Population, stated first

| | |
|---|---|
| journal source | `/api/diag/journal?table=trades&limit=1000`, live VM, read **2026-09-12** |
| label | `exit_reason = "netting_attributed"`, `status = "closed"` |
| rows | **50** — `bybit_1` 39 (paper), `bybit_2` 6 (**real money**), `bybit_portfolio` 5 (paper) |
| symbols | SOLUSDT 15 · ETHUSDT 11 · BTCUSDT 9 · AVAXUSDT 7 · XRPUSDT 7 · ADAUSDT 1 |
| closes span | 2026-08-18T06:25:02Z → 2026-09-11T08:30:54Z |
| venue source | `/api/diag/bybit_raw_closed_pnl`, **27 windows** over 12 (account, symbol) pairs, **301 records** |
| venue read health | 27 of 27 parsed · **0** truncated · **0** errored · 25 `rows_returned`, 2 `no_rows` |

⚠️ **This is not the same 50 rows the backlog row measured.** That row read an earlier tail (ids 4709–5708, `bybit_1` only, 39 rows). Its numbers are not re-quoted here and this memo does not supersede them.

---

## 2. Why the venue endpoint had to be the *other* one

`OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT` establishes the mechanism that produces these rows: a zero-size hedge-book sibling makes `_bybit_position_protection` read `rows[0]` and return `_flat` for a symbol that is **not** flat, after which `_reconcile_netting_partial_closes` attributes and closes.

Adjudicating against `/v5/position/list` would therefore be asking the endpoint that was already wrong. `/v5/position/closed-pnl` asks a different question — *did you book a realised close?* — and shares no filter, dedupe, cursor or `size` field with the position path, which is exactly why `/api/diag/bybit_raw_closed_pnl` was built.

---

## 3. ⚠️ The matcher is graded before it is trusted, and its control flipped the headline's sign

A journal row is matched to a venue record by **side and quantity**. Under one-way netting a symbol is one exchange position holding N journal rows, so a coincidental qty match is a live hazard rather than a theoretical one.

Every match is therefore graded against **`avg_entry_price`** — a field neither qty nor time determines, so agreement is independent corroboration and disagreement refutes the match.

| grade | entry-price agreement | count |
|---|---|--:|
| **corroborated** | ≤ 25 bp | **25** |
| weak | 25–100 bp | 8 |
| **refuted** | > 100 bp | **5** |

**That grading is not a formality — the aggregate reverses on it:**

| population | n | journal | venue | journal − venue |
|---|--:|--:|--:|--:|
| all qty matches | 38 | −10,370.67 | −8,414.81 | **−1,955.86** |
| **corroborated only** | 25 | −5,623.40 | −10,040.05 | **+4,416.65** |

One refuted match carries most of that swing: trade **5262**, whose best qty candidate sits **5.8 days** from the journal close and disagrees on entry by **150 bp**. A figure whose sign flips on a filter choice is the hazard `CLAUDE.md` § "Always state the population" exists for — here the filter is *"is this match real?"*, and answering it is not optional.

---

## 4. What the venue disagrees about is the AMOUNT, and half of the gap is not an error

On the 25 corroborated rows the journal's own figure equals `qty × (exit − entry) × sign` on **25 of 25** — so it is the **gross price move and carries no fee term**. The venue's `closed_pnl` is net. The gap therefore decomposes exactly:

```
journal − venue  =  +4,416.65
                 =  price basis +2,040.68   (46.2%)   <- the estimation error
                 +  fee   basis +2,375.97   (53.8%)   <- a BASIS DIFFERENCE, not an error
```

Reporting the whole $4,416.65 as error would overstate it by more than half. The fee term is positive on every one of the 25, which is what a fee term must be.

### 4a. Five of 25 disagree on the SIGN

| trade | account | symbol | journal | venue |
|---|---|---|--:|--:|
| 4842 | `bybit_1` | SOLUSDT | **+152.09** | −36.44 |
| 4941 | `bybit_1` | XRPUSDT | **+60.19** | −9.58 |
| 5515 | `bybit_1` | SOLUSDT | **+30.23** | **−953.15** |
| 5554 | `bybit_1` | SOLUSDT | **+21.80** | −6.31 |
| 5610 | `bybit_1` | AVAXUSDT | **+134.46** | −281.99 |

Each of these is a row that enters a winner population as a winner and was a loss at the venue. That is a stronger statement than "the magnitude is estimated", and it bears directly on MI-277: a winner-size measurement over a population containing these rows is counting losses as small wins.

### 4b. By account — the dollars are almost entirely paper

| account | class | n | journal − venue | price | fees |
|---|---|--:|--:|--:|--:|
| `bybit_1` | paper | 21 | 4,146.73 | 1,851.88 | 2,294.85 |
| `bybit_portfolio` | paper | 2 | 267.80 | 187.49 | 80.31 |
| **`bybit_2`** | **real money** | **2** | **2.12** | 1.31 | 0.81 |

⚠️ **Do not read the real-money line as "harmless".** It is **n = 2**, and MI-277's finding is about **R and winner size**, not dollars — a 3× overstatement on a $2 trade contaminates an R statistic exactly as much as on a $2,000 one.

---

## 5. Three rows were booked by the venue AFTER the journal closed them — and one is `OI-20260908`'s own trade

Across the 25 corroborated rows the median gap between the venue's `updated_time` and the journal's `closed_at` is **−0.3 minutes**: the venue closed, and the reconciler recorded it a moment later. That is the healthy shape, and it holds on 22 of 25.

Three go the other way:

| trade | account / symbol | venue booked it | journal figure | venue figure |
|---|---|--:|--:|--:|
| 5515 | `bybit_1` / SOLUSDT | **+172.8 min later** | +30.23 | −953.15 |
| 5554 | `bybit_1` / SOLUSDT | **+72.4 min later** | +21.80 | −6.31 |
| **5568** | `bybit_1` / ETHUSDT | **+61.5 min later** | +63.82 | +22.24 |

**Trade 5568 is the exact row `OI-20260908` names** — `bybit_1` ETHUSDT long 26.05, closed by the journal at 2026-09-08T14:21:54Z on an `exchange_qty: 0.0` read while the venue still held the position. The venue booked a `Sell` of **exactly 26.05** at 15:23:24Z, **61.5 minutes later**, at `avg_entry 2476.75` against the journal's 2477.54 (3.2 bp).

So the sequence is now timestamped from the venue side: **the position was not flat when the journal said it was, and the venue closed that exact quantity an hour afterwards.** That is the observation `OI-20260908` asks for and that the backlog row correctly said a journal query could not produce.

⚠️ **Two of those three (5515, 5554) are also sign disagreements.** The premature-close set and the wrong-sign set overlap, which is what you would expect if a row closed at a mark rather than at a fill — but n = 3 does not establish that, and this memo does not claim it.

---

## 6. What it means for the backlog row and for MI-278

- **Option (b) is discharged for 25 of 50 rows** and explicitly not for the other 25. The row asked for individual adjudication against venue truth; that is what this is, with the unadjudicated remainder named rather than absorbed.
- **The row's caveat stands and is now sharper.** It said no winner-size magnitude computed over a population containing `netting_attributed` rows should be quoted without the caveat. The reason is no longer only "the provenance is ESTIMATED" — it is that **5 of 25 corroborated rows have the wrong sign** and 3 were closed before the venue closed them.
- **It does not clear `OI-20260908`; it strengthens it** with venue-side timestamps on the row that item names.
- **MI-278's own two Tier-3 proposals are unaffected** — `ict_scalp_xrp_15m` `tp_at_r` 1.5 → 1.25 and `atr_sl_buffer_mult` 0.20 → 0.10 were measured in a backtest, which has no reconciler and no netting attribution.

---

## 7. Reproducing it, and what is NOT done

```bash
bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=trades&limit=1000' > trades.json
mkdir -p venue
python3 scripts/research/netting_close_venue_adjudication.py \
    --trades trades.json --emit-fetch-plan | sh
python3 scripts/research/netting_close_venue_adjudication.py \
    --trades trades.json --venue-dir venue
```

The script makes **no network call of its own** — it prints the `diag_fetch` lines and you run them, because the venue window is capped at **7 days per call** by Bybit (`ErrCode 10001`) and the chunking is the only reason a plan is needed. `--self-test` asserts the eight properties it would be worthless without, including that a refuted match is refuted and that `could_not_look` never reads as `venue_no_rows_in_window`.

**Not done, deliberately:**

- **No adjudication of the 25 non-corroborated rows.** For the 10 with no qty match the honest reading is *netting* — one venue close covering several journal rows — and testing that needs a set-partition search over candidate sums, which invents a matching rule rather than reading one. Filed as `BL-20260912-HALF-THE-NETTING-ATTRIBUTED-POPULATION-CANNOT-BE-ADJUDICATED-ONE-TO-ONE-AGAINST-THE-VENUE`.
- **No fix.** The price error and the premature closes both live in `order_monitor`, which is Tier-2, and `OI-20260908`'s root cause (book selection in `_bybit_position_protection`) is already the named owner of the second one.
- **No re-measure of MI-277's winner-size numbers.** The row's option (a) asks for that *after* the root cause lands; doing it now would measure the same contaminated population twice.
