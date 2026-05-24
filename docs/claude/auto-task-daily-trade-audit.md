# Auto-task: Daily one-trade lifecycle audit

S-067 follow-up #7. Auto-task / Audit-debug category. (The
`docs/claude/workplan.md` references in this file are **historical** —
the workplan was superseded 2026-05-10; permission tiers + merge
authority are now in `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers,
and milestone/sprint state in `ROADMAP.md`.)

## Why this exists

Aggregate metrics hide single-trade pathologies. Trade #1049 (the
2026-05-10 review canary) only surfaced because two PRs named it; the
broken `closed → exchange-flat` invariant it exposed was invisible in
the dashboard's daily P&L number, the closed-trades panel, and the
hourly-report digest.

A daily one-trade walkthrough — "pick yesterday's trade #N at random,
narrate every stage of its lifecycle, flag any divergence" — surfaces
that class of bug before it compounds. Velotrade's prop-firm audit
process uses the same pattern: one mid-window trade reviewed
end-to-end is more valuable than ten skim-reads.

## Inputs

- `trade_journal.db::trades` — closed trades from the audit window.
- `trade_journal.db::order_packages` — package lifecycle for the
  picked trade.
- `runtime_logs/signal_audit.jsonl` — the originating signal +
  pipeline events.
- `runtime_state/exchange_fills.sqlite` — exchange-truth fills
  (S-067 follow-up #6 — only available once the puller is running).
- Optional: `journalctl -u ict-trader-live -u ict-web-api --since
  yesterday` for context.

## Selection logic

Pick from yesterday's closed (live, non-backtest) trades. Weight the
random draw to oversample the trades a regression most likely lives
in:

| Bucket | Weight | Why |
|---|---|---|
| `exit_reason` matches `reconciler*` | ×4 | Reconciler-driven exits are the canonical "something went wrong" closure path — these are exactly where bugs like trade #1049 hide. |
| absolute P&L > p90 of the window | ×3 | Outliers are either huge wins (rule out pipe-overflow / wrong-symbol bugs) or huge losses (rule out missed stops / fee miscalc). |
| absolute P&L < p10 (small but non-zero) | ×2 | Microscopic non-zero P&L often signals a fee-only "trade" — the canonical no-actual-fill bug. |
| trade duration > p90 of the window | ×2 | Stuck-long trades are the closed-but-still-open class (#1049). |
| anything else | ×1 | Background draw to ensure regular paths get audited too. |

Implementation: query yesterday's trades, score each by the rule
that matches it (sum if multiple bucket hits), draw one with
weighted probability. If no trades closed yesterday, expand the
window to "the last 7 days" before giving up; document the expansion
in the audit output.

The picker is deterministic given the date — seed the RNG with
yesterday's UTC date string so the same audit isn't run twice on a
re-trigger, and the operator can re-derive the pick from the date
alone.

## Audit walkthrough template

Each audit produces one markdown file under
`docs/claude/audits/trade-NNNN-YYYY-MM-DD.md` (NNNN = trade id,
YYYY-MM-DD = the audit date, not the trade date). The file follows
this template:

```markdown
# Trade NNNN — daily one-trade lifecycle audit

- **Audit date:** YYYY-MM-DD (UTC)
- **Picked because:** <weight bucket(s)> with weight = <total>
- **Trade row at pick time:** <symbol> <direction> <qty> @
  <entry_price> → <exit_price>, status=<status>, pnl=<pnl>
  <pnl_pct>, exit_reason=<exit_reason>
- **Account:** <account_id>
- **Strategy:** <strategy_name>

## 1. Signal

`runtime_logs/signal_audit.jsonl` rows for the originating event
(timestamp window: 1h before the trade's `timestamp`):

| event | ts | side | confidence | price | pattern | meta |
|...|

**Pass / fail:** <does the signal exist in the audit log? Is its
shape consistent with what the writer wrote?>

## 2. Entry (`order_packages` open)

`order_packages` row for `linked_trade_id = NNNN`:

| order_package_id | created_at | strategy_name | symbol | direction | entry | sl | tp | confidence |

**Pass / fail:** <is the package linked to the trade? Are
direction / symbol / qty consistent between signal, package, and
trade row?>

## 3. Monitor ticks

`runtime_logs/signal_audit.jsonl` lines tagged with
`order_package_id` between package open and trade close. Note:

* Tick cadence — were ticks fired at the expected interval, or
  was there a multi-tick gap?
* Stop / take-profit moves — does each move match
  `order_package.updated_at` advances?
* Any `monitor_*` warning / error lines?

**Pass / fail:** <are the ticks contiguous? Did the monitor see
the price action the chart shows?>

## 4. Exit verdict

* `exit_reason` from `trades`: <value>
* Reconciler-close marker in `notes`?
* Time delta from last monitor tick to close.

**Pass / fail:** <is the exit reason consistent with the price
action — i.e., did SL/TP actually fire, or was this a reconciler
sweep of a closed-DB / open-exchange row?>

## 5. Exchange truth (when fills puller is running)

Match against `runtime_state/exchange_fills.sqlite`:

* Fills attributed to this trade (by symbol + time window).
* Net qty + total fee from fills.
* Compare against `trade.qty` and `trade.pnl`.

**Pass / fail:** <are the local DB numbers within fee/slippage
tolerance of the exchange-truth numbers?>

## 6. DB row

* `trades` row: <full row dump>
* `order_packages` row: <full row dump>

**Pass / fail:** <are foreign keys consistent? Is `closed_at` set
where the wire shape requires it (`order_packages.updated_at` or
`notes.closed_at`)?>

## Verdict

**OK** | **Anomaly** | **Bug filed**

If anomaly:
* What's the divergence?
* What's the smallest reproducer?
* Filed bug: BUG-NNN

If bug:
* Is it Tier-1 / Tier-2 by `docs/claude/workplan.md` § Decision and
  merge authority?
* Linked PR / issue.
```

## Schedule

The audit is a daily auto-task — wired into the existing daily
auto-task runner (the same routine that today picks roadmap items
or janitor sweeps). Scheduling options, in order of preference:

1. **Auto-task instruction file** — point the daily auto-task
   driver at this doc on the days the operator wants a trade
   audit. The driver chooses based on its category rotation.
2. **Manual operator trigger** — the operator types
   `/auto-task daily-trade-audit` (Telegram) on demand; the
   bot session reads this doc and runs the workflow.
3. **Cron-style** — out of scope for the auto-task system today;
   filed as a follow-up if (1) and (2) prove too sparse.

## Output handling

* The audit doc lands at
  `docs/claude/audits/trade-NNNN-YYYY-MM-DD.md` and is committed
  + pushed in the same session that ran the audit.
* If the audit's verdict is **Bug filed**, the bug entry goes
  through the standard `docs/claude/bug-log.md` flow (or
  `bug-log-pending/` staging if the file is too large).
* If the audit's verdict is **Anomaly** but no bug is filed yet,
  the anomaly is summarised one-line in
  `docs/claude/milestone-state.md` § Notes for the next planning
  session, so the operator can decide whether to escalate.
* Mismatches escalate via ClaudeBot Telegram one-way per the
  workplan's §  Decision and merge authority rules; no operator
  ack is needed to *file* the audit, only to act on a Tier-2
  fix.

## Bootstrap

This is auto-task category instructions. The first audit will run
when the daily auto-task driver picks this category; until then
the doc is filed but unused. Day-1 expectation:

* The puller in S-067 follow-up #6 may not be running yet; § 5
  (Exchange truth) gracefully degrades — note "fills puller not
  yet enabled" and skip the section.
* `docs/claude/audits/` is created lazily on the first run.

## Cross-references

* `docs/sprint-summaries/sprint-067-summary.md` § Hand-off — this
  is item #7 of the queued S-067 follow-ups.
* `docs/claude/workplan.md` § Auto-task routine.
* `docs/claude/exchange-truth-attribution.md` — phase-1 fills
  store; the audit's § 5 read source.
* `docs/audits/silent-empty-2026-05-10.md` — the original audit
  that surfaced the trade #1049 canary.
