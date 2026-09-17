# M20 U49 — the corrected netting scoping rule, applied; and U47 audited against it

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U49** · RESEARCH lane · Tier-1 · works `BL-20260913-NETTING-ATTRIBUTION-REWRITES-POSITION-SIZE-ON-A-ROW-THAT-STAYS-OPEN-SO-THE-EXIT-REASON-FILTER-EVERY-ANALYSIS-USES-CANNOT-SEE-IT` (performance backlog, `severity: high`, `status: open`).

**No new instrument was written, deliberately.** The row names
[`scripts/research/netting_position_size_basis.py --soak`](../../scripts/research/netting_position_size_basis.py)
as the reference implementation, and it already grades exactly this with a
four-state two-source vocabulary. Building a second one would be
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`. **This unit is a USE**, which is
what the row's clause (1) asks for — and it turns that instrument on the lane's
own most recent finding.

---

## Input provenance

* `GET /api/diag/journal?table=trades&limit=1000`, read 2026-09-17T12:4xZ — `sha256:098092a13d8efff290b8eefb33fe543dd42490927c07694264ba861db2dda509`, ids **4873 → 5872**.
* `GET /api/diag/log_file?name=netting_attribution_soak&lines=1000` — `sha256:225847107f0cd73dd45dd031bee7fe176bfea134fe7064a9791b37145d2ebf41`, window **2026-08-29T08:11:10Z → 2026-09-16T07:12:15Z**, a 1000-line tail of a **3,882,168-byte** file.
* Live trader at `git_sha c979c0f70` (`git_sha_on_disk` equal, `restart_pending: false`).

⚠️ **Both inputs are TAILS and both roll off by construction**, which the row warns about by name. Every count below is a **lower bound over the window its source covers**. A row reduced before either window is *unread*, not absent.

---

## 1. What clause (1) asked for, and what was done

> *"a session excluding or flagging netting contamination keys on the notes stamp AND the soak, not on `exit_reason` alone, and says so."*

**Done, and the analysis it was applied to is MI-278 U47** — the lane's immediately-preceding unit, which graded netting as the one candidate that `contributes` to the `bybit_2` risk dispersion. U47 keyed on the **notes stamp** (`netting_attribution_basis`, `netting_attributed_qty`) and **not** on `exit_reason`, which is the correct half. **It did not read the soak**, which is the half this row says is not enough.

So U47 is the natural thing to audit, and the audit could have gone either way.

### The result: U47's count is CONFIRMED, not under-counted

U47's population re-derived on today's pull — `bybit_2`, closed, non-backtest, `pnl NOT NULL`, `closed_at ≥ 2026-08-18T11:43:52`, **n = 33**:

| scoping | rows | ids |
|---|--:|---|
| `exit_reason == 'netting_attributed'` | 4 | — |
| notes **stamp** (what U47 used) | **4** | 4886, 4922, 5080, 5461 |
| **soak** `mode=apply` | 1 | 5461 |
| **union** (stamp OR soak) | **4** | 4886, 4922, 5080, 5461 |
| **soak-only** (stamp shed) | **0** | — |

**The soak adds nothing on `bybit_2`, so U47's "4 of 33" stands and its `contributes` verdict is unchanged.** The one soak-applied row it reaches (5461) was already stamped.

⚠️ **That is a confirming result and it is reported as one.** It is not evidence the defect is absent — it is evidence the defect did not reach *this* account in *this* window. All five shed rows below are `bybit_1` and `bybit_portfolio`.

---

## 2. The fleet census on today's pull

`netting_position_size_basis.py --journal … --soak …`, scope `bybit*`, **783 rows graded** (217 off-venue, not graded):

| | rows |
|---|--:|
| `qty_basis: observed` | 635 |
| `qty_basis: assigned_by_attribution` | 44 |
| `qty_basis: evidence_may_be_shed` — ***we could not look*** | **104** |

| two-source grade | rows |
|---|--:|
| `stamp_and_soak` | 28 |
| `stamp_only` (soak tail does not reach it) | 16 |
| **`soak_only` — the stamp was shed** | **5** |
| `neither` | 734 |

* rows `exit_reason` sees: **42** · rows carrying a stamp: **44** · **the filter misses 2**
* **invisible to `exit_reason`, both sources combined: 7** — 2 stamped-but-mislabelled + 5 stamp-shed
* **the journal alone sees 2**, so a journal-only audit under-counts by **3.5×**

### The five shed rows

| trade | account / symbol | strategy | exit_reason | removed | pnl |
|---|---|---|---|--:|--:|
| **5744** | `bybit_1` BTCUSDT | `squeeze_breakout_4h` | `reconciler_filled` | 90.3% | +0.7941 |
| 5687 | `bybit_1` ETHUSDT | `trend_donchian_eth` | `sl` | 39.0% | −230.9136 |
| 5569 | `bybit_1` ETHUSDT | `ict_scalp_eth_15m` | `reconciler_filled` | 98.2% | +8.4036 |
| 5479 | `bybit_1` ADAUSDT | `ada_pullback_2h` | `reconciler_filled` | 79.9% | −95.472 |
| 5404 | `bybit_portfolio` ETHUSDT | `eth_pullback_2h` | `sl` | 99.5% | −4.962 |

⚠️ **Trade 5744 is new relative to the row's own 2026-09-13 reading, and that is a WINDOW effect, not growth.** That reading covered ids 5731 → 4732; 5744 did not exist inside it. **This is not progress and must not be reported as any**, which the row states explicitly: *"a smaller count on a later pull is not progress either… report the denominators or do not report the count."* Four of the five are the same rows.

---

## 3. A sharper statement of the mechanism than the row makes

The row says the two keys are "droppable". Reading `json_notes._shrink_dict`, they are droppable in **two different ways**, and only one of them is the failure the module's comment already describes:

* `netting_attribution_basis` is a **short string** (`"leg_gone"`, `"fifo"`). `_shrink_dict` trims the **longest unprotected string** first, so on a crowded blob it is eventually trimmed to a prefix — a value matching neither vocabulary entry. That is `BL-20260826-EXIT-REASON-SOURCE-TRUNCATED-BY-THE-NOTES-CAP` verbatim.
* `netting_attributed_qty` is a **float**. `_shrink_dict` only ever trims *strings*, so it can never be trimmed — it can only be **dropped whole** by the second loop, which sheds the largest unprotected key once no trimmable string remains.

Both are covered by the same one-line remedy, and **the argument for it is the module's own**, not mine. `json_notes.py` states:

> *"⚠️ EVERY PROVENANCE MARKER BELONGS HERE … A provenance marker is a SENTINEL: a consumer compares it for equality against a fixed vocabulary, so a trimmed value is not a shorter answer — it is an unreadable third state that matches nothing."*

`netting_attribution_basis` satisfies that criterion exactly: its vocabulary is `leg_gone` / `fifo` (`order_monitor.py:9366`, documented again at `diag.py:544`) and consumers compare against it. The tuple's own comments already record **three** prior instances of this class (`exit_reason_source`; `closed_by_operator` + `pre_mark_exit_reason`). **This is the fourth, and the rule that would have prevented it is written in the file it belongs in.**

---

## 4. What this does NOT establish

* **No published figure is restated and no close is claimed false.** That needs the venue-side read `OI-20260908` specifies; option (b) of the parent row is exhausted (U9 + U16) and was not re-attempted.
* **Clause (2) is NOT cleared and cannot be by this unit** — adding the keys to `_DEFAULT_PROTECTED` is **Tier-2** runtime, and it is put to the operator rather than applied. ⚠️ **It would not recover the rows already shed**, only stop the next ones.
* **The 104 `evidence_may_be_shed` rows are bounded, not resolved.** The soak settles 5 of them; the rest sit before its window.
* **Re-running the instrument clears nothing** — the row says so, and today's 7-vs-6 is a rolled window, not a change in the system.
