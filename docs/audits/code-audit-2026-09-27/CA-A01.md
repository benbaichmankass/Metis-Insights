# CA-A01 — code audit of `src/runtime/order_monitor.py`

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> lane CA-A01 of the 2026-09-27 code audit · session `session_0155iFHoP6BJuj62kfWRL9r1` · reports to manager `session_01KAQRJxRbRwpYTkPgBjNVyQ` · audited HEAD `043825797` (origin/main at 2026-09-27T13:00Z)

**The question:** which parts of `order_monitor.py` do not work as their docstrings, callers, tests and the canonical docs say they should?

**Answer:** **60 open findings**: 1 critical, 14 high, 28 medium, 17 low. On top of those, **27 verified non-issues** and **2 duplicates**. Machine-readable version: [`CA-A01.findings.jsonl`](CA-A01.findings.jsonl), one object per line.

## The critical finding

**CA-A01-001, MEASURED on the venue at 2026-09-27T13:02Z.**
- ib_paper has IB OCA group `oca-protect-t5836` resting: SELL STP 15 MESZ6 @7602.5 and SELL LMT 15 @8390.5. There is **no MES position**.
- Trade 5836 owned that group. `_watchdog_stuck_strategies` closed it on 2026-09-19, and that path never cancels resting protection. The sweep `_cancel_resting_protection_after_flat` is wired into only 2 call sites, both in `_reconcile_orphan_exchange_positions`.
- If either leg triggers, it opens an unintended 15-lot short.
- The same defect class already ran on ib_paper MES on 2026-09-16/17. The journal shows adopted-orphan flips: 5830 short 15, then 5835 and 5836 long 15, then 5852 short 45, 5853 short 30 and 5856 short 15.
- Filed as `PI-20260927-KFWRL9R1-0001` in its own PR, #13157.

## What the live data says overall (MEASURED, venue vs journal, 13:02Z)

- **Positions agree.** Journal open rows equal venue positions on all 26 (account, symbol, side) keys. The 2 journal-only keys are pairs legs that opened after the snapshot.
- **Every position has a stop on the venue.** Coverage equals size for 24 of 24 positions: Bybit 8, Alpaca 14, IB 2. So "am I protected" is not failing on the live population *today*. The live failure is the reverse: **protection with no position** (CA-A01-001).
- **4 of 28 open trades sit under a CLOSED order package** (CA-A01-033 / C-01). One is **real-money bybit_2 XRPUSDT 6172**. Its package has read `reconciler_filled` since 2026-09-26T09:14Z, when the bybit_1 sibling leg closed, so the leg is off the open-package exit population. Its venue SL still rests.
- **6 closed ib_paper rows store `pnl = 0.0` with `pnl_source = unmeasured`** (CA-A01-002). That zero should be NULL.
- Four latent findings were checked against live state and are **not present now**: B-04 (link overwrite), B-05 (buy/sell spelling; all 6,224 rows are long/short), D-01 (IB target-only) and D-06 (Alpaca netted re-arm on real money). The precondition for **E-01**, the hedge-mode skip, **is live**: bybit_1 BTCUSDT holds both books. Both books are covered today.

## Open findings, most severe first

| id | severity | status | claim | location | pipeline |
|---|---|---|---|---|---|
| CA-A01-001 | critical | MEASURED | The stuck-strategy watchdog's flat-close branch finalises a trade and package without cancelling the trade's resting broker protection, and on ib_paper the keyed OCA group oca-protect-t5836 (SELL STP 15 MESZ6 @7602.5 + SELL LMT… | `src/runtime/order_monitor.py:5750-5880 (no cancel); sweep wired only at 3394, 3630 (_ca…` | PI-20260927-KFWRL9R1-0001 |
| CA-A01-005 | high | MEASURED | The close path's 'position already closed on the exchange' recogniser never matches the real Bybit rejection, so a monitor close of a position whose broker SL/TP already fired is treated as a genuine close FAILURE (DB left open… | `src/runtime/order_monitor.py:1092-1114` | PI-20260927-KFWRL9R1-0002 |
| CA-A01-006 | high | MEASURED | A multi-leg package's close verdict is effectuated only against legs[0] (the linked leg) each tick, so if that leg's close fails, is wedge-suppressed, or is in IB cooldown, every sibling leg (e.g. the bybit_portfolio / alpaca_p… | `src/runtime/order_monitor.py:954-1178` | PI-20260927-KFWRL9R1-0003 |
| CA-A01-007 | high | MEASURED | When the exchange close succeeds but the trade-row write raises, the failure is swallowed, closed_count is incremented, and the next tick sends a SECOND reduce-only market close for the same qty on the same account/symbol. | `src/runtime/order_monitor.py:1223-1291,1313-1336` | PI-20260927-KFWRL9R1-0004 |
| CA-A01-018 | high | MEASURED | _reconcile_open_trades marks a DB-open trade 'orphaned' on a single account_order_status 'not_found' read without consulting the venue position view, so a trade whose position is still open on Bybit is removed from the open jou… | `src/runtime/order_monitor.py:L4545-L4581 (reason text at L6765-L6769 in _mark_orphaned)` | PI-20260927-KFWRL9R1-0005 |
| CA-A01-019 | high | MEASURED | _recover_orphan_attribution returns refused=False/package=None when the order-package read RAISES, which _reconcile_orphan_exchange_positions treats as 'no candidate exists' and flattens a live orphan_adopt position with a redu… | `src/runtime/order_monitor.py:L3947-L3950 (swallow) -> L3324-L3333 (branch) -> L4118-L41…` | PI-20260927-KFWRL9R1-0006 |
| CA-A01-020 | high | MEASURED | The reverse reconciler's orphan test is set MEMBERSHIP on (symbol, side), not quantity, so a venue position larger than the sum of the journal's open rows on that (symbol, side) is never detected — the untracked surplus is repo… | `src/runtime/order_monitor.py:L3260-L3267, L3671-L3672` | PI-20260927-KFWRL9R1-0007 |
| CA-A01-021 | high | MEASURED | _adopt_orphan_position and _reattach_adopted_orphans unconditionally overwrite order_packages.linked_trade_id with the orphan row, so a PAPER-account orphan (e.g. bybit_portfolio, which mirrors bybit_2's trades exactly) steals … | `src/runtime/order_monitor.py:L4087-L4091, L4351-L4356 (matcher L3948 is symbol-only; or…` | PI-20260927-KFWRL9R1-0008 |
| CA-A01-033 | high | MEASURED | _sweep_stuck_linked_packages and _cascade_close_linked_package (called from _close_trade_from_order_status, _cascade_close_netted_siblings and the watchdog; plus the identical inline cascade in _mark_orphaned and the watchdog f… | `src/runtime/order_monitor.py:4992-5068, 6063-6103, 6803-6816, 5755-5765` | PI-20260927-KFWRL9R1-0009 |
| CA-A01-034 | high | INFERRED | The watchdog's position-flat/broker-unmatched branch stamps closed_at = the watchdog's own observation time, so the downstream local-PnL sweep prices the row at the candle for that time and labels it candle_at_close (ESTIMATED)… | `src/runtime/order_monitor.py:5852-5876` | PI-20260927-KFWRL9R1-0010 |
| CA-A01-048 | high | MEASURED | _check_broker_naked_ib_positions decides 'fully covered' on IBClient.protection_coverage's COMBINED covered_qty (stop OR target), so an IB position with a full take-profit resting and NO stop is graded covered, never re-armed, … | `src/runtime/order_monitor.py:L8712-L8920` | PI-20260927-KFWRL9R1-0011 |
| CA-A01-049 | high | MEASURED | On a partially covered netted IB position the sweep re-arms rows in SQL row order and credits each re-armed row's qty, so a row whose own bracket is intact is re-armed every sweep while the genuinely naked sibling is skipped fo… | `src/runtime/order_monitor.py:L8918-L8964` | PI-20260927-KFWRL9R1-0012 |
| CA-A01-053 | high | MEASURED | On a netted Alpaca symbol with >=2 open journal rows, the equity naked sweep re-arms only the FIRST row's qty and then treats the symbol as protected, because the re-arm gate is AlpacaClient.protection_state (any stop leg of an… | `src/runtime/order_monitor.py:L8179-L8314` | PI-20260927-KFWRL9R1-0013 |
| CA-A01-057 | high | INFERRED | On every Bybit account not named in BYBIT_GRADED_COVERAGE_ACCOUNTS (shipped empty, so including mainnet bybit_2), the re-arm decision still reads the side-blind covered_qty, so in hedge mode an other-book SL leg can push covera… | `src/runtime/order_monitor.py:L9224-L9304 (producer); caller L10330-L10470` | PI-20260927-KFWRL9R1-0014 |
| CA-A01-073 | high | MEASURED | _check_broker_naked_bybit_positions silently skips every symbol whose protection read returns None (hedge symbol with BOTH books live, no_rows, size_unreadable, or a broker error): no re-arm for either book, no summary counter,… | `src/runtime/order_monitor.py:10197-10199, 10145-10150 (reporting 12146-12149); selectio…` | PI-20260927-KFWRL9R1-0015 |
| CA-A01-002 | medium | MEASURED | Six closed ib_paper trades carry pnl = 0.0 while their notes stamp pnl_source 'unmeasured' and their exit_price differs from entry_price, so an unmeasured PnL is stored as a measured-looking zero. | `src/runtime/order_monitor.py:411-431 (_prov_unmeasured_marker), 1278; IB exit via src/r…` |  |
| CA-A01-008 | medium | MEASURED | The partial-close path handles only the linked leg and, when cumulative partials reach 100%, _full_close_trade_and_package flips the PARENT package to closed while sibling legs are still open - the exact BL-20260818 stranding t… | `src/runtime/order_monitor.py:495-524,640-656,745-749` |  |
| CA-A01-009 | medium | MEASURED | When any leg of a multi-leg modify does not apply, order_packages.sl is left stale by design, and the next trail verdict is ratcheted against that stale level, so a stop already tightened on the successful legs can be LOOSENED … | `src/runtime/order_monitor.py:1444-1551` |  |
| CA-A01-010 | medium | INFERRED | The modify fan-out's stated safety assumption ('an already-applied leg is re-amended next tick. That is idempotent at the venue') is likely false for Bybit: re-sending an unchanged stopLoss/triggerPrice is rejected by Bybit, so… | `src/runtime/order_monitor.py:1513-1522; src/units/accounts/execute.py:2767-2781,2986-3004` |  |
| CA-A01-011 | medium | INFERRED | _full_close_trade_and_package closes the package first and then swallows a failed trade-row write while still incrementing closed_count, leaving an open trade under a closed package that the monitor will never select again. | `src/runtime/order_monitor.py:745-781` |  |
| CA-A01-013 | medium | INFERRED | _cancel_resting_protection_after_flat is a no-op for Bybit (only IBClient implements cancel_resting_protection) so reconciler flat-closes leave Bybit Partial-tpsl legs resting; the function's own docstring documents this and de… | `src/runtime/order_monitor.py:1825-1900` |  |
| CA-A01-023 | medium | INFERRED | _reattach_adopted_orphans runs at the top of the pass before any venue read and re-arms a broker protective bracket (_rearm_broker_protection_after_recovery -> _attempt_naked_autoprotect) without checking that the orphan's posi… | `src/runtime/order_monitor.py:L3193-L3197, L4041-L4115 (re-arm path L7825-L8044 has no p…` |  |
| CA-A01-024 | medium | INFERRED | _recover_orphan_attribution has no account, recency or status bound on candidates, so an orphan can be attributed to a closed package from days/weeks earlier whose SL/TP are stale, and that package is reopened and its levels wr… | `src/runtime/order_monitor.py:L3944-L3989 (+ database.py L1712-L1728 'any strategy/status')` |  |
| CA-A01-025 | medium | INFERRED | The orphan_adopt close-on-disappear pass closes on two batch-LIST absences only; it does not apply the RISK-1 per-symbol broker presence check (account_position_present) or the mass-vanish reset threshold that the strategy-row … | `src/runtime/order_monitor.py:L3343-L3406 vs L3534-L3549 and L3567-L3594` |  |
| CA-A01-035 | medium | MEASURED | When a recovered broker close is mid-bracket (exit_reason stays reconciler_filled), _close_trade_from_order_status asks for the close exec type in the 10 minutes before NOW, not before the recovered close time, so a close older… | `src/runtime/order_monitor.py:6699-6715` |  |
| CA-A01-036 | medium | MEASURED | _recover_close_from_broker_pnl (watchdog recovery) books a matched closed-pnl record's pnl onto an intent_reduce leg and stamps exit_price_source with the broker source (MEASURED), whereas the reconciler close path deliberately… | `src/runtime/order_monitor.py:5354-5361, 5425-5449` |  |
| CA-A01-037 | medium | MEASURED | _classify_broker_exit fills a missing sl or tp from _resolve_protective_levels, which returns the newest package on (symbol, direction) from ANY strategy/account/time, so a trade whose own package has no TP can be labelled 'tp'… | `src/runtime/order_monitor.py:6252-6257 (with 7797-7822)` |  |
| CA-A01-038 | medium | MEASURED | The netted flatten attributes more than 100% of one broker closed-pnl record: the primary keeps the full record pnl whenever record qty <= 1.05x its own qty, and _cascade_close_netted_siblings still adds pnl*(sib_qty/rec_qty) t… | `src/runtime/order_monitor.py:6620-6630, 6336-6413` |  |
| CA-A01-050 | medium | INFERRED | Ungrouped IB protective legs (the SL and TP children of an entry bracket placed by IBClient.place with parentId and no ocaGroup) are each summed into covered_qty, so one trade's intact entry bracket covers twice its qty and a n… | `src/runtime/order_monitor.py:L8712-L8920 (consumer); src/units/accounts/ib_client.py:L3…` |  |
| CA-A01-051 | medium | MEASURED | IB coverage never checks a leg's action against the position side, so a stop resting on the WRONG side (e.g. a stale BUY STP against a long, which would ADD to the long when triggered) is counted as full stop coverage. | `src/runtime/order_monitor.py:L8712-L8920 (consumer); src/units/accounts/ib_client.py:L3…` |  |
| CA-A01-052 | medium | MEASURED | The IB over-cover detector only fires above 1.5x size, so stop excess spread across disjoint OCA groups below 150% (e.g. current trail group 10 + stale group 4 on a 10 long) is completely silent although it carries the same nak… | `src/runtime/order_monitor.py:L8836-L8876` |  |
| CA-A01-056 | medium | MEASURED | _bybit_position_protection treats any position stopLoss string other than '', '0', '0.0', '0.00' as a Full-mode stop covering the whole position, so '0.000', '0.0000', '0E-8' or an unparseable value grades a naked Bybit positio… | `src/runtime/order_monitor.py:L9189-L9221` |  |
| CA-A01-058 | medium | MEASURED | When the Bybit read took the Full-mode branch, sl_leg_ids is an empty set because the legs were never read, yet _netting_rows_to_attribute labels every tracked row 'leg_gone' (its 'evidence' basis) and attributes the close to i… | `src/runtime/order_monitor.py:L9453-L9518 (with producer L9206-L9209 and caller L9711-L9…` |  |
| CA-A01-059 | medium | MEASURED | _is_pairs_sleeve_row always returns False inside _check_broker_naked_bybit_positions because that sweep's SELECT omits setup_type and strategy_name, so the pairs-sleeve divergence branch (journal_qty_divergent_pairs) is dead an… | `src/runtime/order_monitor.py:L9521-L9536 (caller SELECT L10095-L10098)` |  |
| CA-A01-060 | medium | MEASURED | _reassert_applied_state verifies a re-assert by symbol-aggregate stop_qty>0 and target_qty>0, so a sibling trade's legs or the very diverged stop that triggered the re-assert make it report 'both_legs_resting' (and stamp the tr… | `src/runtime/order_monitor.py:L8504-L8538` |  |
| CA-A01-061 | medium | INFERRED | The IB naked re-arm passes trades.direction to IBClient.place_protective unnormalised, and place_protective maps any value other than 'long'/'short' (e.g. 'buy', '' or NULL) to 'short', so such a row on a long position would ge… | `src/runtime/order_monitor.py:L7936-L7938,L8020-L8041 (consumer); src/units/accounts/ib_…` |  |
| CA-A01-074 | medium | MEASURED | _bybit_top_up_partial_sl calls modify_open_order without `side`, so on a hedge-mode symbol apply_position_idx resolves `unresolved` and sends no positionIdx; the qty-scoped top-up is refused and the sweep falls back to the whol… | `src/runtime/order_monitor.py:9378-9381 (caller src/runtime/order_monitor.py:10528-10530…` |  |
| CA-A01-075 | medium | MEASURED | The Bybit naked sweep never checks that the journal row's direction matches the exchange position side before using that row's SL/TP to repair it, so a phantom opposite-direction row (the very row the same pass flags as journal… | `src/runtime/order_monitor.py:10168-10201, 10497-10530, 10547-10548` |  |
| CA-A01-076 | medium | MEASURED | _sweep_pending_pnl_from_bybit computes `_is_reduce` but never forwards `reduce_leg` to account_closed_pnl_for_trade, so intent_reduce rows are looked up with the inverted close side and the primary leg's intended entry — the ex… | `src/runtime/order_monitor.py:10848-10855 (vs src/runtime/order_monitor.py:10911-10915 a…` |  |
| CA-A01-077 | medium | MEASURED | _sweep_pending_pnl_from_bybit writes pnl_percent = closed_pnl/(entry*qty) with no contract multiplier, and IBKR is a broker-reader exchange, so every IB futures row it fills gets pnl_percent inflated by the multiplier (5x on MES). | `src/runtime/order_monitor.py:10951-10961` |  |
| CA-A01-078 | medium | MEASURED | _netting_apply_close's partial branch reduces position_size by the attributed qty but books neither an estimated PnL nor an UNMEASURED declaration for that qty, and returns 'closed' so the reconciler counts a reduction as a close. | `src/runtime/order_monitor.py:9900-9909; counted at src/runtime/order_monitor.py:9763-9764` |  |
| CA-A01-079 | medium | MEASURED | _reconcile_options_expiry_and_assignment concludes a row from ANY lifecycle activity on the same underlying root within the 4-day lookback, with no check against the row's created_at or its own OCC legs, so a new structure not … | `src/runtime/order_monitor.py:11557-11563, 11606-11645; options_lifecycle.py:152-175` |  |
| CA-A01-080 | medium | MEASURED | In _sweep_local_pnl_for_unpriced the exit-label basis is classified from the local `exit_source` variable, which can only be 'recorded_exit_price' or 'candle_at_close' (both ESTIMATED), so the documented FABRICATED-price refusa… | `src/runtime/order_monitor.py:11253-11290, 11395-11418` |  |
| CA-A01-012 | low | MEASURED | Partial-close accounting records the verdict pct (not the filled fraction) in partial_closes.qty and tests cumulative closure with float `>= 1.0`, so short fills, whole-share quantization, or pct sequences like 0.1x10 / 0.7+0.2… | `src/runtime/order_monitor.py:527-533,627-640,683-690` |  |
| CA-A01-014 | low | INFERRED | The management-capability gate runs before the dry_run short-circuit, so dry-run accounts on integrations lacking an op (oanda_practice modify; alpaca/ib partial_close) record the verdict as a failure every tick instead of book… | `src/runtime/order_monitor.py:1931-1945,2073-2084` |  |
| CA-A01-022 | low | MEASURED | _reconcile_open_trades compares the journal direction un-canonicalised (`str(direction).lower()`) against _exchange_position_set's canonical long/short keys, so a DB-open row whose direction is 'buy'/'sell' reads as flat while … | `src/runtime/order_monitor.py:L4597-L4600` |  |
| CA-A01-026 | low | INFERRED | _reconcile_open_trades counts every non-Bybit row with a real order id as 'skipped_no_creds' (a read failure) on every tick, because account_order_status returns None for any exchange other than bybit by design. | `src/runtime/order_monitor.py:L4536-L4540 (clients.py L1153-L1156)` |  |
| CA-A01-027 | low | MEASURED | _classify_orphan_close has no production caller (only tests/test_monitor_reconciler.py) and its docstring describes bybit_2 as spot-margin, which accounts.yaml no longer declares (bybit_2 market_type: linear). | `src/runtime/order_monitor.py:L2923-L2975` |  |
| CA-A01-028 | low | MEASURED | The _reconcile_orphan_exchange_positions docstring says policy 'close' submits a market close via safe_place_order, but the code at L3776-3796 is a stub that falls back to detect_only. | `src/runtime/order_monitor.py:L3103-L3106 vs L3776-L3796` |  |
| CA-A01-039 | low | MEASURED | _close_trade_from_order_status raises TypeError before writing the close if order_status has no avg_price, because _safe_float returns None and the code compares None > 0 outside any try. | `src/runtime/order_monitor.py:6591-6594, 6683-6686` |  |
| CA-A01-040 | low | MEASURED | _is_real_order_id returns True for journal-synthesised ids that are not exchange orders: 'prop-manual-<uuid>' (breakout manual bridge), 'rejected_too_small-<hex>' and 'orphaned-<hex>' (the f'{status}-{uuid}' fallback in _log_tr… | `src/runtime/order_monitor.py:6131-6174` |  |
| CA-A01-041 | low | INFERRED | If a package already received the watchdog's 'position alive' informational alert, the later position-flat force-clear and trade finalisation send no alert, because both branches share the meta.stuck_alert_emitted_at stamp. | `src/runtime/order_monitor.py:5590, 5671-5675, 5895-5913` |  |
| CA-A01-042 | low | INFERRED | A close finalised from a PartiallyFilledCanceled entry keeps the journal's intended position_size, so pnl_percent (and any later local-compute pnl on the fallback path) uses the unfilled quantity. | `src/runtime/order_monitor.py:6620-6641, 6673-6686` |  |
| CA-A01-043 | low | INFERRED | _sweep_unlinked_packages step (0) only reconciles from trades in ('open','closed'). A package whose executed trade was later marked 'orphaned' falls through to step (2) and is stamped 'orphaned: package was never executed'. Ste… | `src/runtime/order_monitor.py:4820-4853, 4936-4949` |  |
| CA-A01-062 | low | INFERRED | _resolve_protective_levels takes the newest order package matching only symbol/root + direction, from any strategy or trade, with no check that SL/TP bracket the trade's entry, and callers fill a missing TP from it — so a trade… | `src/runtime/order_monitor.py:L7797-L7822 (callers L8295-L8300, L8938-L8943)` |  |
| CA-A01-063 | low | MEASURED | The Alpaca equity sweep counts and pages a FULLY naked position (stop_qty 0) as 'partially_naked' / partial_stop_coverage, mislabelling the worst state as the lesser one. | `src/runtime/order_monitor.py:L8243-L8258` |  |
| CA-A01-081 | low | INFERRED | Three reconciliation phases report a failed read as a clean empty pass: _sweep_pending_pnl_from_bybit and _sweep_local_pnl_for_unpriced return errors=0 when the scan query (or the local sweep's imports) fail, and _reconcile_opt… | `src/runtime/order_monitor.py:10795-10799, 11142-11144, 11168-11170, 11541-11543; emit p…` |  |
| CA-A01-082 | low | MEASURED | run_exit_evaluation_tick has no per-package/per-strategy exception isolation, so one exception out of _apply_update aborts evaluation of every later strategy in the pass, and in the EXIT_LOOP_DECOUPLE_DISABLED rollback path run… | `src/runtime/order_monitor.py:11793-11892, 12204-12211` |  |
| CA-A01-083 | low | MEASURED | Two comments in this chunk describe behaviour the code does not have: run_reconciliation_tick says the netting reconciler 'reuses the same freshly-read exchange truth' as the naked sweep, and _check_naked_positions says auto-pr… | `src/runtime/order_monitor.py:12159-12160, 10630-10635` |  |
| CA-A01-084 | low | INFERRED | In annotate mode (the shipped default) a confirmed netting divergence is re-annotated on every tick forever, appending the same row list to netting_attribution_soak.jsonl each pass, and summary key `broker_naked_qty_uncovered` … | `src/runtime/order_monitor.py:9698-9748 (no dedupe after confirm); src/runtime/order_mon…` |  |

## Verified non-issues and duplicates

| id | disposition | claim |
|---|---|---|
| CA-A01-003 | verified-non-issue | Journal open rows and venue positions agree in quantity on every (account, symbol, side) key, and every live position has venue-side stop coverage equal to its size — the protection lifecycle is not failing on the live populati… |
| CA-A01-004 | verified-non-issue | The bybit_1 pairs-sleeve legs carry venue stops at exactly 2x / 0.5x entry (BTCUSDT short 0.003 SL 169402.8 vs entry 84701.4; BNBUSDT long 0.27 SL 390.5 vs entry 781.0), so the price-blind protection predicates grade them cover… |
| CA-A01-015 | verified-non-issue | exit_population correctly unions roster and open-package names and distinguishes 'could not read' from 'nothing open'. |
| CA-A01-016 | verified-non-issue | The modify path does not write order_packages.sl/tp when the venue rejects a leg, and trades.stop_loss is synced only for legs that actually applied. |
| CA-A01-017 | verified-non-issue | _package_open_legs never treats a failed read as 'no legs'; both close and modify refuse to act on read_failed. |
| CA-A01-029 | verified-non-issue | _parse_created_at and _exchange_position_set handle the timestamp/side shapes they claim: SQLite space format, ISO with offset, 'Z', and Buy/LONG/Sell side variants. |
| CA-A01-030 | verified-non-issue | A Bybit adopted row that was reattached to a strategy (no order id in notes, strategy_name != orphan_adopt) is closed by neither function in this chunk when its venue position vanishes, but it IS covered elsewhere. |
| CA-A01-031 | verified-non-issue | _load_account_cfgs_for_reconcile's stripped cfg (no symbols/options/account_class keys) does not break the downstream consumers that need those keys. |
| CA-A01-032 | verified-non-issue | The active-close marker, grace/confirm/readopt/min-fill-age env readers and the close-fail paging backoff behave as their docstrings declare. |
| CA-A01-044 | verified-non-issue | The durable alert cooldown (_cooldown_admits -> alert_cooldown.cooldown_admits) cannot silence an alert forever. |
| CA-A01-045 | verified-non-issue | Age and timezone handling in _pkg_age_minutes and in the watchdog/unlinked-sweep SQL is correct. |
| CA-A01-046 | verified-non-issue | _prorate_netted_broker_pnl's arithmetic and the provenance downgrade on proration are correct. |
| CA-A01-047 | verified-non-issue | The watchdog's (symbol, direction) liveness match is not broken by IB contract-month symbols. |
| CA-A01-054 | duplicate | When a hedge-mode Bybit symbol has BOTH books live, _bybit_position_protection returns None ('ambiguous_multi_book'), and the naked sweep then skips every row on that symbol without a summary counter or page, so both books go u… |
| CA-A01-055 | duplicate | _bybit_top_up_partial_sl calls execute.modify_open_order without `side`, so on a hedge-mode symbol the Partial set_trading_stop goes out with no positionIdx, is refused by the venue, and the sweep falls back to a Full-mode re-a… |
| CA-A01-064 | verified-non-issue | _classify_group_owner never reads an unreadable clientId as this session: None on either side returns 'unknown', equal ints 'this_session', else 'other_session'. |
| CA-A01-065 | verified-non-issue | _bybit_sl_leg_qty / _bybit_sl_leg_trigger cannot manufacture coverage or a price: unparseable, NaN, zero and negative values return None, and the caller counts None qty as unknown_qty_sl_legs, which makes the sweep skip rather … |
| CA-A01-066 | verified-non-issue | Bybit TP legs are not counted as stop coverage: covered_qty sums only stopOrderType in {stoploss, partialstoploss}; TP types feed only protective_leg_count (the cap figure). |
| CA-A01-067 | verified-non-issue | A Bybit read failure is not graded as 'naked' or 'flat': get_positions/get_open_orders exceptions return None, an empty position list is 'no_rows' (refused), and pybit raises on non-zero retCode so an error envelope cannot beco… |
| CA-A01-068 | verified-non-issue | _bybit_mark_fully_covered is an in-tick cache marker only and cannot suppress re-protection on later ticks: protection_cache is rebuilt per sweep call and the marker is written only after a confirmed top-up or re-arm. |
| CA-A01-069 | verified-non-issue | The re-assert budget/cooldown (_REASSERT_STATE) cannot starve naked re-protection: it gates only the price-divergence re-assert path, is per-process (reset on restart), and at the default PROTECTION_REASSERT_MODE=annotate place… |
| CA-A01-070 | verified-non-issue | _base_futures_symbol only strips a month-code suffix (letter in FGHJKMNQUVXZ + 1-2 digits) from a >=2-letter root, so equity/ETF and crypto symbols pass through unchanged and IB coverage reads on the contract.symbol root. |
| CA-A01-071 | verified-non-issue | The Alpaca quantity grade is side-correct and never collapses 'could not look': it counts only reducing-side legs, a failed positions read -> coverage_read_failed, unparseable legs -> coverage_ungradeable, and a symbol absent f… |
| CA-A01-072 | verified-non-issue | IB coverage read failures are skipped and counted, not graded naked: protection_coverage None -> read_failed += 1 and continue; unparseable leg qty -> ungradeable and continue; STP LMT is classified as a stop before the LMT test. |
| CA-A01-085 | verified-non-issue | _sweep_pending_pnl_from_bybit books closed_pnl without _prorate_netted_broker_pnl (a fourth broker-close persist site the helper's docstring does not list), but whole-record booking of a bigger netted record is blocked upstream. |
| CA-A01-086 | verified-non-issue | Netting attribution excludes pairs-sleeve rows before grouping and stamps provenance correctly on full closes (anchored -> candle_at_close = ESTIMATED; no_anchor -> pnl_source 'unmeasured', pnl NULL; deferred -> nothing written). |
| CA-A01-087 | verified-non-issue | The Bybit naked sweep's repair is idempotent within and across ticks: in-tick via _bybit_mark_fully_covered on both coverage bases, cross-tick because a Full-mode re-arm makes the next read return source full_position_stop (cov… |
| CA-A01-088 | verified-non-issue | Omitting closed_at_ms in the pending-pnl lookup (window open->now, growing over 7 days of retries) does not let later trades' fills bleed into the match. |
| CA-A01-089 | verified-non-issue | The options reconciler does not treat an unreadable broker as expiry: it requires both the activities read (retCode 0) and the positions read to succeed before concluding any row. |

## Reading ledger

| range (`src/runtime/order_monitor.py`) | read by | read in full? | also read, for contracts and callers |
|---|---|---|---|
| 1–2387 | chunk reader A | yes (2,387 lines) | `monitor_verdict.interpret_verdict`; `execute.close_open_position` / `modify_open_order` (L2732–3400); `clients.EXCHANGE_MANAGEMENT_CAPS`; turtle_soup / trend_donchian trail; `tests/test_order_monitor_package_legs.py` |
| 2380–4776 | chunk reader B | yes | `clients.account_open_positions` / `account_order_status` / `_bybit_configured_symbols` / `_alpaca_pos_in_scope`; `ib_client.positions`; `database.get_recent_order_packages_for_symbol` / `insert_trade` |
| 4755–7060 | chunk reader C | yes | `alert_cooldown`; `scripts/ops/reattach_stranded_package_legs.py`; SQLite `datetime()` tz behaviour (tested) |
| 7040–9548 | chunk reader D | yes | `ib_client.protection_coverage` / `place_protective` / `_protective_leg_side`; `alpaca_client.protection_state` / `protection_coverage` / `place_protective`; `bybit_position_mode`; `bybit_position_book.select_position_row`; `tests/test_protection_price.py` |
| 9520–12212 | chunk reader E | yes | `bybit_position_book`; `execute.modify_open_order`; `fills_pnl`; `options_lifecycle`; `provenance`; `exit_anchor`; `src/main.py` callers; `docs/reference/env-vars.md` |
| 1825–1903, 1090–1114, 5700–5900 | lane lead | yes (re-read to verify CA-A01-001 / A-01 / C-01) | `execute.py` L2755–2800 (error-string shape); `stray_oca_groups.py` header |

**Coverage:** 12,212 of 12,212 lines read. The overlapping ranges were read twice.

**Not read in full:**
- The 102 test files that import `order_monitor`. Readers grepped them per function and read the relevant tests. They were **run**, not read line by line.
- The client modules (`execute.py`, `clients.py`, `ib_client.py`, `alpaca_client.py`). Only the sections named above were read; they belong to other lanes.

**How findings were made.** The five chunk readers ran as parallel sub-agents of this lane. Each finding marked MEASURED in a chunk was reproduced against the real module, using fake DBs and fake clients (scripts kept in the lane scratchpad, not committed). This lane then re-verified CA-A01-001, A-01 and C-01 by hand, and checked B-04, B-05, D-01, D-06 and E-01 against live data.

## Behavioural coverage (checked against real data, not the app's own fields)

Surfaces used, all read-only: `/api/diag/exchange_positions`, `/api/diag/bybit_open_orders` (×3 accounts), `/api/diag/alpaca_open_orders` (×3), `/api/diag/ib_open_orders`, `/api/diag/journal?table=trades` (all 6,224 rows) and `?table=order_packages` (3,000 newest).

| capability in scope | exercised on live data? | result |
|---|---|---|
| journal open rows ⇔ venue positions (qty, per key) | yes | 26/26 agree |
| Bybit stop coverage (qty, side, reduce-only) | yes | 8/8 |
| Alpaca stop coverage | yes | 14/14 |
| IB stop coverage | yes | 2/2 |
| protection cancelled after a close (no orphan protection) | yes | **fails**: CA-A01-001 |
| package closes only when all legs close | yes | **fails**: 4/28 open trades under closed packages |
| package link prefers the real-money leg | yes | holds now (21/21) |
| direction canonicalisation in the journal | yes | all long/short |
| unmeasured PnL stored as NULL | yes | **fails**: 6 rows at 0.0 |
| hedge-mode dual-book precondition (E-01) | yes | present (bybit_1 BTCUSDT) |
| Alpaca netted multi-row coverage (D-06) | yes | not present on real money |
| IB target-only coverage (D-01) | yes | not present |
| trailing / exit-lever application vs venue SL | no | — |
| close-reason classification accuracy (C-05) | no | — |
| broker-PnL recovery / netted proration accuracy | no | — |
| netting attribution (annotate soak) | no | — |
| options expiry / assignment reconcile | no | — |
| orphan adopt / reattach runtime frequency | partly (the 2026-09-16/17 MES cascade in the journal) | — |
| Bybit 110017 close-rejection frequency (A-01) | archive only (7 samples in `ERROR-FEED-DIGEST.json`) | — |
| stuck-watchdog flat-close frequency | partly (trade 5836) | — |

**Exercised: 12 of 20.** Two more were exercised partly.

## Tests run

`python3 -m pytest -q` over every file under `tests/` that mentions `order_monitor`: 102 files, **2,313 passed, 0 failed**, 97 s, run at HEAD `043825797`.

A green run is evidence only for what the tests assert. Several findings name a test that pins the defective behaviour: D-09 (`tests/test_protection_price.py` asserts that `'banana'` is covered) and E-08 (the only test greps the source text). Several others name a branch no test reaches: A-01 and B-02.

## What I would audit next with more budget

1. **Exit-lever application against the venue.** For every open trade, compare the strategy's current trail verdict with the resting SL price (A-05, A-06).
2. **The live env values that set several severities:** `BYBIT_HEDGE_MODE_SYMBOLS`, `BYBIT_GRADED_COVERAGE_ACCOUNTS`, `NETTING_ATTRIBUTION_MODE`, `PROTECTION_REASSERT_MODE` (D-07, D-10, E-12, D-13).
3. **The 110017 close-rejection rate** in `journalctl` over 7 days (A-01, A-02).
4. **A sweep of every keyed protective group on all venues against its trade's status.** This generalises CA-A01-001 into a standing detector.
5. **How IB reports `ocaGroup` for bracket children** (D-03).
