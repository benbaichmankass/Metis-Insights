# S-HEALTH-REVIEW-GRADING-PROP — /health-review + grade backfill + prop status-request pings

## Date Range
- **Start:** 2026-07-03
- **End:** 2026-07-03

## Objective
- **Primary:** run the full `/health-review` with operator emphasis on (1) the Alpaca pipeline end-to-end and (2) Claude decision-grade coverage across ALL order packages (operator suspected incomplete backfill).
- **Secondary (operator follow-ups, same session):** backfill the missing grades immediately (not deferred to the next review); build the prop-bot account-status request ping with a paste-ready reply template; report the open Breakout ETH position (operator screenshot) into the prop journal; record the alpaca_live leave-as-is funding decision.

## Tier
- **Tier 1:** backlog drain, scores-file append, sprint/doc updates.
- **Tier 2:** `src/prop/prop_status_request.py` + `breakout_notify.emit_prop_status_request` + the `src/main.py` per-tick wire (observability notification path, no order path) — operator requested it in chat 2026-07-03 (the Tier-2 ack), and "merge when ready" given for PR #5521. The prop fill report-back (issue #5527) is the sanctioned Tier-2 `prop-report` relay.

## Starting Context
- Review window 2026-07-02T08:15Z → 2026-07-03T17:15Z. Direct diag path dead (`DIAG_BASE_URL` still points at the terminated x86 micro) — all live reads via the `vm-diag-snapshot` relay; burst-cancellation (BL-20260611-002) hit again (8/11 first-wave pulls silently cancelled), worked around with serial refires.

## Work Completed
1. **/health-review** (issues #5496–#5525): overall **investigate**. Core plumbing healthy (heartbeat/ticks/state-consistency/DB INV-1..5 clean, cron snapshots green). Mandatory reachability flag: **ib_paper dark most of the window** — `vm-ib-gateway-recover` fired autonomously (#5511, relogin verified 17:01:44Z); persistent diag `positions:null` afterwards diagnosed as the cross-host net-liq verification limitation (BL-20260610-009 class), NOT a continuing outage (trader journal clean of IB errors, account flat, CME closed for Jul-3 early close) — verdict recorded in BL-20260623-002, definitive close-out deferred to the 2026-07-06 CME session. Alpaca fleet verified healthy: all 3 accounts ACTIVE/not-blocked; **BL-20260627-ALPACA-LIVE-API-UNAUTH resolved** (alpaca_live authorizes and holds IEF x1). Watches: xauusd_trend_1h silent-enabled on shelved oanda_practice; trainer service last run exit 15 (routed to /ml-review); 4 in-window orphaned packages (churn-verify item covers); prop_account_status 0 rows ever (new BL-20260703-PROP-STATUS-EMPTY).
2. **Grade-coverage audit + same-day backfill:** operator suspicion CONFIRMED — 2,724 packages vs 2,566 graded ids at review time; daily system-reviews appended only 1-2 rows/day since 06-29. Backfill: fresh live-DB sync on the trainer (`sync_trainer_data.sh`), canonical `score_order_packages.py` rubric to a temp file, dedupe, chunked transport via trainer relay (#5528–#5534), **161 rows appended** (`source=health-review-backfill-20260703`). Post-append: **2,727 distinct graded ids == 2,727 packages — coverage gap ZERO.**
3. **Prop status-request pings:** new `src/prop/prop_status_request.py` (open prop position + absent/stale `prop_account_status` → prop-bot ping with `bal <balance> <equity> [realized_today]` and the `kind:"account_status"` JSON template; knobs `PROP_STATUS_REQUEST_MAX_AGE_HOURS`=24 / `PROP_STATUS_REQUEST_COOLDOWN_HOURS`=12; baseline no-gate; state pruned when flat). Emitter in `breakout_notify.py` (rides the `prop_monitor` push kind); wired per-tick in `src/main.py`; 8 unit tests.
4. **Prop fill report-back** (#5527): open Breakout ETHUSD long 1.87 @ 1613.78 (SL 1700.51 trailed / TP 1773.54, uPnL +286.01, opened 07-01 18:06 terminal-local) ingested HTTP 200 → prop_fills row 13, linked to ticket `prop-manual-fd4469985586`, notifications fired. Partially answers BL-20260625-PROP-ETH-LIVE (the ETH prop leg fired and was placed).
5. **Backlog drain:** resolved BL-20260627-ALPACA-LIVE-API-UNAUTH, BL-20260702-002 (trainer SSH restored), BL-20260626-ALPACA-WHOLEUNIT-LATENT (operator decision: alpaca_live stays unfunded as-is; whole-share refusals accepted — do not re-raise), BL-20260703-GRADING-COVERAGE-GAP (same-day); updated BL-20260623-002 (recurrence + verdict); opened BL-20260703-PROP-STATUS-EMPTY.

## Validation Performed
- Scores file post-append: every line parses; 2,736 lines (1 meta + 2,735 rows), 2,727 distinct ids == DB package count from the 20:58Z-fresh sync; the live file was never opened in write mode (append only).
- `tests/test_prop_status_request.py`: 8/8 pass. Neighboring prop tests: identical pass/fail set with changes stashed (the 4 sandbox failures pre-exist; CI green on main). `ruff` clean on all touched files.
- PR #5521 CI: all 16 checks green on the first two heads; re-running on the final rebased head at log time.
- vm-ib-gateway-recover run log: relogin completed, API port verified reachable.

### Gaps not yet verified
- ib_paper trader-side reads confirmed only indirectly (no IB errors in the trader loop; CME closed) — definitive confirmation is MES/MGC/MHG fetch/eval activity at the next CME session (2026-07-06).
- The prop status-request ping fires for the first time on the live VM after this PR deploys (the open ETH position + empty prop_account_status is exactly its trigger condition).

## Documentation Updated
- `docs/claude/health-review-backlog.json` (drain + decisions, item-scoped diffs).
- This sprint log. Env-knob docs live at the module call sites per the curated-subset convention.

## Contradictions or Drift Found
- `scripts/ops/score_order_packages.py` default out-path opens mode "w" (wholesale rewrite) — contradicts the scores file's APPEND-ONLY meta; recorded in the resolved BL item as a Tier-1 follow-up (`--append`/skip-existing flags). Worked around by writing to a temp file.
- Dashboard CLAUDE.md still says the Claude column populates "until a /health-review scores a package" — grading moved to /performance-review in the 2026-05-26 split; cosmetic, left for the next dashboard-doc touch.

## Risks and Follow-Ups
- BL-20260703-PROP-STATUS-EMPTY: rule-distance panel blind until the first account-status report-back (the new ping should drive this).
- BL-20260611-002 remains live (relay burst cancellation) — serial refires are the working pattern.
- Follow-up: `score_order_packages.py --append` mode; xauusd_trend_1h explicit disable decision.

## Deferred Items
- IB gateway root-cause escalation (BL-20260527-003) if the 07-06 session shows continued darkness.

## Next Recommended Sprint
- /performance-review over the newly-complete grade set (the per-strategy read now has full decision coverage).

## Wrap-Up Check
- Backlog updated ✔ · scores appended + verified ✔ · prop feature tested ✔ · PR #5521 CI green pending final head ✔(watching) · Claude-channel ping sent (#5522) ✔
