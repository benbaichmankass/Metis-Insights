# The work system — how due work reaches someone who acts

> **Doc status:** `unknown` · category `plan` · last verified `never` · adopted `2026-10-04` · checklist row **WORK-SYSTEM**
> Operator, 2026-10-04 ~15:05Z, approving it: *"that's not a band-aid, that's a
> structural fix … make sure it's canonized correctly in the manager skill so all
> the managers pick it up … this is highest priority."*
> Binding summary: [`.claude/skills/manager/SKILL.md`](../../.claude/skills/manager/SKILL.md)
> § "The work system" and [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md)
> § "Due work must reach someone".

## Why

MEASURED 2026-10-04 by the manager over `docs/claude/work/pipeline/` (1,802
items): **366 due, 151 unrouted**, 237 of the due never checked once, 215
`routed` and still due, **25 `ask_operator` due and never sent to the operator**.
36 of 45 Stage-1 soak legs sat at `insufficient-data` with no end date. The R5
grade's six `kill` calls (09-26) were never acted on, and this week's grade ran
but landed no record (newest file is `comms/research/soak_book_grade/2026-09-26.json`).
The brief was 737 KB.

**Root cause: every attention signal was a page someone had to open.** Nothing
was pushed and nothing noticed silence. The fix has three parts. The VM
computes due. The VM pushes to a person. And an expected signal that does not
arrive raises an alarm.

## 1. Lifecycle of each work type

| type | created as | "due" means | ends as |
|---|---|---|---|
| **build** | checklist row `queued` | the manager dispatches it | `landed_unproven` (merged) → `done` (observed); `dropped` + reason |
| **research unit** | `research/queue/<id>.yaml` with a pre-registered `decision_rule` | the dispatcher runs it | result in `research/results/`; the rule *is* the decision (mandate or `MD-KILL-QUESTION`) |
| **soak** | a pipeline row carrying a **soak contract** (§5) | `ready`, `overdue` or `dead` | pass → Gate-2 evidence; fail → demote or kill; no "keep waiting" |
| **observation** | pipeline row, `due_when.kind=observation` with `clears_when` and `check_every_days` | `last_checked + every ≤ today` | `done` when `clears_when` holds; `killed` + `terminal_reason` |
| **decision** | pipeline row, `next_action=ask_operator`, classified as a preference per manager skill § "Before any operator popup" | at creation | operator answer recorded verbatim on the row → `done` |
| **alert** | a push from `attention_watch` or an existing notifier | at send | the manager's next review routes the item it names |

## 2. Who computes "due"

| signal | computed by | runs where | how often |
|---|---|---|---|
| pipeline due / unrouted | `scripts/ops/pipeline.py::is_due` / `unrouted` | VM, live on every brief request and every watch pass | per request, hourly |
| soak state (`accruing`/`ready`/`overdue`/`dead`/`unknown`) | **SOAK-WATCH**, published to `docs/claude/work/SOAKS.json` (one file, read by the brief and the watch) | VM | hourly (consumed by the watch) |
| checklist row health | `scripts/ops/attention_watch.py::probe_inflight` (git blame age of the row) | VM | hourly |
| silence (expected signal missing) | `attention_watch.py` probes (§4) | VM | hourly |

Nothing is "due" because someone declared it. **The clock and the data decide.**

## 3. Where each signal surfaces, who acts, how fast

| signal | surface | to | acts | within |
|---|---|---|---|---|
| daily summary (counts + top 8 ranked, ask_operator first) | Telegram via @claude_ict_comms_bot, after 06:00Z, on the existing carrier | operator | reads; answers any ❓ASK | same day |
| new `ask_operator` item | Telegram, `high`, on creation | operator | answers, or says "make it a mandate" | 24h |
| soak → `ready` | Telegram `normal` + brief soaks slot | manager | grade against the contract; promote/kill per mandate | next daily review |
| soak → `overdue` / `dead` | Telegram `high` + brief | manager | kill, or redesign the contract | next daily review |
| expected signal missing (§4) | Telegram `high`, re-sent every 24h while breached; one ✅ on clear | operator, and the manager at its review | dispatch a fix lane | next daily review |
| everything due | the persisted daily report, `GET /api/bot/work/report` (≈05:00Z). The live view is `GET /api/bot/work/brief`, short and ranked with a soaks slot (**BRIEF-FIX**). | manager | dispatch, close or decide each one | daily review (05:52Z routine) |
| lane finished / blocked | `create_trigger(persistent_session_id=<manager>)` + `fire_trigger` | manager | merge/archive, or re-dispatch | on wake |
| urgent live trading issue | existing trader alerts (@bict_trading_bot) | operator | unchanged | unchanged |

## 3a. The persisted daily report and the manager's review loop

The operator, 2026-10-04 ~15:15Z: *"Even if it's not directly a push, it needs to
be a concentrated, regularly generated report that the manager knows to look
at … if it's not getting a ping when the generation happens, then it at least
needs to know to periodically check to see if there's a new report and to
review it."*

| | |
|---|---|
| generator | `scripts/ops/work_report.py`, generated on the **first `ict-work-digest` pass at or after 05:00 UTC** (the one scheduled carrier; no timer of its own) |
| stored at | VM `runtime_logs/work_reports/<report_id>.json` + `latest.json` (kept 45 days) |
| served at | `GET /api/bot/work/report` (latest) · `?report_id=…` (a past one) |
| `report_id` | `WR-YYYYMMDD-HHMMZ`, the generation time in UTC, e.g. `WR-20261005-0530Z` |
| contents | expected-signal alarms · soak states (dead/overdue/ready first) · open `ask_operator` items · due count + top 25 ranked · then the brief verbatim (`render_daily_brief.render()`, BRIEF-FIX's renderer; §3 is "what moved"). Each section carries `ok` / `empty` / `error`. |
| alarm if missing | `attention_watch` probe `report` fires (Telegram, `high`) when, at 05:50Z, no report was generated today, when the latest is more than 26h old, when any section has `error`, or when the report is over 64 KB |

**The manager gets NO push when the report is generated.** The daily-review
routine (05:52Z) is the "periodically check" path, and its prompt names
`GET /api/bot/work/report`. It works like this:
1. Fetch the latest report.
2. Check `generatedAt`. If it is not today's ≈05:00–05:10Z, or `erroredSections` is
   non-empty, that is the first incident to fix.
3. Work each item: dispatch, close or decide.
4. Record `last_review` = `{report_id, reviewed_at, by, counts}` at the top
   level of `MANAGER-CHECKLIST.json`.

Per-item dispositions go on the pipeline items themselves (`state`,
`routed_to`, `terminal_reason`), because that is where the next manager and the
next report read them. Copying them into the checklist would create a second
source. The next manager then compares the `report_id` in `last_review` with
the latest report to see what is new.

Anything due at a known time gets a `send_later` wake instead of a hope.

## 4. Silence detection — what alerts when nothing arrives

`scripts/ops/attention_watch.py` runs on every hourly `ict-work-digest` pass (§ 7).
Each probe reads `ok` / `breached` / `unknown`. **`unknown` never clears an
alarm.**

| expected signal | breached when | why this bound |
|---|---|---|
| R5 weekly soak grade lands | newest `comms/research/soak_book_grade/*.json` ≥ 8 days old | weekly (Sun 03:00Z) + 1 day |
| research results land | no commit to `research/results/` in 36h | the dispatcher lands several a day when healthy |
| `in_flight` rows move | a row's checklist lines untouched ≥ 3 days | a row nobody writes to is not in flight |
| the manager reviews | checklist not committed in 30h | one daily review + 6h |
| the daily report is produced, fresh and whole | no report today by 05:50Z, latest > 26h old, an `error` section, or > 64 KB | the manager reviews from it, so a missing report means a blind review |
| the watch itself runs | `runtime_logs/attention_watch_receipt.json` stale (read via `/api/diag/log_file?name=attention_watch_receipt`) | its own heartbeat; the manager checks it at review |

The manager-silence probe is the backstop for the whole design. Managers are
sessions and sessions end. If none reviews for 30h, the operator is told to
start one.

## 5. The soak contract

Every soak is created with all five of these, or it is not created:

1. **What it verifies.** Live mechanics and realized cost match the Stage-0
   backtest. Never edge.
2. **Expected event rate, taken from the backtest** (trades per week on this
   leg × symbol × timeframe).
3. **n needed and its power** for the pass/fail rule.
4. **End date** = start + n ÷ rate. **Longer than ~2 weeks means the design is
   wrong.** Fix it up front: a wider soak book, a looser n backed by a stated
   power, or a different instrument. Do not just wait longer.
5. **Pass/fail rule**, registered before the soak starts.

States: `accruing` (on pace, before the end date) · `ready` (n reached) ·
`overdue` (end date passed, n not reached) · `dead` (no events when the rate
says some were due) · `unknown` (could not read). Only `ready`, `overdue` and
`dead` push. SOAK-WATCH owns the computation and the report.

## 6. What is built where

| piece | owner | state |
|---|---|---|
| this doc, skill + canonical rules, checklist row | WORK-SYSTEM | this PR |
| `attention_watch.py` + `work_report.py`, both run by the EXISTING `ict-work-digest` pass; `GET /api/bot/work/report`; receipt in the diag allowlist | WORK-SYSTEM | this PR; **Tier-2 (code on the existing VM carrier + a web-api route), held for the manager to merge** |
| soak contract, `SOAKS.json` (with `state`), soak alarms on the pipeline, soak report, weekly grade producer | SOAK-WATCH | its own PR |
| short, ranked, truthful brief with a soaks slot and size cap | BRIEF-FIX | its own PR |

**Closes when** (row WORK-SYSTEM): (1) `/api/diag/services` shows
`ict-work-digest.timer` active, with `attention_watch_receipt` showing a pass; (1a)
`GET /api/bot/work/report` returns a `WR-…-050xZ` report with no errored
section; (2) the receipt shows `outcome: sent` with
a digest; (3) the operator confirms a digest arrived in Telegram; (4) the brief
is under BRIEF-FIX's size cap and its soaks slot is populated from
`SOAKS.json`; (5) the first silence alarm has fired and later cleared. The
soak-grade alarm is breached today, so it fires on the first pass.

## 7. One carrier, not two (manager review of #16387, 2026-10-04)

The VM already runs `ict-work-digest.timer` hourly. It drives
`work_digest_now.py`, the hourly digest of state CHANGES: checklist
transitions and the standing close-wedge ledger. MEASURED: its receipt read
`sent` for hour 2026-10-04T15. Its content answers *what changed*. It never
showed due items, `ask_operator`, soaks or silence, which is why things were
still ignored while it ran.

The first draft of this PR added a second timer and a second digest beside it.
That draft was withdrawn, because it was the "new register beside the old one"
shape and it doubled the noise. The design now:

- **One timer.** `work_digest_now.run()` sends its change digest, then calls
  `attention_watch.run()`. That one call produces the daily report (at or
  after 05:00Z), the daily summary (at or after 06:00Z), and the edge alerts
  and silence alarms, which go out immediately and only on change. No unit was
  added, and none was retired.
- **Known limit.** The carrier's own death silences the alarms too. The
  backstop is the manager's 05:52Z review routine. It reads `generatedAt` and
  treats a stale report as the first incident.
- **Cadence is the operator's call, raised, not flipped.** The hourly change
  digest is hourly because the operator chose it on 2026-09-02 (*"that's how
  I want it for now until we get a little more settled in"*). The proposal is
  daily for change summaries and immediate for edge alerts.

