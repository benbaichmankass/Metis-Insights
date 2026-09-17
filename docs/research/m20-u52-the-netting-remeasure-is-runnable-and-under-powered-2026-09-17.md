# M20 U52 — the re-measure the row waited for is now runnable, and it says `insufficient_n`

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U52** · RESEARCH lane · Tier-1 · works `BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED` (performance backlog, `severity: high`, `tier: 1`, `status: open`).

Instrument: [`scripts/research/netting_postfix_remeasure.py`](../../scripts/research/netting_postfix_remeasure.py)
(54 self-test controls; **14 planted defects, 14 caught by a named control** — one of them, P12, only after the suite was strengthened, because no fixture reached the branch).

---

## Input provenance

<!-- trades -->
<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
pulled_at: 2026-09-17T13:36Z
n_rows: 1000
id_field: id
rowset_digest: sha256:6838f9e990a3a206d374a139745951959c4582fe362594fc4f0dbe89c91c0e5f
order_digest: sha256:ebc2084f08202a7a35182ebb20db051fd7d5bf9d9f3fefa2f3e6862ef9e0b506
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5876
id_last: 4877
n_rows_without_id: 0
n_duplicate_ids: 0
-->

<!-- order_packages -->
<!-- input-provenance
source: /api/diag/journal?table=order_packages&limit=1000
pulled_at: 2026-09-17T13:36Z
n_rows: 1000
id_field: order_package_id
rowset_digest: sha256:4ad3be5265ca269a63b2f3e9ce20b4e4eaabc619a74ae76735317f7c3b96b5a2
order_digest: sha256:5e7797b21053e041467e211473aa73b5c1f419628a4adb65c8b5617356e397f0
fields_covered: close_reason,confidence,created_at,direction,entry,exit_plan,exit_plan_state,linked_trade_id,meta,model_scores,order_package_id,signal_logic,sl,status,strategy_name,symbol,tp,updated_at
id_first: pkg-57790a432401461b
id_last: pkg-a021a942e44446b1
n_rows_without_id: 0
n_duplicate_ids: 0
-->
Soak: `GET /api/diag/log_file?name=netting_attribution_soak` — **1000 lines** (the endpoint caps there whatever you ask for), `2026-08-29T08:11:10Z → 2026-09-16T07:12:15Z`, 1000 parsed / 0 unparseable, `mode: apply` on 33.

⚠️ **The stanzas carry no per-row digests**, so a later run can detect a difference and cannot NAME the changed rows. That capability is MI-278 U51 (`render_stanza(include_row_digests=True)`) and is **not on `main` yet** — PR #12432. The re-check this memo schedules should re-render with it.

**Population for every figure:** `bybit_1`, `status=closed`, not backtest, `pnl NOT NULL` — **n = 477** across four eras. Excluded and counted, never dropped: 295 other-account, 205 not-closed, 23 null-pnl.

---

## 1. Option (a) is reachable now, and the row does not know it

The row's exits are (a) *"OI-20260908's root cause lands … and a re-measure over a post-fix window shows the fall persisting or vanishing"* and (b) individual venue adjudication. **(b) is exhausted** — U9 matched 38 of 50 and U16 proved the remainder unreachable by quantity matching of any kind.

**(a)'s first half is DONE.** MI-281's correction to `OI-20260908` records the book-selection fix as landed (`46e1efb1f`, PR #11435) and **deployed 2026-09-08T18:53Z**. Eight days of post-fix window now exist.

## 2. There are TWO fixes in that window, so a three-era split is confounded

A **second** fix to the same chain merged inside it: **PR #11903** (MI-283), the `(symbol, position_idx)` dedupe in `account_open_positions`, merged **2026-09-12T19:47:52Z**. Both sever the path from a dropped hedge book to a false close. The eras are therefore **four**, and any result pooled across the last two attributes to neither.

## 3. The measurement

| era | closed | netting | share | by reason | by stamp | soak-only | winners | soak cov |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| `1_pre_e35` | 164 | 13 | 7.9% | 13 | 0 | 0 | 6 | partial |
| `2_e35_only` | 162 | 18 | **11.1%** | 15 | 1 | 2 | 6 | covered |
| `3_fix_a` | 79 | 5 | 6.3% | 3 | 1 | 1 | 3 | covered |
| `4_fix_a_and_b` | 72 | 3 | 4.2% | 2 | 0 | 1 | 1 | partial |

| contrast | | p |
|---|---|--:|
| `2_e35_only` vs `3_fix_a` | did fix A alone change the share? | 0.3500 |
| `3_fix_a` vs `4_fix_a_and_b` | did fix B add anything on top of A? | 1.0000 |
| `2_e35_only` vs `4_fix_a_and_b` | both fixes vs neither | 0.1346 |
| `1_pre_e35` vs `2_e35_only` | **control: e35 itself, no fix involved** | 0.3508 |
| `2_e35_only` vs **POOLED** `3+4` | *attributes to neither fix* | **0.0681** |

**Five contrasts. Bonferroni at 0.05/5 = 0.0100 — none survives.**

## 4. ⚠️ The scoping rule decides the verdict, and the unsound one gives the opposite answer

Run with `exit_reason == 'netting_attributed'` alone — the filter this row, U9 and U16 all use, and which **U33 proved unsound** because `_netting_apply_close`'s PARTIAL branch leaves the row OPEN to close later under an ordinary reason — the pooled contrast reads **9.3% → 3.3%, p = 0.0375**: significant. On the corrected **stamp-AND-soak union** it reads **11.1% → 5.3%, p = 0.0681**: not.

The filter misses **7 of 33** applied soak rows and misses them **unevenly** — 4 in era 2, 2 in era 3, 1 in era 4 — which is what moves the verdict. **A session that used the row's own scope would have reported a significant post-fix improvement.**

## 5. ⚠️ And the unsound scope is BIASED TOWARD `estimated`, which softens the row's headline

The row states *"`classify_pnl` returns ESTIMATED for 39 of 39 — there is not one measured row in it."* Under the corrected scope **2 of 39 are MEASURED** (5534, 5663), and **both entered via `notes_stamp`, neither via `exit_reason`**.

That does **not** contradict the row — under *its* scope the claim holds. It identifies a **mechanism**: a row the filter misses closed under `sl_cross`, and an `sl_cross` gets a broker-truth exit where a `netting_attributed` close never does. So "100% estimated" is partly a property of the filter, not only of the path.

## 6. The row's own question: `insufficient_n`

| era | n graded | median R | values |
|---|--:|--:|---|
| `1_pre_e35` | 6 | 3.022 | 0.815, 1.212, 2.900, 3.143, 3.277, 12.811 |
| `2_e35_only` | 6 | 2.568 | 0.033, 0.259, 0.659, 4.476, 4.811, 7.209 |
| `3_fix_a` | 3 | 0.652 | 0.397, 0.652, 3.315 |
| `4_fix_a_and_b` | 1 | 0.285 | 0.285 |

The post arm holds **4** graded netting winners against the pre arm's own published **n = 11**. **A median over 4 draws is not a distribution**, and publishing one would answer the row with exactly the kind of number it exists to distrust. The direction is down — and at n=4, with one arm being a single trade, that is not evidence.

⚠️ **R is imported, never re-derived.** `trades` carries **no `r` column** — measured, 0 of 1000 — so the first draft of this instrument read a field that does not exist and graded both arms `unmeasurable`, a **false** negative. The owner is `winner_size_collapse_2026_09_12.risk_usd`, which reads the entry-frozen `order_packages.entry`/`.sl`; its docstring records why `trades.stop_loss` is the wrong basis (it is the TRAILED stop, putting a dragged-up distance in a **denominator** — one row read R = 3672 on it). 16 of 16 winners graded, 0 `no_risk_basis`.

## 7. When it becomes answerable

| arm | now | needs | at | date |
|---|---|---|---|---|
| **share** | 151 rows, 5.3% | ~349/arm at 80% power | 18.0 closes/day | **~2026-09-28** |
| **winner-R** | 4 graded winners | n = 11 | 0.48 winners/day | **~2026-10-01** |

⚠️ **Planning figures, not guarantees.** Each takes the observed post rate as the true one; the winner-arrival rate is estimated from **4 events** and carries that noise. They say when to look again, never what will be found.

## 8. What this does NOT establish

* **It does not attribute anything to either fix.** Two landed inside the window and neither separates; the only contrast reaching p < 0.10 is the one that pools them.
* **It does not say the fall vanished.** It says the arm is too small to ask, and names the date it will not be.
* **It does not close the row.** Both halves of option (a) are now *reachable*; neither is *answered*.
* **A fourth confound is stated rather than controlled: the instrument improves across the boundary.** MEASURED share of all closed `bybit_1` rows: **10.4% → 21.0% → 22.8% → 33.3%**. A post arm that looks different may be differently *measured* — the same caveat `winner_size_collapse`'s Phase 1 exists for.
* **Era 1 and era 4 have only PARTIAL soak coverage** (the soak spans 08-29 → 09-16 against a 08-21 → 09-17 pull), so their union counts are under-counts of unknown size. Era 4's is the one that matters: its 4.2% may be **low for a reporting reason**, which cuts against the favourable reading.
* **No parameter is proposed and none may be.**
