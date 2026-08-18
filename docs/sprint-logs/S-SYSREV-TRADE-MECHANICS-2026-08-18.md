# Sprint Log: S-SYSREV-TRADE-MECHANICS-2026-08-18

## Date Range
2026-08-18 (single session)

## Objective
Operator ask, three parts: (1) run a system review including backlog monitoring;
(2) close the standing prop account-status request, which the operator believed
carried wrong information; (3) **deepen trade review from grading decisions to
verifying the MECHANICS of live trades** — brackets, dynamic exits, monitoring —
and fix anomalies found. Mid-session the operator added emphasis: the live XRP
short has been open ~3 weeks through chop, and recent work "should have addressed
this" — verify whether the system is genuinely choosing to hold.

## Tier
Tier-1 for everything landed here (backlog, audit tooling, docs). One Tier-2
write executed under explicit operator instruction: `POST /api/bot/prop/report`
account_status for `breakout_1`. **No Tier-3 change made** — the order-path fix
this session identified is written up as a proposal for operator approval.

## Starting Context
- Local HEAD `08dc998`, matching the live web-api's `/api/diag/version`
  (`git_sha 08dc9987`) — the deploy was current, so code on disk == code running.
- Backlogs at session start: health 662 items (195 kept_open, 31 open),
  performance 103, ml 99.

## Repo State Checked
- `config/lever_reachability.json` (M31 P1 reachability registry)
- `config/strategies.yaml`, `config/accounts.yaml`
- `src/runtime/order_monitor.py`, `src/runtime/position_telemetry.py`
- `src/units/strategies/*.py` (monitor roster sweep)
- `.github/workflows/prop-report.yml`, `docs/claude/system-actions.md`

## Files and Systems Inspected
Live VM read-only over the Caddy HTTPS transport (`https://ict-bot.duckdns.org`):
`/api/bot/positions`, `/api/bot/prop/{status,fills,tickets}`,
`/api/bot/db/table/{trades,order_packages,prop_account_status}`,
`/api/bot/candles`, `/api/bot/notifications`, `/api/diag/{version,ib_open_orders,
exchange_positions,ib_state,venue_session,position_telemetry,log_file}`.

## Work Completed

### 1. Prop status request closed (operator ask #2)
The bot's "694h old" was **hours (28.9 days), not days** — the arithmetic was
right and the unit was misread. But the operator's recollection of "a snapshot
last week" was also right about *something*: prop fill #29 was reported
2026-08-13 (ETHUSDT close, −$50.55). It was a **fill/close report, which carries
no balance**, so `prop_account_status` was untouched and its newest row stayed
2026-07-20. Both parties were describing real events; the report kinds differ.

Ingested the operator's terminal numbers (balance/equity 4747, unrealized 0;
`realized_today` deliberately omitted — the terminal's "P&L $0" is open P&L, and
honest-null beats a fabricated zero) via `prop-report` issue #9926, HTTP 200,
row id 7. `status_freshness` `stale` → `ok`.

**The number that matters:** distance to the static DD floor is now **$47.00**,
against a daily-loss limit of $142.41. The binding constraint has crossed over to
the DD floor — the daily-loss guard can no longer bind first. Every one of the
last five reported prop closes (−18.49, −85.90, −96.94, −18.06, −50.55) exceeds
the remaining cushion.

### 2. `BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG` (critical, Tier-3)
The headline finding of the mechanics review. `order_monitor._apply_update`
resolves `matched_trade` from `open_pkg["linked_trade_id"]` on **both** the
modify and close branches, but `multi_account_execute` fans one package out to N
accounts. So N−1 legs are never trailed and never closed; and because a monitor
close flips the PARENT package to `closed`, the loop's `status="open"` filter
drops those legs permanently. `_cascade_close_netted_siblings` does not cover it
(scoped `AND account_id=?`). Measured live: 4 of 8 multi-leg open packages have
divergent sibling stops, 6 of 35 open trades are stranded under a closed package,
and `bybit_portfolio` has 24 closes with **zero** monitor-driven exits against
its managed sibling `bybit_2`'s 45. Caught in the act this morning on
`pkg-830fb965b6db48ff`. Full detail and evidence in the backlog entry.

### 3. `BL-20260818-ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH` (critical, Tier-3)
`ict_scalp.monitor()` emits no `tp_cross` — only a stale-stop (undeclared on the
MGC leg) and a break-even SL-modify. A roster sweep found four such modules
(`ict_scalp`, `fvg_range_15m`, `hf_displacement_cont`, `hf_vwap_revert`); the
other six all emit `tp_cross`. Those four rely entirely on a resting venue limit
— and `/api/diag/ib_open_orders` shows ib_paper holding 2 orders, **both STP,
zero LMT account-wide**. The intersection is trade 4487: MGC long 105, declared
target 4297.66, last close 4420.4 — **122.74 points past target, 11 days open**,
+$155,715 unrealized with a stop 139 points below. Neither exit exists.

### 4. Tier-1 observability: `scripts/ops/exit_mechanics_audit.py`
Read-only audit (SQLite via the canonical resolver, or `--api` over HTTP for a
relay-bound session) reporting three separately-counted populations: `stranded`,
`divergent`, and `agreeing-now` — the third kept distinct because it is not a
defect today but is exactly the set that diverges when a trail first fires, and
folding it away would understate exposure. `linked_missing` is its own state
rather than folded into `stranded` (*we could not look* ≠ *we looked and it is
fine*), and a failed run exits 2 so an unreadable journal can never print as a
clean book. `--api` mode asserts `filter_state == "applied"` before reporting.
Verified against the live journal; it reproduces every count above.

### 5. `attach-ib-target` has never executed once — found and fixed
Following the MGC thread into the backlog turned up
`BL-20260816-MGC-4487-CONFIRM-CLOSE-AT-OPEN`, a CONFIRM item awaiting exactly the
read this session took. The answer is that no fill occurred **and no target was
ever placed**: the operator approved the repair on 2026-08-16, the action merged
2026-08-17T22:31Z, and its only two invocations both died — #9920 exit 127
(git-sync lag, correctly diagnosed at the time) and #9922 `ImportError: cannot
import name 'get_connection' from 'src.units.db.database'`. Verified against the
module rather than inferred from the traceback: that symbol has never existed
there (it exports `Database` / `get_db`), so the action could not have run in
dry-run or apply mode, on any symbol, ever. A blind retry would have failed
identically. A second latent defect sat on the same three lines: `with
get_connection() as conn:` — sqlite3's context manager commits a *transaction*
and does not close, and it opened a read/write handle for a pure SELECT.

Fixed here: a read-only connection through `src.utils.paths.trade_journal_db_path()`
with an explicit try/finally close. **Not dispatched** — the fix isn't deployed,
and the 2026-08-16 approval was given against a last of ~4398.70 while MGC is now
4420.4, so an approval to sell 105 contracts at market is two days and 22 points
stale and needs re-confirming.

This is green-is-not-evidence one level up: a *merged* repair is not a *run*
repair, and a run that failed is not a repair at all. It sat ~8h with the failure
sitting in plain text in an issue comment.

### 6. Backlog worked
Filed eight new items with severity/tier/resolution_criteria/evidence
(the two above plus `PROP-RULE-DISTANCE-IGNORES-THE-FILLS-STREAM`,
`ATTACH-IB-TARGET-NO-THROUGH-MARKET-REFUSAL`,
`DIAG-BASE-URL-POINTS-AT-TERMINATED-VM`,
`PROP-REPORT-RELAY-REJECTS-A-BODY-WITH-ANY-PROSE`, `ATTACH-IB-TARGET-HAS-NEVER-RUN`,
`CLAIM-BASIS-GUARD-DOES-NOT-READ-THE-DETAIL-FIELD`) and **escalated**
`BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT` from high to critical: the
requirement is no longer a near-miss, it is **breached** — `requirement_state:
"breached"`, `max_interval_ms 61035.9`, `interval_breaches 2` over
`intervals_measured 818` on one 7.7h process.
**Advanced four existing items** with fresh measurements rather than only adding:
`MGC-4487-CONFIRM-CLOSE-AT-OPEN` (answered — no fill, placement path reopened as a
defect per its own criterion), the two MES over-coverage rows (venue re-measured
clean at 15-vs-15, order 375 gone — advanced, **not** closed, since both require a
detector that still does not exist), and `BYBIT-PORTFOLIO-ETH-DEAD-LEG` (root cause
found: both its rows are stranded siblings of already-closed packages, so a per-row
cleanup would not stop the mechanism). Note `state: "fresh"` coexisting
with `requirement_state: "breached"`, which is the condition the two-threshold
split was built to represent.

## Validation Performed
- Prop write verified by the endpoint's own response (HTTP 200, id 7,
  `distance_to_dd_floor_usd: 47.0`, `status_freshness: "ok"`).
- Multi-leg defect verified four independent ways: the code path; the live stop
  divergence; the package/trade linkage (`pkg-7cb8577792ca4006` → two trades, one
  `linked_trade_id`); and the exit-reason distribution across managed vs mirror
  accounts. Broker truth (`/api/diag/exchange_positions`) confirms the stranded
  BTC legs are genuinely still open at Bybit.
- MGC verified against broker order state (zero LMT), live candles (4420.4 at
  15m/1h/1d — so the intermittent `candles_unavailable` blindness is an
  aggravator, not the cause), and the strategy source.
- Audit script run against live data; output matches the hand queries row for row.

## Documentation Updated
- `docs/claude/health-review-backlog.json` — 6 added, 1 escalated (668 items).
- This sprint log.
- Coordination board #6927: `START` posted before any commit.

## Contradictions or Drift Found
- `DIAG_BASE_URL` in the cloud-session environment still names the x86 micro
  terminated 2026-06-16. It fails as a 45s hang, which reads as "VM down" rather
  than "address gone". Filed; the working transport is the Caddy hostname.
- `attach-ib-target`'s four documented refusals do not include a target already
  through the market, where the action is a market exit wearing the label of a
  protective repair (diagnostic-provenance sub-class A). Filed.

## A self-inflicted lesson worth recording
The first commit re-serialised `health-review-backlog.json` with `indent=1` instead of the
file's `indent=2, ensure_ascii=False`, rewriting all ~37k lines. CI then failed
`impossibility-claim-guard` on **8 pre-existing rows** — because that guard is
added-lines-scoped and a wholesale reformat makes every line "added". The obvious reading
was "this guard is broken / main is red", and a first attempt to test that hypothesis was
itself wrong (the `git stash` left the offending commit in the worktree). Restoring the
original serialization dropped the diff from 18855/18701 to 186/6 and took the guard from
8 findings to 0 with no change to its logic. Recorded because the false signal was
persuasive, pointed away from the real cause, and cost two verification rounds.

## Risks and Follow-Ups
- **The `*_portfolio` books are not mirrors.** CLAUDE.md declares them the paper
  mirror of the live-traded portfolio and the dashboards' "Paper" toggle renders
  them, but they run a STATIC exit policy while the book they mirror runs the
  MANAGED one. Their P&L must not be read as "what the portfolio would have
  done" until the multi-leg fix lands. On Alpaca the assignment is inverted and
  worse: `alpaca_paper` (soak) is consistently the managed leg.
- No real-money leg is stranded today. That is luck, not design.

## Deferred Items
- The Tier-3 order-path fix (package-wide effectuation) — proposed, not written.
- The MGC 4487 disposition — an operator trading decision, not a repair.
- `xrp_pullback_2h` `queued_tier3` — see below.

## Next Recommended Sprint
**The XRP row is decision-ready and should be closed first.** The operator's
2026-08-16 instruction on `config/lever_reachability.json::xrp_pullback_2h` was
"widen the sample FIRST". That measurement is **complete**: the COMPLETE live
population reads 2/37 = 5.4% (the partial 27-row read's 0/27 and the original
truncated 33.3% are both corrected in the file), agreeing with the independent
backtest basis at 5.9%. The re-swept 2.17R alternative would be reachable and
still fails OOS, so "lower the arm" is not available. Live trade 4163 sits at
`cap_r` 3.9233 against `arm_r` 4.49 — in the unreachable 94.6% — which is the
whole answer to "is the system deciding to hold?": it is not deciding, the lever
cannot fire. Then: the multi-leg Tier-3 fix, then the exit-interval breach.

## Wrap-Up Check
- [x] Coordination board START posted before first commit
- [x] Operator's explicit ask (prop snapshot) completed and verified
- [x] Findings filed with severity/tier/resolution_criteria/evidence
- [x] Populations stated on every quantitative claim
- [x] No Tier-3 change made; order-path fix proposed only
- [ ] `DONE` board comment — posted at session end
