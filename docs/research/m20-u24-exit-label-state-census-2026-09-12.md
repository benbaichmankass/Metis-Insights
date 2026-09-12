# MI-278 U24 — the "unattributable" winners are in two different states, and the reader the backlog row asks for already exists

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **Unit:** MI-278 U24 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Advances, and CANNOT close,** `BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`.
> **Tier-1.** One new `scripts/research/` module; every VM read a `GET /api/diag/*`. No `src/`, no config, no order path, nothing enacted.

---

## 0. The answer, in six sentences

The backlog row proposes one remedy — persist `stop_order_type` / `cancel_type` at close and branch on it — for what turns out to be **two categorically different states** that it counts as one number. Measured over 68 non-pairs winners since 2026-08-27: the 29 `reconciler_filled` rows carry `exit_reason_source: unresolved` on **29 of 29**, meaning the classifier *ran, looked, and declined*, while the 7 `netting_attributed` rows carry **no key at all on 7 of 7**, which `order_monitor`'s own comment calls *"a 100% signature that none had ever reached the classifier"*. Those need different fixes: the first has a classifier that ran against an unusable price, the second has a path that runs no classifier, and a new column written by a path that writes nothing stays empty. **The row's premise about the fields is confirmed with a positive control** — `stop_order_type`, `cancel_type` and their camelCase forms occur **zero** times across 362 closed rows while `exit_price_source` occurs 357, `pnl_source` 325 and `close_exec_type` 186. ⚠️ **But the row's implied gap is partly WRONG, and that correction is the most useful thing here:** `src/web/api/routers/performance.py` **already** separates `label_unattested` / `label_refused` / `label_unresolved` / `label_attested`, so the reader the row asks for exists and is correct — what is missing is that nobody has read it *for this population*. I set out to show that a consumer would silently miscount the netting rows and the code refuted me.

---

## 1. What the row asks for, and why this unit cannot deliver it

Its `resolution_criteria`:

> The unattributable share is reduced by a **durable surface** rather than by a re-grade — the closing order's `stop_order_type` / `cancel_type` persisted at close time and **BRANCHED on by a reader**.

That is a `src/` journal-writer change: **Tier-2**, not mine to land. So this unit answers the Tier-1 question that should precede building it — *which state are these rows actually in, and would the proposed column reach them?*

---

## 2. A trap worth recording before any of the numbers

`trades` returns **41 columns** and **none of them is `exit_price_source`**. Reading that list and concluding the provenance is missing would have been wrong: `src/runtime/provenance.py:65` states the markers live in **`notes.exit_price_source` / `notes.pnl_source`** — a JSON blob, not columns.

I made exactly that misread and caught it by checking the source rather than trusting the schema dump. `parse_notes` in the shipped module exists to stop the next reader repeating it, and its docstring says so.

---

## 3. The census

**Population, stated:** one `/api/diag/journal?table=trades` pull of **1000 rows** → **68** graded (closed · non-backtest · `pnl NOT NULL` · `created_at >= 2026-08-27` · winners · **pairs excluded**, per the row's own scoping). Reproduce with:

```
python3 scripts/research/exit_label_state_census.py --trades <trades.json> --since 2026-08-27
```

| exit_reason | attested | unresolved | **unattested** |
|---|---|---|---|
| `reconciler_filled` | | **29** | |
| `netting_attributed` | | | **7** |
| `operator_flatten_reconciled` | | 3 | |
| `tp` | 9 | | |
| `sl` | 3 | | |
| `tp_cross` | | | 6 |
| `sl_cross` | | | 2 |
| `giveback_stop` | | | 2 |
| `exchange_flat_reconciled` | | | 3 |
| `stuck_strategy_watchdog` | | | 2 |
| `exit_head` / `intent_reduce` | | | 1 / 1 |
| **total** | **12** | **32** | **24** |

### 3.1 ⚠️ Most of that `unattested` column is BY DESIGN, and reporting it as a defect would be the error

`performance.py`'s own comment scopes the classifier: only the *reconciler-derived* buckets (`""`, `reconciler_filled`) *"are ones the classifier was ever meant to reach"*. So `tp_cross`, `sl_cross`, `giveback_stop`, `exit_head` and `intent_reduce` being unattested is correct — **their `exit_reason` already names the mechanism**, and no classifier was meant to run.

Splitting the 24 on that basis — **my reading, labelled as a judgement rather than a measurement**:

- **names its own mechanism (12):** `tp_cross` 6 · `sl_cross` 2 · `giveback_stop` 2 · `exit_head` 1 · `intent_reduce` 1
- **names no mechanism (12):** **`netting_attributed` 7** · `exchange_flat_reconciled` 3 · `stuck_strategy_watchdog` 2

**`netting_attributed` is the largest bucket that is both unattested and mechanism-silent**, which is what makes it the one the row is really about.

---

## 4. Would the proposed column reach them?

**Population: all 362 closed non-pairs rows in the pull.**

```
proposed : stop_order_type 0 · cancel_type 0 · stopOrderType 0 · cancelType 0
control  : exit_price_source 357 · pnl_source 325 · close_exec_type 186
probe_state: gradeable        VERDICT: proposed_fields_absent_on_this_population
```

The zero is a **measurement**, not a failed grep — the control fields are present on the same rows through the same accessor. The probe returns `not_gradeable_no_control_field_present` rather than a verdict when the control is empty, so a zero can never be reported off a broken reader.

**And the venue surface the remedy would draw on already exists**: `/api/diag/bybit_raw_order_history`, whose own docstring calls `cancelType` *"the load-bearing field"* separating a user cancel from a venue-side clear, and notes that the nearest existing caller `account_order_status` **normalises exactly those fields away**. So the remedy is buildable — the objection is not feasibility, it is that **it is one remedy aimed at two defects**.

---

## 5. What this changes about the row

- **For the 29 `unresolved`:** the classifier ran and declined because the price was unusable. A venue-supplied `cancelType` is *independent* evidence and would genuinely help — and the target list is already enumerable, because the journal declares which rows they are.
- **For the 7 `netting_attributed`:** the classifier never ran. Adding a column to a path that writes no close-side keys at all changes nothing. **The first fix is that the path records something.**
- **The reader is not missing.** `performance.py` already reports the four states separately and correctly. The row should cite it rather than ask for it.

---

## 6. What this unit does NOT do

- **It does not close the row** — the durable surface is Tier-2 and unbuilt, and the row's other clearing condition is a re-run *on a later window*, which today's data cannot be.
- **It proposes no `src/` change here.** The two-remedy split above is the input to a Tier-2 proposal, not the proposal.
- **It does not re-run U7 / U10 / U16.** Those asked whether the venue *can* name a mechanism; this asks which *journal state* these rows are in and whether the *proposed field* reaches them.
- **The 12/12 split in §3.1 is a judgement**, not a measurement — it depends on reading `exit_reason` as self-describing, and someone may reasonably re-cut it.
- **n is small and one-window**: 68 winners, 7 netting rows. Nothing here survives being quoted without those denominators.

---

## 7. Verification

`python3 scripts/research/exit_label_state_census.py --self-test` → **24 controls, 0 failures**, no network.

Five defects were planted. Three tripped the controls that name them; **two exposed real gaps, and both are recorded rather than smoothed over:**

| planted defect | outcome |
|---|---|
| collapse an absent key into `unresolved` | caught — **4, 5, 6, 9, 12, 14** |
| report a zero with no control field present | caught — **16** |
| leave pairs in the population | caught — **19, 20, 21** |
| make malformed `notes` raise instead of returning `{}` | **caught only by CRASHING** — my `grep '^ FAIL'` could not see it, which is a flaw in how I was *measuring* the plants, not in the harness |
| rename the refusal constant upstream | **tripped NOTHING** — control 3 used `REFUSED` on both sides, a tautology, while the docstring promised a rename would fail here |

The last one is now true rather than promised: control 3 pins the **literal** string, and new control **3b** reads `EXIT_LABEL_REFUSED_UNMEASURED` textually out of `src/runtime/provenance.py` and compares, so an upstream rename fails here instead of silently mis-bucketing every refusal. A file it cannot read returns `None` — *we could not look* — which the control treats as a failure, never a pass.
