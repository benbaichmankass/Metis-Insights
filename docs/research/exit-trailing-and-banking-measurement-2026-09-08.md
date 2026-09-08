# Exit mechanics — the TRAILING and BANKING half, measured

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **MI-188b** · object [`WO-20260908-EXIT-MECHANICS-THE-TRAILING-AND-BANKING-HALF`](../claude/work/objects/) · cycle `CY-20260906-TRADING-TRUTH`

**MEASUREMENT ONLY.** No parameter is proposed, no exit-matrix cell is re-graded,
no live position was touched. Findings that suggest a change are FILED, not acted on.

The 2026-08-23 exit-matrix verdict (SHIP BLOCKED) covered take-profit / cap
geometry. This is the half it did not cover: **trailing** (does the stop move,
and does it end trades) and **banking** (has a partial scale-out ever executed).

---

## 0. What was read, and what bounds it

| source | how read | population bound |
|---|---|---|
| `trades` | `/api/bot/db/table/trades`, paged `offset` 0→5550 | **complete census, n=5550** — `total` echoed 5550 and 5550 rows were fetched |
| `order_packages` | same, paged 0→4467 | **complete census, n=4467** |
| `position_telemetry` | same | **complete census, n=175** |
| `exit_lever_soak.jsonl` | `/api/diag/log_file?name=…&lines=1000` | ⚠️ **TAIL CAP 1000 of a 1 376 985-byte file** — the last 1000 records only (2026-08-30 → 2026-09-08), **not** the lifetime |
| `exit_ladder_soak.jsonl` | same | ⚠️ **TAIL CAP 1000 of 641 866 bytes** (2026-06-19 → 2026-09-08), **not** the lifetime |

Provenance is `src/runtime/provenance.py::classify_pnl` (imported, not re-derived).
`UNVERIFIED` is never folded into `MEASURED`.

**Decision population** (closed, non-backtest, `pnl NOT NULL`) = **1475**.
`is_backtest` is 0 for all 5550 rows, so the non-backtest clause removes nothing here.

| bucket | n | share of 1475 |
|---|---:|---:|
| measured | 577 | 39.1 % |
| estimated | 468 | 31.7 % |
| unverified | 230 | 15.6 % |
| fabricated | 200 | 13.6 % |

Status split of the census: `rejected` 3277 · `closed` 1668 · `exchange_rejected` 434 ·
`orphaned` 131 · `open` 31 · `rejected_too_small` 6 · `error` 3.

---

## 1. How often did a trailing amend actually fire on a live position?

### The count itself is NOT ESTABLISHED, and that is a finding about the instrument

**No durable per-amend record exists anywhere.** Traced through
`order_monitor._apply_update`'s `KIND_MODIFY` branch (`src/runtime/order_monitor.py`
≈ L1237–1470), an applied trailing amend leaves exactly four traces:

1. `db.update_order_package(pkg_id, updates)` — **overwrites** `order_packages.sl`/`tp` in place. No history row.
2. `trades.stop_loss` / `take_profit_1` — likewise **overwritten** in place.
3. `logger.info(...)` — systemd journal only. This repo has **measured that journal's retention at ~30 minutes** (CLAUDE.md, `work_decision_sweep_receipt`).
4. `enqueue_trade_update(...)` — a Telegram ping, drained and gone.

Verified absence: **zero** `log_signal_event` / `log_outcome` calls in L1230–1480.
`trades.protection_repairs` is documented as *deliberately excluding* the ordinary
trailing amend. So the question "how many amends fired" cannot be answered from
any surface that outlives the tick.

### What CAN be established: a lower bound on packages whose stop demonstrably moved

`order_packages.exit_plan` carries the entry-time `stop.price` and — verified by
grep — **has no update site anywhere in `src/`**; it is written once at package
creation. Comparing it to the current `order_packages.sl` yields a defensible
detector.

**Population: all 4467 order packages, complete census.** Three states, not collapsed:

| state | n | share |
|---|---:|---:|
| **stop moved** (≥1 amend landed) | **140** | 3.1 % |
| stop unmoved | 1958 | 43.8 % |
| **ungradeable — no entry stop recorded** (*we cannot look*) | 2369 | 53.0 % |

**Of the gradeable 2098: 140 moved = 6.7 %.**

⚠️ **The gradeable set is a TIME WINDOW, not a random sample.** Ungradeable packages
were created **2026-05-02 → 2026-07-16** (1586 in May, 780 in June, 3 in July);
gradeable ones **2026-06-18 → 2026-09-08**. The `exit_plan` column was migrated in
around mid-June. So this reads *"since 2026-06-18"*, and says nothing about May.

**Positive control — the moves are real trailing, not noise.** Of the 140 moves,
**140 are favourable and 0 are adverse**: 92 stops raised on longs, 48 lowered on
shorts. A ratchet is exactly what that looks like.

⚠️ **This is a LOWER BOUND on packages, not a count of amends.** N amends on one
package collapse to one observation, and an amend that moved a stop and moved it
back is invisible. The true amend count is ≥ 140 and unbounded above by this method.

---

## 2. What actually ENDED the trade — trailing stop vs declared TP vs other

### `exit_reason` alone cannot answer this, by construction

**There is no `trailing_stop` exit reason.** A trailed stop and an untouched
original stop both fire as the same broker-side stop and land as `sl` / `sl_cross`.
Independently, this repo already records that `exit_reason` misdescribes *why*
(trade 4904 reads `reconciler_filled` for an operator flatten).

Over `status='closed'`, **n=1668, complete census**, the largest bucket is
`reconciler_filled` at **467 (28.0 %)** — a label that does not name a cause.
`sl` 191 · `sl_cross` 113 · `vwap_cross` 102 · `exchange_flat_reconciled` 100 ·
`reconciler_incomplete` 93 · `pairs_revert` 88 · `intent_reduce_executed` 85 ·
`pairs_stop` 78 · **`tp` 64** · `pairs_half_open_cleanup` 59 · `netting_attributed` 49 ·
`intent_reduce` 39 · … · `tp_cross` 19 · **`stale_stop` 14** · `exit_head` 9 ·
**`giveback_stop` 5** · `time_decay` 4.

### Answering it by joining to the trailed set

**Population: the 185 closed trades on the 140 demonstrably-trailed packages**
(2026-06-18 → 2026-09-08; 185 > 140 because multi-leg packages carry several trades).

| what ended it | n | share of 185 |
|---|---:|---:|
| `reconciler_filled` — **does not name a cause** | 49 | 26.5 % |
| **declared TP** (`tp` 26 + `tp_cross` 6) | **32** | **17.3 %** |
| **stop-out** (`sl` 23 + `sl_cross` 13) | **36** | **19.5 %** |
| `exchange_flat_reconciled` | 21 | 11.4 % |
| `netting_attributed` + `netting_phantom_reconciled` | 22 | 11.9 % |
| `intent_reduce*` | 13 | 7.0 % |
| `giveback_stop` | 5 | 2.7 % |
| `stuck_strategy_watchdog` | 4 | 2.2 % |

Provenance over those 185 (`pnl NOT NULL`): estimated 102 · measured 47 ·
fabricated 14 · unverified 3.

### Decisive test — did the stop-outs hit the TRAILED level or the original?

For each of the 36 stop-outs, comparing `exit_price` against both the entry-time
stop and the final stop:

| | n |
|---|---:|
| **exited at the TRAILED stop** | **33** |
| exited at the ORIGINAL stop | 3 |

Typically within a fraction of a percent of the trailed level and percent-scale
away from the original — e.g. trade 3577 (`xrp_pullback_2h`, long): original
1.0655, trailed to 1.1116, exited 1.1115 (0.009 % from trailed, 4.317 % from original).

**So: the trail is real, it works, and on the trailed population it ends trades
slightly more often than the declared TP does (33 confirmed trailed stop-outs vs
32 TP exits).** Both are dwarfed by the ~59 % of that population closed by
reconciler / netting / intent-reduce paths that are not strategy exits at all.

⚠️ Comparison bound: this is the *trailed* population by construction. It is not a
claim about the fleet, and it is not an A/B of trailing against its own absence.

---

## 3. Has a partial bank ever occurred at all? — **NO. Zero.**

**Population: all 5550 trade rows, complete census.**

| probe | result |
|---|---:|
| rows whose `notes` carry a non-empty `partial_closes` list | **0** |
| rows where the `partial_closes` key exists at all, even empty | **0** |
| `exit_reason` containing `partial` | **0** |
| `exit_reason` containing `tp1` | **0** |
| trades with a positive `take_profit_2` | **0** |

### The denominator that makes the zero meaningful

The zero is **not** evidence that the banking mechanism is broken. It is evidence
that the mechanism **has never been reachable**. Four independent gates, each
sufficient on its own:

1. **Only one strategy can emit a partial verdict.** `close_qty_pct` is produced at
   exactly one site in `src/`: `turtle_soup.py:537` (`reason: "tp1_partial"`).
2. **That strategy has never opened a position.** turtle_soup has **3 rows in the
   entire history** (2026-05-10 → 2026-07-01, all `bybit_1`): 2 `rejected`,
   1 `exchange_rejected`. It never reached the monitor, so it never reached TP1.
   It is now `execution: shadow` — 1 of 55 strategies, and it trades nowhere live.
3. **The ladder precondition has been met 3 times, ever.** A rung requires
   `meta.tp2` distinct from `tp` (`exit_plan.py` L293). Across **all 4467 packages**,
   exactly **3** carry a `tp2` — all three turtle_soup, statuses `orphaned`,
   `orphaned`, `rejected`. None reached a managed exit.
4. **`partial_close` is a bybit-only capability.** `EXCHANGE_MANAGEMENT_CAPS`
   (`src/units/accounts/clients.py`) grants it to `bybit` alone; IB, alpaca, oanda
   and breakout all resolve to unsupported.

### And the soak built to justify shipping it is returning a null result

`exit_ladder_soak` exists to accrue, per executed order, the laddered exit that
*would* be used next to the single target actually placed — the pre-flight evidence
for graduating the ladder (its own docstring: *"Nothing reads it back… graduating
the ladder is the behaviour-changing, backtest-gated step"*).

Over its last **1000 records** (⚠️ tail cap, 2026-06-19 → 2026-09-08, **51 distinct
strategies**, venues `api` 945 / `prop` 55):

- `ladder.n_rungs` = **0 for all 1000**
- `differs_from_single_target` = **False for all 1000**

Every derived "ladder" collapses to the single target already placed. The soak has
accrued **zero evidence of a ladder to graduate to**, and nothing reads it back to
notice. Same class as `BL-20260826-TARGET-EXTENSION-SOAK-IS-100PCT-SENTINEL-AND-CANNOT-YET-OBSERVE-THE-LEVER`, on a
different soak.

---

## 4. Instrument defects found (FILED, not fixed)

**(a) `exit_lever_soak.mode` is a hardcoded literal, and reads as a mode.**
`record_exit_lever_annotation` writes `"mode": "annotate"` unconditionally
(`exit_lever_soak.py:64`); no other value is reachable. All 1000 tail records
therefore read `annotate` — while `stale_stop` (14) and `giveback_stop` (5) were
closing real trades *inside that same window* (through 2026-09-02).

The real gate is `declared_bars is not None` — whether the strategy DECLARES the
lever in YAML. **Declared ⇒ the lever ACTS and writes NO soak row. Undeclared ⇒ it
observes and writes one.** So the file is a log of the lever **NOT** firing, and
"100 % annotate" — all 1000 records of the tail described above — is a
statement about the writer, not about the fleet.

This is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A as CLAUDE.md defines it, in a
surface a review session is expected to read. I nearly filed the inverted finding
("the trailing levers are all in annotate mode and none of them apply") — which is
false, and would have been acted on.

**(b) `position_telemetry.levers` is a declaration, not a firing record.** 59 of 175
rows carry a non-empty `levers` (`trail_decay_arm_r` ×55, `giveback_min_mfe_r` ×4).
`_lever_view` (`position_telemetry.py:232`) returns *"which R-threshold levers this
package DECLARES, and their arms"* — the configured threshold, echoed. It counts
nothing. Both fields are one obvious misreading away from a wrong fleet-level claim.

**(c) The trailing amend has no durable half** (§1) — the tick-cost shape of
`BL-20260816-TICK-COST-HAS-NO-DURABLE-HALF-SO-A-BUSY-MERGE-DAY-CANNOT-BE-MEASURED`, on the exit path.

---

## 5. What is NOT established

Stated explicitly rather than filled in:

- **The number of trailing amends.** Only a lower bound on *packages* (≥140 since 2026-06-18). Not the amend count, and nothing at all before 2026-06-18.
- **Any May-2026 trailing behaviour.** 53 % of packages carry no entry-time stop; those are almost entirely May/June and are `ungradeable`, never `unmoved`.
- **The lifetime of either soak.** Both reads are 1000-line tails of multi-hundred-KB files. The ladder's `n_rungs=0` is airtight only because the **census** of `meta.tp2` (3 of 4467) independently confirms it — the tail alone would not carry it.
- **Whether trailing HELPS.** This measures what the trail did, not whether the book is better for it. No lever-OFF arm was run; that is `BL-20260814-NINE-SHIPPED-LEVERS-NEVER-GRADED-AGAINST-THEIR-OWN-ABSENCE`.
- **Why 3 of 36 trailed stop-outs exited nearer the ORIGINAL stop.** Not investigated; n is small.
- **What `reconciler_filled` actually was** in the 49 trailed cases (26.5 %). The label does not name a cause, and `BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE` already records that these are not re-classified once the price lands.

---

## Reproducing

```bash
curl -s -H "Authorization: Bearer $DIAG_READ_TOKEN" \
  "https://ict-bot.duckdns.org/api/bot/db/table/trades?limit=500&offset=N&order_by=id&order_dir=asc"
```
Page `offset` to `total`; assert fetched == `total` before calling it a census.
`scripts/ops/diag_fetch.sh` mangles non-`/api/diag/*` paths (404s on
`/api/bot/db/...`) — curl the Caddy host directly for those.
