# AUD-1 — real-money protection: venue vs journal (E75, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane AUD-1 of the E75 system audit, dispatched by AUD-LEAD (session_01XDNUbSGVQoJCGqtWEW1Fzj). Findings: [`AUD-1.findings.jsonl`](AUD-1.findings.jsonl). Window: 2026-09-22T10:18Z to 2026-09-29T10:18Z. Live reads: the vm-diag relay (#14056–#14058, #14073–#14075) and, from 10:18Z, direct diag via the Caddy host. The deployed trader SHA was `316efffee` (`/api/diag/version`, `restart_pending: false`).

## 1. The question and the answer

**Question.** For every real-money position open at any point in the last 7 days, did the venue's resting orders match the protection the journal believes exists (SL and TP, sized to the current quantity after partial fills), across the position's whole life?

**Answer.** The real-money population is **11 positions**: `bybit_2` 8 and `alpaca_live` 3. `ib_live` is `dry_run`, and `breakout_1` is a manual bridge with no venue read.

**`bybit_2`: yes for the stop, not fully established for the target.** Bybit's own order history shows 8/8 stop legs:
- created in the entry second, reduce-only;
- sized to the full entry quantity;
- triggered within one tick of the journal stop;
- never replaced, and filled at the close.

TP legs are confirmed for only 3/8. The other 5 no longer appear in venue history, most likely because of venue retention (not verified). Two more gaps:
- 5 of the 8 stop fills are journaled as `reconciler_filled` / `netting_attributed`, not `sl`.
- `bybit_2` is in **hedge mode** on all four symbols. That answers CA §9's open question about FIX-CA-06: the fix is deployed but not yet exercised, because no dual-book interval occurred in the window.

**`alpaca_live`: cannot be answered from the venue.** No diag surface reads Alpaca order history, and the journal stores no Alpaca leg ids. The app's own record says every Alpaca entry since 09-15 needed a `naked_rearm` 6–7 minutes after the fill (17/20, including 3/3 live). This happened even though entries are sent as brackets. So either each position was venue-naked for about 6 minutes, or the sweep misreads the bracket's child legs; which one is unknown. IAUM and SLV were flat at the venue by 13:31Z on 09-28, below their stops, and were journaled as reconciler closes. The paper mirrors took a different, app-side exit 5 minutes later.

No partial fills occurred, so the size-after-partial-fill case had n=0.

## 2. Findings

| id | sev | claim (short) | ca_ref |
|---|---|---|---|
| SA-AUD-1-alpaca-rearm-after-every-entry | high | 17/20 Alpaca entries (3/3 live) naked-rearmed 6–7 min after a bracket entry | CA-A01-053 |
| SA-AUD-1-alpaca-order-history-unreadable | medium | no Alpaca order-history surface and no leg ids, so alpaca_live protection-over-life is unobservable | — |
| SA-AUD-1-alpaca-live-gap-exit-not-sl | medium | 6097 IAUM / 5875 SLV flat at the 09-28 open, below their stops, journaled `exchange_flat_reconciled` | CA-A01-035 |
| SA-AUD-1-alpaca-mirror-exit-diverges | medium | SLV: live exited at the venue at the open; both mirrors closed app-side 5 min later at other prices | — |
| SA-AUD-1-bybit2-sl-fill-journaled-as-reconciler | medium | 5/8 bybit_2 venue SL fills journaled as reconciler/netting closes | CA-A01-035 |
| SA-AUD-1-bybit2-hedge-mode-confirmed | medium | bybit_2 is hedge-armed on ADA/XRP/ETH/BTC; FIX-CA-06 deployed, not observed | CA-A01-073 |
| SA-AUD-1-bybit2-tp-leg-history-gap | low | 5/8 TP legs no longer in venue history | — |
| SA-AUD-1-raw-positions-basecoin-queries-fail | low | `bybit_raw_positions` base_coin legs always fail (ErrCode 10001) | — |
| SA-AUD-1-bybit1-phantom-journal-row | low | (paper, seen in passing) bybit_1 ETHUSDT journal 0.88 vs venue 0.44, logged every sweep | CA-A01-020 |
| SA-AUD-1-bybit2-sl-legs-match-venue | info | NON-ISSUE: 8/8 SL legs full-size, on level, and resting entry→close | — |
| SA-AUD-1-realmoney-flat-no-stranded-legs | info | NON-ISSUE: bybit_2 and alpaca_live flat with 0 resting orders at 09:53Z | CA-A01-013 |
| SA-AUD-1-ib-fixca01-deployed-not-exercised | info | FIX-CA-01/01c deployed; stray group gone; 0 post-fix watchdog flat-closes to observe | CA-A01-001 |
| SA-AUD-1-no-partial-fill-population | info | NON-ISSUE (untested): 0 partial entry fills in n=8 | — |

## 3. Coverage

**Behavioural (primary).** The question needs 5 capabilities; 3 were exercised end to end against real data:

| Capability | Result |
|---|---|
| (a) bybit_2 SL placement → rest → fill | **exercised**, n=8, venue history joined to the journal |
| (b) bybit_2 TP rest over life | **partly**, 3/8 |
| (c) alpaca_live protection over life | **not exercised**, no venue history surface |
| (d) resting-order state vs journal at an instant, all live venues | **exercised**, 09:53Z |
| (e) protection resize after a partial fill | **not exercised**, n=0 occurrences |

Fix status (merged, deployed and observed are separate facts):

| Fix | Merged | Deployed | Observed |
|---|---|---|---|
| FIX-CA-01 / 01c | yes | yes | no (never triggered) |
| FIX-CA-06 | yes | yes | no (never triggered) |
| FIX-CA-01b | n/a (an order cancel, not code) | n/a | stray group `oca-protect-t5836` absent from ib_open_orders at 09:53Z |

**Reading (secondary).**
- **Read in part:** `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §3–5; `docs/audits/code-audit-2026-09-27.md` §4 table, FIX-CA-01/06 briefs and §9; `CA-A01.findings.jsonl` (critical/high rows); `src/web/api/routers/diag.py` (route list and the docstrings of the order/position routes); `src/units/accounts/alpaca_client.py:555-615` and `:1786-1840`; the `BYBIT_TPSL_MODE` row of `docs/reference/env-vars.md`.
- **Not read:** `src/runtime/order_monitor.py` beyond grep hits; `_check_broker_naked_equity_positions` itself (the high finding's root cause is therefore open); `src/units/accounts/execute.py`; `clients.py`; all tests; other CA lane files.

## 4. Could not look

- **Alpaca order history**, for all alpaca_live and mirror positions: no diag route exists.
- **Bybit TP legs** 334a7499, 7815e4d7, f8ec4d17, 18b13166 and 9311ea6e: absent from venue history.
- **`bybit_coverage_soak`**: 74 MB, and log_file only tails it, so the 7-day bybit_2 rows were not read.
- **The ict-trader-live journal for 2026-09-23 13:30–13:40** (IAUM entry): returned "-- No entries --", i.e. outside retention.
- **`/api/bot/db/table/trades` via the relay**: HTTP 401 (#14073, #14074).
- **Trainer-VM journal query** (#14100): failed on this lane's own heredoc indentation error (`IndentationError`). Not retried, because the direct diag journal read (3000 rows) replaced it.
- **`/version` in the relay batch** (#14056): cut off. It was read directly afterwards.
- **`breakout_1`**: manual bridge, no venue order read.

## 5. Spend note

About 25 tool round trips, 9 relay issues and about 15 direct diag reads. The session cannot read its own meter. The lead reported about $2.75 at 10:18Z, and this lane ran well under its $45 ceiling. Relay issues opened: #14056, #14057, #14058, #14073, #14074, #14075, #14100.
