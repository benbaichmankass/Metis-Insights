# S-COVERAGE-AND-CASCADE-CLASSIFY-2026-08-22

## Date Range
2026-08-22 (session `s3-exitpath`, second unit of work), continuing from
`S-EXIT-ATTRIBUTION-2026-08-22` (main at `e9dbd7b0`).

## Objective
Operator-approved order: **G.1 first, then 1.7**, plus the stale 0.3 status glyph.
G.1 was promoted ahead of 1.7 because at 24.1% broker-truth coverage the exit picture
is unmeasurable for three-quarters of trades, and both 1.1 and 1.2 are gated on it.

## Tier
Tier 1 throughout. **No code behaviour changed** — the only `src/` edits are five
explanatory comments. No `execution:` change, no Tier-3 gate, no order-path change, no
money-DB write, no live-VM mutation.

## Starting Context
Fresh branch off the merged `main` (`e9dbd7b0`), per the git contract for a designated
branch whose PR has already merged.

## Work Completed

### G.1 — the premise is wrong on all three counts

**(a) `pnlCoverage` is not one number.** `/api/bot/performance` is **real-money only**:
0.78 lifetime (n=400), and `/trades/closed` real money is **180/200 = 0.90**. Over Bybit
rows in the newest 500 closed of *all* classes it splits:

| account | class | n | measured | coverage |
|---|---|--:|--:|--:|
| `bybit_2` | real money | 37 | 23 | **0.622** |
| `bybit_portfolio` | paper | 34 | 10 | 0.294 |
| `bybit_1` | paper/demo | 371 | 30 | **0.081** |

`bybit_1` is **84%** of the closed population, so the alarming `0.06` is a
paper-dominated blend. A coverage figure spanning both funding classes is not a quantity
about anything.

**(b) The windows are nested**, so the 24h/7d/30d/all snapshot cannot show direction —
it conflates recency with the fix. The only valid test is the same window across time,
and at real-money volume (1–5 closes/day) the post-fix cohort aged ≥24h is **n=5**.
**Not answerable**, and recorded as not answerable rather than answered.

**(c) The cause is not fill capture.** `bybit_1` holds **541 fills** over 30d — the most
of any account (bybit_2 90, bybit_portfolio 60), current to 2026-08-22T13:36Z — and
`live_bybit_fill_accounts` explicitly includes paper Bybit accounts. Capture was never
the bottleneck, so the hourly-timer change could not have moved this account.

**The rejecter is the qty gate.** `src/runtime/fills_pnl.py` accumulates fills then
refuses unless `abs(filled - target) <= target * QTY_TOLERANCE`, with
`QTY_TOLERANCE = 0.05`. Under one-way netting **one exchange fill closes a position
spanning N journal rows**, so a single fill overshoots one row's qty:

| account | SOLUSDT | ETHUSDT | XRPUSDT | BTCUSDT |
|---|--:|--:|--:|--:|
| `bybit_1` median fill/trade | **1.40** | **1.44** | **1.31** | 0.94 |
| `bybit_2` median fill/trade | — | 0.14 | 0.17 | 0.80 |

A 31–44% overshoot against a 5% tolerance is **6–9× over** → `return None` →
`candle_at_close`. `bybit_2`'s fills are *partials* that accumulate correctly to the
target, which is why it clears 0.622 on a fraction of the fills.

⚠️ **Stated limit:** those ratios are per-symbol **medians, not a per-trade join**. The
direction and order of magnitude are solid; per-trade confirmation is the next step and
is not claimed.

**Not fixed, deliberately.** Widening `QTY_TOLERANCE` would attach a whole-*position*
fill to one row and stamp `exit_price_source = 'exchange_fill'` — a **MEASURED** grade —
on a row whose economics were inferred. That is strictly worse than the honest
`candle_at_close` it would replace, and it is the same attribution problem
`NETTING_ATTRIBUTION_MODE` already exists to stage annotate-first.

### 1.7 — classified, and my own count was an over-report

⚠️ **Three genuine gaps, not seven.** The first enumeration grepped for
`_cascade_close_linked_package` within 60 lines — **one helper's name** — when the
question is whether the *package gets closed*. Four sites close it by calling
`db.update_order_package` directly, invisible to that grep. **Measuring the mechanism
instead of the effect** is the same class as the rest of this programme's list.

**Correctly handled (4)** — annotated in place with where and with what reason:
`:3140` (the cascade helper) · `:3587` `_close_unattributable_orphan` (package closed at
`:3605`) · `:5250` `_watchdog_stuck_strategies` (force-closed earlier on the **same
branch** at `:5147`; the source comment reads *"Force-close the package + cascade the
trade"*) · `:9054` `_close_options_row` (closed at `:9036`).

**Genuine gaps (3)** — annotated as gaps, not fixed:
- `:2910` adopted-orphan-disappeared. ⚠️ **The intuitive exemption is false and was
  measured false**: *"an adopted orphan has no package"* — but **44 of 74**
  `setup_type='adopted_orphan'` rows carry a non-null `order_package_id`
  (`filter_state` asserted `applied`), and 26 close with exactly this reason. **I
  assumed the exemption; the measurement caught it.**
- `:7734` `_netting_apply_close` — no package reference of any kind; `netting_attributed`
  is 5.3% of main-path closes, so it fires in production.
- `execute.py:1719` intent_reduce — the largest (48 + 10 main, +21 pairs).

**Why not patched:** `_sweep_stuck_linked_packages` has **no age gate**, so a cascade
advances the close by ~1 tick — but the strategy-monocle gate reads *open* packages, so
that tick is a real change to how soon a live strategy may signal again. Tier-2.

**The counter-argument, recorded for the operator:** the sweep fires
`enqueue_stuck_package_sweep` on **every** miss, its own comment saying a non-zero sweep
*"means a PRIMARY cascade path missed … surface it so the gap is visible"*. With pairs at
109 and intent_reduce at ~79 in the window, that alarm has been firing routinely — the
desensitized-alarm shape this repo names as its own P1. Closing the gaps would **quieten a
real alarm**, not merely tidy a label. #10145 already removed the largest contributor.

### 0.3 — stale status glyph
Item 0.3's text read `✅ **DONE.**` and the session log confirmed it, while its Status
column still read `☐`. A session scanning the Status column read it as open. Corrected.

## Validation
- `test_pairs_package_cascade.py` + `test_monitor_reconciler.py`: **128 passed**.
- `ruff`: clean. Guards run before push.
- Every live read asserted `filter_state == "applied"` before its `total` was trusted.
- **Two of my own claims were refuted by measurement in this session** and are recorded
  as such rather than quietly corrected: the seven-gap count, and the adopted-orphan
  exemption.

## What was NOT done, deliberately
No change to `fills_pnl.py`, no `QTY_TOLERANCE` widening, no cascade added to the three
gap sites, no historical re-attribution. All are Tier-2/3 and each would write a
provenance grade or change live gate timing.

## Rows
- `BL-20260822-FILLS-MATCHER-QTY-GATE-BLOCKS-NETTED-ACCOUNTS` → **new**, high
- `BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE` → corrected (7 → 3) + classified

Backlog: health 793 → 794, ml 104, performance 105 — no duplicate ids, asserted by
arithmetic.
