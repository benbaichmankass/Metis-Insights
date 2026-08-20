# E0 census, live half — the journal does not record why most trades exit

**Date:** 2026-08-20 · **Step:** E0 of
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)

**Population — state it before reading any share.** `trade_journal.db::trades`
on the trainer's synced copy (`/home/ubuntu/ict-trading-bot/data/trade_journal.db`,
mtime 2026-08-20T05:00:17Z, 913 MB), filtered to `status='closed' AND
COALESCE(is_backtest,0)=0`. **1226 rows, 53 legs, 26 distinct `exit_reason`
values.** Real money 438, paper 788. Pulled via trainer-diag
[#9988](https://github.com/benbaichmankass/Metis-Insights/issues/9988).

⚠️ **The first attempt ([#9986](https://github.com/benbaichmankass/Metis-Insights/issues/9986))
returned `LEGS 0` and was nearly reported as a finding.** It had fallen back to
`/home/ubuntu/ict-trading-bot/trade_journal.db` — a **stray 8 MB journal with
`trades_total 0`** — because the trainer has no `/data/bot-data`. Zero rows read
as "no exits recorded" and are actually "we opened the wrong file". This run
enumerates every candidate journal with its row count **first**, so the
denominator is visible before any share is computed.

---

## 1. The headline

**781 of 1226 live closes (63.7%) carry an `exit_reason` that the producer
itself declares it could not classify.** Real money: **235 of 438 (53.7%)**.

This is not a taxonomy quibble imposed from outside. `order_monitor.py:6032`
says it in its own comment:

> *"Fallback path has no recovered exit price, so sl/tp cannot be classified —
> keep the generic reconciler tag."*

The close path **tries** to resolve `sl` vs `tp` via `_classify_broker_exit` and
falls back to `reconciler_filled` when the exit price is unrecoverable. So
`reconciler_filled` is a correctly-implemented *"we could not look"* — and it is
the **modal value of the entire live book**.

| class | what it means | n | share |
|---|---|--:|--:|
| **unclassified by producer** | the journal does not record what decided the exit | **781** | **63.7%** |
| path | post-entry market information decided it — an actual exit MECHANISM | 181 | 14.8% |
| bracket | a price level fixed at ENTRY decided it (`sl`/`sl_cross`/`tp`/`tp_cross`) | 129 | 10.5% |
| portfolio | the intent layer / another position decided it (`intent_reduce*`) | 105 | 8.6% |
| infrastructure | a watchdog, an operator, or a cleanup decided it — not a trading rule | 26 | 2.1% |
| clock | a timer fixed at entry (`time_decay`) | 4 | 0.3% |

The unclassified bucket splits into two genuinely different states, which is why
it is not reported as one number:

| sub-state | reasons | n | share |
|---|---|--:|--:|
| **venue flat, which leg unknown** | `reconciler_filled` 541 · `reconciler_incomplete` 93 | 634 | 51.7% |
| **cause not recorded at all** | `exchange_flat_reconciled` 79 · `adopted_orphan_disappeared` 26 · `backfill_closed_pnl_recovery` 21 · `netting_attributed` 8 · `netted_misattributed` 5 · `netting_phantom_reconciled` 4 · `exit_coverage_no_strategy` 4 | 147 | 12.0% |

`reconciler_incomplete` is stronger than its name suggests: per
`scripts/ops/mark_reconciler_incomplete.py`, it is stamped on rows that are
`closed AND pnl IS NULL` **after** the Bybit backfill attempt failed — so those
93 rows have neither a cause nor a P&L.

## 2. What E0 can and cannot conclude from this

**It CAN conclude:** the live journal is not a substrate on which "who decides
exits" is answerable for most trades. Any per-leg exit-reason distribution taken
off it is being read over a population whose modal value means *unknown*, and
until now nothing stated that.

**It CANNOT conclude** that path exits are 14.8% of live behaviour. That is a
**lower bound**. Of the 445 closes the journal *does* classify, path is 40.7% —
also not the answer, because the unclassified 781 are not a random sample (they
concentrate in the venue-bracket path, which is precisely the class that fails to
resolve an exit price). The two figures bracket the truth and neither is it.

**The harness half is a different population and stays separate.** The 78.5%
bracket-decided figure in the process doc § 0.1 comes from 284 `xrp_pullback_2h`
*backtest* trades, where the harness records the exit reason it itself chose and
no reconciler exists. That population can answer the question; the live one
cannot. Reporting them as one number would be the mistake this document exists
to avoid.

## 3. Per leg — every gradeable leg, unknown share stated

Legs with n ≥ 20, sorted by how much of the leg is unknown.

| leg | n | unknown | path | bracket |
|---|--:|--:|--:|--:|
| `orphan_adopt` BTCUSDT | 23 | **100.0%** | 0.0% | 0.0% |
| `squeeze_breakout_4h` BTCUSDT | 62 | **98.4%** | 0.0% | 0.0% |
| `ict_scalp_avax_5m` AVAXUSDT | 20 | **95.0%** | 0.0% | 0.0% |
| `ict_scalp_5m` BTCUSDT | 57 | 87.7% | 0.0% | 8.8% |
| `htf_pullback_trend_2h` BTCUSDT | 58 | 82.8% | 0.0% | 1.7% |
| `ada_pullback_2h` ADAUSDT | 32 | 78.1% | 0.0% | 3.1% |
| `trend_donchian` BTCUSDT | 50 | 78.0% | 8.0% | 4.0% |
| `slv_trend_1h` SLV | 22 | 77.3% | 0.0% | 18.2% |
| `sol_pullback_2h` SOLUSDT | 22 | 68.2% | 0.0% | 0.0% |
| `trend_donchian_ada_4h` ADAUSDT | 22 | 68.2% | 0.0% | 4.5% |
| `eth_pullback_2h` ETHUSDT | 43 | 67.4% | 0.0% | 14.0% |
| `xrp_pullback_2h` XRPUSDT | 23 | 65.2% | 0.0% | 17.4% |
| `vwap` BTCUSDT | 362 | 52.8% | 28.2% | 17.1% |
| `pairs_sol_eth_b` ETHUSDT | 41 | 39.0% | 31.7% | 0.0% |
| `pairs_bnb_btc_b` BTCUSDT | 31 | 32.3% | 58.1% | 0.0% |
| `pairs_sol_eth_a` SOLUSDT | 41 | 31.7% | 31.7% | 0.0% |
| `pairs_bnb_btc_a` BNBUSDT | 32 | 25.0% | **59.4%** | 0.0% |

**Eleven of seventeen gradeable legs have ZERO recorded path exits.** The two
sleeves that do carry a real mechanism are the ones whose exit rule is the
strategy itself: `vwap` (28.2%, `vwap_cross`) and the market-neutral **pairs**
sleeve (31.7–59.4%, `pairs_revert`/`pairs_stop`). Across the crypto pullback and
donchian family — the legs ~20 M20 lever cells were swept against — the entire
recorded path total is **four `exit_head` closes on `trend_donchian` BTCUSDT**
(8.0% of that leg); every other gradeable leg in the family records none. Two
family legs do carry lever fires but fall below the n ≥ 20 floor and so are not
in the table: `trend_donchian_xrp_4h` (`stale_stop` 3 of 15) and
`ict_scalp_eth_15m` (`stale_stop` 7 of 13).

## 4. The consumer half: the only exit-reason surface renders this as "other"

`/api/bot/strategies` is the one place an exit-reason distribution is published,
via `_normalise_exit_reason` (`src/web/api/routers/strategies.py:205`). Applied
to the same 1226 rows it renders:

| bucket | n | share |
|---|--:|--:|
| `reconciler` | 634 | 51.7% |
| **`other`** | **357** | **29.1%** |
| `sl` | 111 | 9.1% |
| `vwap_cross` | 102 | 8.3% |
| `tp` | 18 | 1.5% |
| `time_decay` | 4 | 0.3% |

**`other` is 29.1% and contains eighteen distinct reasons** that are not alike:

- **the M20 levers themselves** — `stale_stop` 10, `exit_head` 4,
  `giveback_stop` 2. The entire subject of this workstream is invisible on the
  only surface that reports exits.
- **real portfolio decisions** — `intent_reduce_executed` 68, `intent_reduce` 37
- **real pairs-sleeve decisions** — `pairs_revert` 34, `pairs_stop` 29
- **orphan and netting artifacts** — `exchange_flat_reconciled` 79,
  `adopted_orphan_disappeared` 26, `backfill_closed_pnl_recovery` 21,
  `netting_*` 17, `exit_coverage_no_strategy` 4
- **infrastructure closes** — `pairs_half_open_cleanup` 14,
  `stuck_strategy_watchdog` 8, `manual_closeall` 3,
  `operator_flatten_reconciled` 1

A lever firing and an unreconciled orphan land in the same bucket, so no reader
of that surface can tell them apart — and a lever that fires ten times is
indistinguishable from one that never fires.

## 5. What this changes

1. **E0's falsifier is met, and then some.** The process doc says *"if >70% of
   exits are bracket-decided, the leg has no exit mechanism and steps E3+ are
   premature."* On the live book the stronger statement holds for eleven of
   seventeen legs: **not one recorded path exit**. Sweeping more lever cells at
   those legs before they have a mechanism at all is the premature step E0 exists
   to catch.
2. **The measurement substrate has to be fixed alongside E1.** A lever whose
   fires are recorded as `other` cannot be evaluated in production even if the
   backtest passes — E5's annotate-soak would be grading against a field that
   cannot represent the outcome.
3. **`vwap` and the pairs sleeve are the working examples.** Both exit on a
   post-entry market condition that the strategy computes itself, and both are the
   only legs whose exits are legible. That is a hint about lever FORM for E3, not
   a result: neither has been shown to exit *well*, only *legibly*.

## 6. Filed

- `BL-20260820-EXIT-REASON-UNCLASSIFIED-IS-THE-MODAL-VALUE` — 63.7% of live
  closes unclassified, with no consumer stating the share.
- `BL-20260820-STRATEGIES-EXIT-REASON-ROLLUP-BUCKETS-LEVERS-AS-OTHER` — the
  published rollup cannot distinguish a lever fire from an orphan artifact.
- Tool: `scripts/research/exit_census.py` (39 self-tests) computes the same
  census over harness corpora, plus MFE capture rate and the MAE-to-stop ratio
  from § 1.5.1 of the process doc. The harness half of E0 runs next.
