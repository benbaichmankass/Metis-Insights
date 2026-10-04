# The work system — how due work reaches someone who acts

> **Doc status:** `live` · category `plan` · adopted `2026-10-04` · checklist row **WORK-SYSTEM**
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
| soak state (`accruing`/`ready`/`overdue`/`dead`/`unknown`) | `scripts/ops/soak_state.py::soak_states()`, built by **SOAK-WATCH** | VM | hourly (consumed by the watch) |
| checklist row health | `scripts/ops/attention_watch.py::probe_inflight` (git blame age of the row) | VM | hourly |
| silence (expected signal missing) | `attention_watch.py` probes (§4) | VM | hourly |

Nothing is "due" because someone declared it. **The clock and the data decide.**

## 3. Where each signal surfaces, who acts, how fast

| signal | surface | to | acts | within |
|---|---|---|---|---|
| daily digest (counts + top 8 ranked, ask_operator first) | Telegram via @claude_ict_comms_bot, after 06:00Z | operator | reads; answers any ❓ASK | same day |
| new `ask_operator` item | Telegram, `high`, on creation | operator | answers, or says "make it a mandate" | 24h |
| soak → `ready` | Telegram `normal` + brief soaks slot | manager | grade against the contract; promote/kill per mandate | next daily review |
| soak → `overdue` / `dead` | Telegram `high` + brief | manager | kill, or redesign the contract | next daily review |
| expected signal missing (§4) | Telegram `high`, re-sent every 24h while breached; one ✅ on clear | operator, and the manager at its review | dispatch a fix lane | next daily review |
| everything due | brief `GET /api/bot/work/brief`. Short and ranked, with a soaks slot (**BRIEF-FIX**). | manager | dispatch, close or decide each one | daily review |
| lane finished / blocked | `create_trigger(persistent_session_id=<manager>)` + `fire_trigger` | manager | merge/archive, or re-dispatch | on wake |
| urgent live trading issue | existing trader alerts (@bict_trading_bot) | operator | unchanged | unchanged |

**The manager's daily review starts from the brief** (manager skill § "The
daily review"). Anything due at a known time gets a scheduled wake
(`send_later`) instead of a hope.

## 4. Silence detection — what alerts when nothing arrives

`scripts/ops/attention_watch.py` runs hourly on `ict-attention-watch.timer`.
Each probe reads `ok` / `breached` / `unknown`. **`unknown` never clears an
alarm.**

| expected signal | breached when | why this bound |
|---|---|---|
| R5 weekly soak grade lands | newest `comms/research/soak_book_grade/*.json` ≥ 8 days old | weekly (Sun 03:00Z) + 1 day |
| research results land | no commit to `research/results/` in 36h | the dispatcher lands several a day when healthy |
| `in_flight` rows move | a row's checklist lines untouched ≥ 3 days | a row nobody writes to is not in flight |
| the manager reviews | checklist not committed in 30h | one daily review + 6h |
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
| `attention_watch.py` + `ict-attention-watch.{service,timer}` + diag allowlist | WORK-SYSTEM | this PR; **Tier-2 (new VM timer), held for the manager to merge** |
| soak contract, `soak_state.py`, soak alarms on the pipeline, soak report, weekly grade producer | SOAK-WATCH | its own PR |
| short, ranked, truthful brief with a soaks slot and size cap | BRIEF-FIX | its own PR |

**Closes when** (row WORK-SYSTEM): (1) `/api/diag/services` shows
`ict-attention-watch.timer` active; (2) the receipt shows `outcome: sent` with
a digest; (3) the operator confirms a digest arrived in Telegram; (4) the brief
is under BRIEF-FIX's size cap and its soaks slot is populated from
`soak_states()`; (5) the first silence alarm has fired and later cleared. The
soak-grade alarm is breached today, so it fires on the first pass.
