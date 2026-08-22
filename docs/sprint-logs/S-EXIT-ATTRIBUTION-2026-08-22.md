# S-EXIT-ATTRIBUTION-2026-08-22

## Date Range
2026-08-22 (single session, `s3-exitpath`), continuing directly from
`S-EXIT-PROTECTION-CLUSTER-2026-08-22` (`s2-exitclust`, main at `ae31ddab`).

## Objective
Workplan **item 1.1 — the exit path itself**, per the operator's 11:1xZ ordering (*bugs
and technical blockers before research*), with items **1.5** and **1.6** folded in as
inputs rather than run as separate fronts.

## Tier
Tier 1 for all measurement. **One Tier-2 change shipped**: a package-bookkeeping cascade
in the pairs close path. It places, modifies and cancels **nothing** — it is the same
`order_packages` write the stuck-cascade sweep already performed, made timely and
honestly-reasoned. No `execution:` change, no Tier-3 gate, no account mode, no model
promoted, no live-VM mutation. **The stuck-cascade sweep was NOT changed**, per item 1.6's
own instruction.

## Starting Context
`docs/claude/WORKPLAN-2026-08-21.md` is the queue; the 11:1xZ operator block is current.
Board tail **proved** at `perPage=5, page=257` → **short page of 4**, newest the
predecessor's own release at 13:31:11Z; sole session, slot free, no open 🔒.

## Repo State Checked
Branch at `ae31ddab` = `origin/main` exactly (0 ahead / 0 behind, `git rev-list --count`
both directions). The live web-api serves `ae31ddab` (`/api/diag/version`), so every read
below is against deployed code. ⚠️ Clone **unshallowed first** (50 → 3,551 commits) before
any `git log -S` or file-age read.

## Files and Systems Inspected
- `src/units/strategies/pairs_executor.py` — `_close_pair`, `_open_pkg_meta`, `run_pairs_tick`
- `src/runtime/order_monitor.py` — `_cascade_close_linked_package`, `_close_trade_from_order_status`, `_classify_broker_exit`, `_resolve_linked_package_id`, `_sweep_stuck_linked_packages`
- `src/runtime/pipeline.py` — `_has_open_package_for_strategy` call site, the `pairs_` monitor-unit resolution
- `src/units/accounts/execute.py` — the `intent_reduce` parent-chunk close
- Live: `/api/diag/version`, `/api/bot/db/table/trades`, `/api/bot/db/table/order_packages`

## Work Completed

### 1. Item 1.6 — TRACED, and its feared half REFUTED

The mechanism is no longer a hypothesis. `_close_pair` writes `status='closed'` straight
to the trade row and therefore never routes through
`order_monitor._close_trade_from_order_status`, which is the **only** place
`_cascade_close_linked_package` runs. Grep confirms **zero** cascade calls anywhere in
`pairs_executor.py`.

The live arithmetic closes **exactly on both arms** (newest 500 closed non-backtest rows,
`filter_state` asserted `applied`):

| | trades | packages |
|---|--:|--:|
| closed via the monitor (cascades) | 57 | 57 `reconciler_filled` |
| closed via `_close_pair` / `intent_reduce` (no cascade) | 120 | 120 (109 swept + 11 not yet) |

`pairs_revert` / `pairs_stop` / `pairs_half_open_cleanup` appear on **99 trade rows and
ZERO package rows**. Concentration: pairs **109/177 = 61.6%** `stuck_cascade_recovered`
against main **15/323 = 4.6%** — a **13.4×** gap, which is what rules out a tick-ordering
race (that would be uniform).

**The row's open question is answered NO by a second, independent route.** It asked
whether the strategy-monocle gate governs the isolated pairs path — i.e. whether the
sleeve depends on the repair sweep to un-block itself each tick. It does not:
`_has_open_package_for_strategy` is consulted at exactly one site (`pipeline.py:611`, the
pipeline's signal dispatch), and pairs legs are **not in the strategy roster** —
`run_pairs_tick` (`main.py:864`) owns them and never reaches that site. **The gate cannot
see a pairs leg.** So the larger feared finding does not hold; this was bookkeeping.

### 2. Item 1.6 — FIXED

`_cascade_close_pair_package`, called from `_close_pair`, forwarding the leg's own
`f"pairs_{outcome}"` reason. Guarded at **both** the helper and the call site so the
isolation is structural rather than conventional: a package-write failure must never reach
`_close_pair`'s `except`, which sets `closed_ok = False` and would report a leg that **is
flat at the broker** as a failed flatten.

**Generalised, and this is bigger than pairs:** only **1 of 8** literal `status='closed'`
call sites cascades. ⚠️ That 8 is a **lower bound** — 20 further `update_trade` sites pass
a dict *variable* whose status the matcher cannot read. Filed rather than swept.

### 3. Item 1.1 — re-measured on broker truth, and the framing INVERTS

Full method and caveats: `docs/research/exit-attribution-broker-truth-2026-08-22.md`.

Of **323** main-path closes only **78 (24.1%)** carry a broker-truth exit price; the other
245 are estimated/fabricated and are **UNKNOWN** — excluded, not counted. Judged by
`_classify_broker_exit`'s **own** conservative inequality:

| | n | share |
|---|--:|--:|
| crossed the declared **stop** | 40 | 51.3% |
| crossed the declared **target** | 6 | 7.7% |
| between the two | 32 | 41.0% |
| **reached a declared level** | **46** | **59.0%** |
| **labelled** decision-driven | **15** | **19.2%** |

**An under-attribution gap of 31 rows — the brackets reach a declared level about three
times as often as the journal records it.** Item 1.1 is, on this population, substantially
an **attribution** defect rather than a mechanism one.

**What survives of the original framing:** the stop:target skew is real — **40 : 6 =
6.7 : 1** on actual fills — and **41.0%** of measurable exits genuinely landed between the
brackets.

## Validation

- **Falsification, both directions.** `tests/test_pairs_package_cascade.py`: **7/7 FAIL**
  against the pre-fix file (the source was stashed and the absence of the call verified at
  0 occurrences), **7/7 pass** after. Regression: `test_pairs_*` + `test_pair_*` 89 passed
  / 1 skipped; `test_monitor_reconciler.py` 121 passed.
- **The DDL is lifted from `src/units/db/database.py`**, never hand-declared — the fixture
  additionally asserts `linked_trade_id` exists, so a migration that drops it fails the
  test rather than silently passing against a fictional table.
- **My own test caught a real weakness in my own fix.** The first version's isolation
  depended on the helper's internal guard; the end-to-end test failed, and the call site
  now guards itself.
- **Two candidate causes for the mis-attribution were REFUTED, not merely unselected.**
  *Wrong level source*: the classifier reads `order_packages.sl/tp`, 0 of 44 rows had a
  null package stop, and the two sources give an identical verdict on 43 of 44. *A
  different close path*: `closed_by` is `monitor_reconciler` and `closed_reason` the
  identical string on **both** the crossed and non-crossed groups.
- ⚠️ **I retract one of my own numbers.** A first pass used a symmetric ±0.10-risk-unit
  band and reported **72.7%** at a bracket. That is wrong — a symmetric band counts a fill
  that stopped *short* of the stop as a stop hit. The production inequality gives
  **59.0%**. The retraction is in the research doc, the backlog row and the workplan so the
  bad number cannot be re-quoted from any of them.
- ⚠️ **The backfill hypothesis is UNDERPOWERED and is labelled so, not quoted as a
  result.** 18 of 21 mis-attributed rows carry a 2026-08-08 price-backfill marker, but the
  2×2 is 18/3 vs 15/8 — **Fisher exact one-sided p = 0.111 at n=44**.

## What was NOT done, deliberately
- **No historical `exit_reason` repair.** Re-stamping 31 money-DB rows on a p=0.111
  hypothesis is the fabrication class this repo already pays for.
- **The stuck-cascade sweep is untouched** — it is what keeps the sleeve working.
- Criterion 4 (price axis on the *enforcing* path), #10081's apply-mode repair, the Alpaca
  read surface, and the MES stop divergence — all still operator-gated and untouched.
- The scheduled **Sunday 2026-08-23 22:30Z** session (`trig_014S3NAzMKy2Ac2AM2GgyRE5`,
  MES attach + MGC flatten) — **not** this session; neither duplicated nor pre-empted.

## Owed
**The live positive control on the pairs fix.** After deploy, a pairs close must produce a
package carrying `pairs_revert` / `pairs_stop` / `pairs_half_open_cleanup` instead of
`stuck_cascade_recovered`. Pairs close ~3/day on this population, so one should appear
within a day. Until that read exists the fix is verified **by unit test only**, and this
log says so rather than claiming a live result.

## Rows
- `BL-20260822-PAIRS-PACKAGES-CLOSED-BY-THE-STUCK-CASCADE-SWEEP` → **resolved** (traced + fixed; live control owed)
- `BL-20260822-EXIT-ATTRIBUTION-UNDER-REPORTS-BRACKET-HITS` → **new**, high
- `BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE` → **new**, medium

Backlog: health 791 → 793, ml 104, performance 105 — **no duplicate ids**, asserted by
arithmetic at every write.
