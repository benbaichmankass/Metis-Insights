# AUD-2 — trading correctness, signal → order → fill → exit → grade (E75 audit, 2026-09-29)

## 1. Question and answer

**Question.** For closed trades from the last 14 days, does each hop (signal → `order_packages` → fill → monitor decisions → exit with a correct close reason → graded in `comms/claude_strategy_scores.jsonl`) meet its declared contract on live data? Does any exit stamped sl/tp sit outside every recorded bracket level?

**Answer.** Three hops hold and two do not. I measured all 328 closed trades (`closed_at >= 2026-09-15`, 7 accounts) instead of a stratified sample, which covers every account × exit-family cell.
- **Held:** the entry fill (0 of 328 drift more than 50 bps from the package entry) and the monitor-decision hop (every non-pairs trade has a `position_telemetry` row).
- **Recurrence check:** the `BL-20260826` mismatch class did not recur at its 22/28 rate. 13 of 71 sl/tp exits sit more than 0.2% from every recorded level, and 9 are wrong-side, all within 0.08%.
- **Failed, grading:** the graded hop has not run since 2026-09-08. 4 of 328 trades have a grade row, and all 4 are stale `orphaned` rows.
- **Failed, close reason:** 97 of 328 closes (30%) are reconciler-class rather than TP, SL or strategy-close. 6 of the 11 real-money closes are among them.
- **Established:** I read the live journal, so this is observed, not just merged. I did not verify what is deployed against each CA fix.

## 2. Findings

Full records are in `AUD-2.findings.jsonl`.

| id | sev | claim |
|---|---|---|
| SA-AUD-2-grading-hop-stalled | high | No grade row after 2026-09-08. 4 of 328 recent trades have a grade row, all stale `orphaned` rows from 2026-06-23. |
| SA-AUD-2-real-money-closes-unclassified | high | 6 of 11 real-money closes have no TP/SL/strategy-close reason. The bybit_2 `reconciler_filled` rows are `unresolved` and their PnL matches sl rows. |
| SA-AUD-2-sl-tp-exit-price-estimated | medium | 9 of 34 monitor_reconciler sl/tp exits use a candle price and an estimated label. Example: trade 6055, exit 1.6% past its stop, held 29h. |
| SA-AUD-2-ib-paper-pnl-zero | medium | 11 of 13 ib_paper closes carry pnl 0.0 with a non-zero price move. Extends CA-A01-002, which counted 6 on 2026-09-27; the count in this window is now 11. |
| SA-AUD-2-ib-paper-same-package-tranches | low | One MES package produced 4 same-account rows, and 3 of them carry pnl 0.0. |
| SA-AUD-2-cross-exit-provenance-mixed | low | 7 sl_cross rows take their price from `verdict`, not a fill. |
| SA-AUD-2-bracket-mismatch-recurrence-rate | info | Non-issue (partial). 13/71 mismatch, 9 wrong-side within 0.08%. The journal keeps only the original bracket, so stop moves cannot be checked. |
| SA-AUD-2-pairs-no-telemetry-nonissue | info | Non-issue. Only pairs closes lack telemetry, which is by design. |
| SA-AUD-2-entry-fill-nonissue | info | Non-issue. Median entry drift is 0-1.5 bps and the maximum is 42.2 bps. |

Counts: 0 critical, 2 high, 2 medium, 2 low, 3 info.

## 3. Coverage

**Behavioural.** 6 of 6 hops of the declared pipeline were exercised on live data. They are the package row, the entry fill, the monitor telemetry, the exit price against the bracket, the exit reason and provenance, and the grade. The sample was 328 trades, 302 packages and 7 accounts, and the unit was every closed trade in the window. What I did not exercise: broker-side resting orders (that is AUD-1's lane) and the FIX-CA-07/08/10 behaviour, which I did not test directly.

**Reading.** Files read:
- `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §3, §4.2 and §5 (excerpts)
- `docs/CLAUDE-RULES-CANONICAL.md` (autonomy mandate excerpt)
- `.claude/skills/diag-data/SKILL.md` (excerpt)
- `docs/ARCHITECTURE-CANONICAL.md` (pipeline steps 1-2 only)
- `src/web/api/routers/diag.py` and `src/units/db/database.py` (route list and table DDL excerpts)
- `comms/claude_strategy_scores.jsonl` (parsed in full)
- CA-A01 findings, by grep only

Explicitly NOT read: `src/runtime/pipeline.py`, the monitor and reconciler code, `docs/TRADE-PIPELINE.md`, the CA-A02/A03 lane files, and the grading workflow. The `signals` audit stream (`audit_query`) was not queried.

## 4. Could not look

- **Stop-move history.** The journal keeps only the original bracket, so "every bracket level ever recorded" is only partly testable. The 11 profitable sl-stamped exits cannot be confirmed as trailing ratchets.
- **Broker-side check.** Exits were not compared against Bybit or Alpaca order history. `bybit_raw_order_history` was not used.
- **Grade file on the VM.** I read the copy on `origin/main`. `ict-git-sync` pulls that copy, but I did not confirm the VM copy is identical.
- **Journal window.** `limit=1000` covered trades from 2026-09-02, which is enough for the 14-day window. I did not verify completeness against a broker count.
- **Diag transport.** The direct HTTPS relay worked (no 401). No relay issues were opened.

## 5. Spend

About 25 tool calls, all direct reads with no relay issues. Well under the $30 ceiling; I cannot read the dollar meter.
