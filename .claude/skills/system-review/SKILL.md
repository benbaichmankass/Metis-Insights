---
name: system-review
description: Master SYSTEM REVIEW session — the WORK is the review; the report is just its deliverable. Runs all three reviews (/health-review + /performance-review + /ml-review), actively reviews the whole system since the last review (technical health + every trade graded + strategy promotion/demotion readiness + ML training-cycle health + soak progress), DIAGNOSES bugs and PROPOSES fixes, raises flags when something has stalled or a gate is met, then synthesizes ONE consolidated, time-windowed system report (per-trade dossiers split real/paper/prop, PnL trend, market context, ML fleet) — a self-contained responsive HTML the apps surface in their Reports list, pinged once. Use when the operator says "/system-review", "/system-report", "run the system review/report", "give me the daily/weekly/monthly review", or "what has the system been doing / where do we stand". Takes --window=since-last|daily|weekly|monthly (default since-last). NOT a replacement for the three skills (it invokes them) and NOT a code review.
---

# /system-review — the master system-review session (deliverable: the system report)

> **⚠️ READ FIRST — WHAT THIS SESSION IS.** This is **FULL END-TO-END QA OF THE
> WHOLE SYSTEM**, NOT a report-generator and NOT a scan-and-sweep-under-the-rug
> exercise. Your job is to actively **HUNT** for issues across every layer (bugs,
> correctness gaps, money-at-risk conditions, silent regressions, stalled
> pipelines), **ROOT-CAUSE** them, **PROPOSE** the exact fix, decide the Tier-2/3
> calls **WITH the operator**, and then **FIX** them — this session. **Finding a
> fixable bug and logging it to a backlog as a post-it note instead of driving it
> to a fix is a REVIEW FAILURE** — that is exactly how bugs become operational
> catastrophes. The consolidated report is the *deliverable*, never the goal. You
> can ALWAYS weigh in with the operator — but raising the flags is YOUR job; never
> passively wait for the operator to point at the problem. This framing binds the
> three sub-reviews this session runs, too.

This is the **master review session**. The **review is the work**; the **report
is just the deliverable** it produces. It does not replace `/health-review`,
`/performance-review`, or `/ml-review` — it **runs all three** and then
synthesizes a single executive **system report** (per-trade dossiers, market
context, per-class PnL trend, ML fleet). One report, one Telegram ping, one HTML
link, surfaced in both apps' **Reports** list. (The on-disk / artifact / API
name stays "report" — `/api/bot/reports`, `comms/reports/`, the apps' Reports
tabs — only the *session* is the "review". `/system-report` is a back-compat
alias for this same session.)

If the operator asked for ONE domain only — just system health, just trading
performance, or just models — STOP, use that single skill instead. This skill
is the all-three roll-up.

Fully autonomous: pull live state yourself via the diag relays (skill:
`diag-data`); the operator pastes/downloads/SSHes nothing.

## This is a REVIEW (work), not a report (passive summary) — binding

A system review is a **work session**, same posture as the session-start
contract: **diagnose and fix / propose, don't just describe.** Summarizing
findings and moving on is the failure mode this skill exists to prevent. Every
run MUST actively:

1. **Grade every trade** since the last review (the `performance-review` scorer
   runs — see "Running the three reviews"; the grading-freshness guard is
   mandatory).
2. **Assess strategy promotion/demotion readiness** — for each strategy, where
   it stands vs its gate (KILL / DEMOTE_SHADOW / TUNE / HOLD / PROMOTE per the M7
   review packets + the shadow→advisory ladder). Surface what is *ready to
   promote* and what should be *demoted/killed*, with the evidence.
3. **Confirm the ML lifecycle is progressing** — training cycles actually ran
   since the last review, dataset builds succeeded, models are advancing, and
   every **soak is actually soaking** (accruing the volume/days it needs). If a
   training cycle is failing, a model is stuck, or a soak has stalled — or has
   already MET its promotion criteria and is just sitting there — **raise it as
   a flag**, don't let it pass silently.
4. **Find bugs and propose fixes** — a review that surfaces a pipeline/data/exit
   bug carries it to a *proposed fix* (Tier-1/2 fixed in a follow-up PR; Tier-3
   proposed to the operator with the exact change). Orphaned status, non-TP/SL/
   strategy closes, undelivered alerts, stalled soaks: bugs to drive, not notes
   to file.
5. **Raise flags loudly** — anything degrading (a strategy bleeding, a soak
   stalled, a gate met-but-unactioned, a training failure) goes in
   `operator_priorities` / `cross_review_notes` with `operator_action_required`
   set, not buried.
6. **Work the three review backlogs down — a HARD COMPLETION GATE, every
   open item, not a sample** — `docs/claude/{health,performance,ml}-review-backlog.json`
   are part of the job, not a tally. Each run, **triage EVERY open item in all
   three** (the sub-reviews each enforce their own 100%-triage gate — see their
   "Draining the backlog — a HARD COMPLETION GATE" sections; running them is how
   this step is done). For each: re-validate against the state you pulled, then
   *drain* — fix Tier-1 items in-place / Tier-2 in a follow-up PR and mark
   `resolved` with a full-timestamp `resolved_at`; carry Tier-3 items to the
   operator as an exact proposed change. An item may stay `kept_open` ONLY if it
   is genuinely soaking, blocked on future data, or a Tier-3 awaiting the
   operator — and then it MUST carry an update noting this run's re-validation +
   the blocker. **Triaging "the recent few" or the items this session touched is
   a review FAILURE** — the backlog IS the standing open-task list, so reporting
   a review "done" while open items sit unlooked-at is the exact lazy-incompetence
   failure this gate exists to stop. A report whose `backlog_summary` shows many
   open and ~zero triaged is that tell. Counting the backlog
   (the `backlog_counts.py` roll-up) is NOT the same as working it, and each
   domain's `count_untriaged` MUST be 0.

Producing the report is NOT the finish line — the review's findings being
*driven* (fixed, or put in front of the operator as an exact decision) is, and
the backlogs being *worked down* is. The **Review-coverage guard** below fails a
run that skipped the promotion / training / soak assessment or that shows no
backlog drive.

## THE CHECKLIST — render it on EVERY status update (operator-directed 2026-08-31)

**The review is DONE only when every item in the checklist is ticked.** Not when
the report renders, not when the session runs long, not when a session judges in
prose that it has covered enough.

    python scripts/ops/system_review_checklist.py            # the chart
    python scripts/ops/system_review_checklist.py --check    # non-zero if incomplete

Operator directive: *"every time that I ask for a status update, the session
knows to give me the chart with the items that are in the review mandate, a
checklist of what was actually done versus not done or is still in work. And
another row for notes ... we need clear log keeping so that I can also
understand what the state is."*

So: **any time the operator asks where things stand, render that chart in the
reply.** Do not summarise it in prose instead — prose is what let a review report
completion with a third of its mandate untouched.

- State lives in `docs/claude/system-review-checklist.json` (committed, so the
  state survives a session and a fresh session inherits it rather than starting
  a new private tally).
- **The item list is DERIVED**, not typed: it reads
  `render_system_report.py::_REQUIRED_COVERAGE_KEYS` — the tuple CI actually
  enforces — plus the three sub-reviews, the report and the ping. A typed list
  drifts, and it already had: the prose below says "TEN required keys" while
  that tuple holds **13**. Field beats comment.
- **Five statuses, never collapsed:** `not_started` (nobody looked) ·
  `in_progress` · `blocked` (and on what) · `done` · `n_a`. "Not started" and
  "blocked" are different facts and a chart that conflates them is useless.
- **`done` REQUIRES evidence and `n_a` REQUIRES a reason** — both are refused
  without one, because an unevidenced tick is precisely what this exists to stop.
- Update the row the moment an item completes, not at the end; a session that
  compacts mid-run must not lose what it already did.

## Review-coverage guard (mandatory — 2026-06-23)

Before rendering, the consolidated payload MUST carry a populated
`consolidated.review_coverage` object proving the review actually covered its
mandate (not just the trade/health summary). Same enforcement pattern as the
grading-freshness guard. Required, non-empty:

- `review_coverage.strategy_promotion` — per-strategy promotion/demotion stance
  (ready-to-promote, demote/kill candidates, or "all HOLD") with evidence; pulled
  from the `ml`/`performance` sub-reviews + `/api/bot/strategies/{name}/review`.
  **A stance is not enough — this must PUSH.** For every candidate whose gate is
  MET, the review either dispatches the next step itself (where the tier allows)
  or puts the exact decision in front of the operator with the evidence attached.
  **A gate that has been MET across two or more consecutive reviews without a
  decision is a MANDATORY `flags_raised[]` entry** — the same
  anti-normalization rule `execution_capture` carries. "Ready to promote" that
  stays "ready to promote" for a month is the soak equivalent of an alarm
  everyone walks past.
- `review_coverage.ml_training_health` — did training cycles run since the last
  review? dataset builds OK? any failing/stuck cycle? **any
  `manifest_quarantine_tripped` / `manifest_quarantined` cycle event** (a
  single-manifest OOM the trainer escalated — BL-20260717-TRAINER-SINGLE-MANIFEST-OOM,
  requires a Rule-3 shrink/GPU/drop disposition — see the `/ml-review` rubric)?
  (from `/ml-review` + trainer relay).
- `review_coverage.soak_status` — each active soak (shadow models, conviction,
  exit-ladder) and whether it is accruing as expected, stalled, or has met its
  gate; flags for any stall / met-but-unactioned.
- `review_coverage.authored_cells` — **mandatory on the weekly window**
  (2026-07-31, P0.4). Proof the review checked the standing
  [`docs/audits/authored-cell-reaudit-register.md`](../../../docs/audits/authored-cell-reaudit-register.md)
  against `config/regime_policy.yaml`: (a) every authored cell has a register
  row (a cell added since the last review with no row is a FAIL), (b) no
  live-affecting cell is past its `Next due` without a recorded verdict, and
  (c) any due cell gets its re-audit dispatched this run (or a named blocker
  cited from the register). A decision is not permanent evidence — this is the
  guard against a cell authored on a since-invalidated measurement staying
  authored indefinitely (the gld_pullback_1h ~25× fee over-charge shape,
  `BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER`). Cell EDITS remain Tier-3 —
  this block only proves the evidence age was checked.
- `review_coverage.execution_capture` — **mandatory** (2026-07-30). Proof the
  review MEASURED how much of each live strategy's edge actually reached the
  account (not just graded the decision). See § "Execution-capture review". This
  is the guard against the failure that motivated it: the `BYBIT_TPSL_MODE=full`
  shared-bracket bug made 5m scalps ride 6–14h and round-trip their MFE back to
  the stop for **weeks**, unnoticed by successive reviews, because no review was
  measuring capture. Any anomaly open across **≥2 reviews** (`reviews_open>=2`)
  is a MANDATORY `flags_raised[]` + `operator_priorities` escalation.
- `review_coverage.since_last_build_verification` — **mandatory** (2026-08-20,
  operator directive). **Enumerate every capability that shipped since the
  previous review and give each one a VERDICT**: `running` ·
  `wired_not_yet_exercised` · **`UNWIRED`** · `unverifiable` (+ a `reason`).
  `count_shipped` must equal the number of items listed — a partial enumeration
  is the failure this key exists to catch. **Any `UNWIRED` item MUST appear in
  `flags_raised[]`.**

  Why: *"we don't keep building things out half way and then leaving them to
  rust."* Measured 2026-08-20 — **161 of 384 tools under `scripts/` have
  nothing that runs them** (12 referenced nowhere at all, 149 referenced only
  by docs), and `scripts/ops/trainer_dataset_gc.py` — the retention tool for a
  12 G dataset tree — sat unrun with **0 mentions across 7,442 cycle-log rows**
  while the disk it was written for climbed to **93 %**. Every instance of this
  class in the record was found *by accident, months later*, never by a review.
  Run **`scripts/ci/check_unwired_artifacts.py`** and read its diff against the
  previous run; a newly-appearing entry is a shipped-and-unwired capability.

- `review_coverage.backlog_classes` — **mandatory** (2026-08-20, operator
  directive). **Read the WHOLE open backlog for PATTERNS before disposing of
  any individual row.** Carries `total_open_reviewed` (the full open count, not
  a sample) and `classes[]`, each with `class`, `member_ids` (**≥ 2** — one row
  is an instance, not a class) and `structural_fix` (what retires the whole
  class).

  Why: draining one row at a time is a treadmill — the same defect returns
  under a new id. `order_packages.id`, the fictional column behind
  `BL-20260810`, was still declared in **20 test fixtures** after the fix swept
  only the reporting instance. **Do this pass FIRST**, before `backlog_drive`:
  the classes decide which rows are worth disposing of individually and which
  are symptoms of one fix.

- `review_coverage.structural_health` — **mandatory** (2026-08-24, operator
  directive). **`backlog_classes` finds patterns in the BACKLOG. This finds
  them in the RUNNING SYSTEM — where the biggest defects have no backlog row
  at all**, because they are visible only as a distribution over live data.

  Operator, 2026-08-24: *"if we see that trades aren't closing properly, or
  that there are bugs that are not really resolving themselves over time
  because we're just putting on band-aids and we need a bigger structural fix
  — those are also things you should be looking for and suggesting here."*

  Carries `population`, `findings[]` (each with `finding`, `measured`,
  `trend`, `structural_fix`) and `hypothesis_tested`. Three binding rules:

  1. **The population is the WHOLE HISTORY, not the window.** A structural
     trend is invisible in a three-day slice. The window is precisely what let
     successive reviews report execution-capture as a flat metric.
  2. **Every finding carries a `trend`** (`falling` / `flat` / `rising` /
     `first_measurement`) against prior reviews. *Is this class shrinking?* is
     the whole question — a count that is flat across reviews means the fixes
     are not touching the cause, which IS the complaint.
  3. **State ONE falsifiable hypothesis and test it.** Report the verdict even
     — especially — when it is `refuted`. A structural review that only
     confirms what it already believed has tested nothing; this is RULE ONE
     ("verify your own output too, hardest when it confirms what you
     expected") applied to the review itself.

  Why it exists. Measured the day it was added, over **all 1,324 closed
  non-backtest trades**: **64.7% of closes come from cleanup machinery** and
  35.3% from a decision, and the M20 exit levers — the entire point of the
  exit-refinement program — had fired **17 times ever (1.3%)**. Separately,
  the strategy-**decided** exit path is the *unmeasured* one (27.0% measured
  coverage vs the janitor path's 52.0%; 41.8% of decided closes carry no
  provenance stamp at all), and the pairs sleeve stamped **79 of 79** closes
  with nothing. **None of those facts was a backlog row.** Eight consecutive
  reviews reported the same execution-capture percentage as a metric without
  once asking what it was a symptom OF. The rule-3 hypothesis on that run was
  REFUTED — the review predicted the provenance gap was downstream of janitor
  closes and the measurement said the opposite — and that refutation was the
  most valuable thing the pass produced.

  Do this pass **after** `backlog_classes` (the classes are an input to it)
  and **before** writing `operator_priorities` — the structural findings are
  what those priorities should be ranked against.

- `review_coverage.ml_output_actionability` — **mandatory** (2026-08-20,
  operator directive). *"Just checking that the trainer VM is green isn't
  enough — we need to verify that the training sessions and backlogs are
  actually being worked through and producing reliable and actionable results,
  every day."* `ml_training_health` answers *did it run*; this answers **did
  what it produced get used**. Carries `cycles_in_window`,
  `outputs_consumed_by` (who actually reads the output — a named consumer, not
  "the registry"), and `verdict` ∈ `actionable` · `producing_but_unused` ·
  `not_producing` · `unverifiable`. The last two **must** reach
  `flags_raised[]`.

  Measured 2026-08-20 for calibration: 7,442 cycle rows over 149 cycles, with
  `manifest_audit_flagged` 406 and `manifest_untrained_stale` 290 — **~15 %
  non-ok manifest events** — while `outcome` across the whole log totalled
  `{trained: 20, already_complete: 20}`. A green service tells you none of that.

- `review_coverage.unexercised_fixes` — **mandatory** (2026-08-24, operator
  directive: it *"should be a live item that each system review needs to report
  on and check thoroughly until we see it work correctly"*). **A fix that is
  DEPLOYED and a fix that WORKS are indistinguishable from every surface we
  have** — the code is on `main`, the deploy sha matches, the tests pass, and
  none of that shows the mechanism ever ran. Only the mechanism firing on a real
  trade settles it.

  One row per fix still awaiting first exercise, each carrying `fix`,
  `deployed_sha`, `verdict` ∈ `exercised` · `still_unexercised` · `regressed` ·
  `unverifiable`, and — **required when `exercised`** — `evidence` naming the
  trade or event in which the mechanism demonstrably acted. Anything other than
  `exercised` **must** reach `flags_raised[]`. A row leaves this block only on
  `exercised` **with** evidence; it is not drained by time passing.

  ⚠️ **Do not accept a proxy for the mechanism.** MGC 4773 (2026-08-23) closed
  with BOTH bracket legs resting and the take-profit **233.9 points in the
  money**, and still exited via the monitor's `tp_cross` — the attached target
  never acted. The order book looked like proof and was not. Check the TRADE:
  which mechanism closed it, and should the fixed one have fired?

  Open at the time of writing: **#10174's IB transmit fix** and the **durable
  target-naked cooldown**, both shipped 2026-08-23, both unexercised. Two in one
  day is what makes this a class rather than a row — see
  `BL-20260823-IB-TRAILING-A-STOP-SILENTLY-DROPPED-THE-TARGET`.

- `review_coverage.research_results_disposition` — **mandatory** (2026-08-30,
  operator directive: *"we need some mechanism for making sure results are read
  and logged where they need to be for influencing decision making"*). The
  RESEARCH sibling of `ml_output_actionability`: that key asks whether ML output
  gets used, and nothing asked it of backtests, sweeps and corpora.

  **Why it exists.** R1–R6 of `docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md`
  stop at *landed* — R2 makes landing part of the run and no stage covers *read*.
  Measured 2026-08-30, `grep -c 'corpus\|research/queue'` across the
  system-review / health-review / performance-review / ml-review /
  research-driver SKILL.md files returns **0 for all five**: the research
  pipeline was invisible to every review we run.

  ⚠️ **This is NOT what `provenance-consumer-guard` asks.** Eight scripts read
  these corpora, so a consumer EXISTS. This asks the question one level over:
  **was a consumer RUN, on THIS batch, and did a decision come out of it.**

  Run `python3 scripts/research/research_disposition.py --report`. Carries
  `units_landed_since_last`, `dispositioned`, `unread`, `superseded_unread`, and
  `verdict` ∈ `actionable` · `landed_but_unread` · `none_landed` ·
  `unverifiable`. The last two **must** reach `flags_raised[]`.

  ⚠️ **`superseded_unread` is NOT a finding and must not be counted as one** —
  196 of 288 historical units are superseded by construction, and reporting 288
  failures on day one is the desensitized-alarm P1. ⚠️ **`unverifiable` means a
  store could not be READ**, which is not *nothing unread*.

  **A review DISPOSITIONS; it does not merely read** — the same rule
  `backlog_drive` had to be hardened with. Writing a disposition through
  `research_disposition.append` REFUSES a vacuous reason, using the same
  `_NON_REASONS` vocabulary, so "carried forward unchanged" cannot satisfy this
  key. Measured at introduction: **92 unread / 196 superseded / 0 dispositioned**
  — every sweep the fleet has ever run landed and was dispositioned by nothing.

- `review_coverage.test_execution_verification` — **mandatory** (2026-08-30,
  operator directive: *"verify the mechanisms and the tests run since the
  previous review"*). `since_last_build_verification` is the MECHANISMS half;
  this is the TESTS half.

  ⚠️ **It is NOT "which test files never run", and a session that goes looking
  for that will find nothing and report a false all-clear.** Measured: CI runs
  `pytest -q tests/` **wholesale** over all 824 files, so an unrun test file is
  not the gap. The real gap is one level over — `pytest-run` is **PATH-FILTERED**,
  and that workflow's own comments record **FOUR** separate incidents of a
  *"9-SECOND green pytest-run"* merging and leaving `main` **red**. A green check
  is not evidence the suite ran.

  Carries `runs_in_window`, `suite_executed` (runs that actually executed the
  suite, not a filtered short-circuit), `guards_executed`, and `verdict` ∈
  `executed` · `short_circuited` · `not_run` · `unverifiable`. Anything other
  than `executed` **must** reach `flags_raised[]`.
  `docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence".

- `review_coverage.flags_raised[]` — the loud flags this review surfaced (may be
  empty only if genuinely nothing is degrading — state that explicitly).
- `review_coverage.account_reachability` — **mandatory** per-account up/down for
  every declared-live broker account (the "all declared-live, non-shelved" set:
  `mode: live` + a probeable exchange, excluding the dry/shelved `ib_live` /
  `oanda_practice` and the API-less `breakout_1`). Pull it from
  `/api/diag/exchange_positions` (positions=null ⇒ unreachable), the latch state
  (`runtime_logs/account_reachability_alert_state.json` via
  `account_reachability_alert.down_accounts()`), and `/api/bot/accounts/balances`
  (`api_ok`). **Any down live account is a MANDATORY `flags_raised[]` entry that
  fires its OWN standalone high-priority ping — it must NOT be buried only in the
  report body.** This is the explicit guard against the failure that motivated it:
  the IB gateway was dark across reviews and went unflagged.
- `review_coverage.backlog_drive` — proof the three backlogs were *worked*, not
  just counted: per domain, what you `drained` this run (the item ids you
  resolved) and `deferred` (ids left open + the reason each is legitimately not
  actionable now: soaking / future-data / Tier-3-awaiting-operator). **Per
  domain it MUST also carry `{open_at_start, triaged, count_untriaged}`, and
  `count_untriaged` MUST be 0 with `triaged == open_at_start`** — the completion
  gate mirroring the sub-reviews. If you drained nothing, this must say why every
  open item is non-actionable — "no time" / "didn't look" / triaging only "the
  recent few" is a review FAILURE, not a valid reason.

**STOP and complete the assessment if any of the THIRTEEN required keys
(`strategy_promotion`, `ml_training_health`, `soak_status`, `execution_capture`,
`backlog_drive`, `account_reachability`, `since_last_build_verification`,
`backlog_classes`, `ml_output_actionability`, `structural_health`,
`unexercised_fixes`, `research_results_disposition`,
`test_execution_verification`) is missing or empty, OR if any domain's
`backlog_drive.count_untriaged > 0`**

⚠️ **This list said TEN and omitted `structural_health` until 2026-08-30**,
while `_REQUIRED_COVERAGE_KEYS` had enforced it since 2026-08-24 — the same
declared-vs-enforced drift, in the harmless direction this time (enforced but
undeclared, rather than the `account_reachability` case of declared but
unenforced). Both are bugs: the SKILL is what a session reads, the tuple is what
CI checks, and a reader cannot see the gap from either side alone. **The count
is stated in words deliberately, so adding a key without updating it reads as
an obvious contradiction rather than an off-by-one nobody notices.**

⚠️ **`account_reachability` was declared mandatory here on 2026-06-29 and
enforced by NOTHING until 2026-08-20** — this text said "six required keys" and
named it, while `render_system_report.py::_REQUIRED_COVERAGE_KEYS` held five and
omitted it. Its stated motivation is *"the IB gateway was dark across reviews
and went unflagged"*, and on 2026-08-20 the full-system audit measured the
gateway restarting **three times in 33 minutes** (only one scheduled) with
nothing flagging it. All THIRTEEN are now in that tuple with real validators. **If
you add a fourteenth key here, add it there in the same commit** — a declared-but-
unenforced key is worse than no key, because the skill reads as if it is
covered. — a review that can't show its
promotion/training/soak coverage, its *execution-capture* measurement, its
*full* backlog drive (every open item triaged, not a sample), *or its
per-account reachability* has not actually run, regardless of how complete the
trade/health summary looks. (Relay-blocked data is allowed only as an explicit
`"unavailable: <reason>"` string — never silently omitted.)

The renderer surfaces this mechanically: `render_system_report.py --strict`
exits non-zero when a required `review_coverage` key is missing/empty or an
`execution_capture` anomaly with `reviews_open>=2` is not escalated into
`flags_raised[]`. Run the render step with `--strict` (see § "Render & deliver")
so a skipped assessment can't quietly ship.

## Coordination (binding — check before the first diag pull)

This skill fans out real work (three sub-reviews, each dispatching live-VM and
trainer-VM diag/system-action requests and committing backlog drains) — often
via background `Agent` sub-agents that have no session identity of their own.
Before your first substantive tool call: read + post to the **live
coordination board** (GitHub issue #6927) per `docs/claude/coordination-board.md`
+ `docs/CLAUDE-RULES-CANONICAL.md` § "Multi-session coordination" step 0 — a
single `▶️ START` naming that this is a `/system-review` covering all three
sub-reviews' scope, **before** launching any sub-agents. They cannot post for
themselves. (Skipping this is exactly how a 2026-07-22 run of this skill
collided, unnoticed, with a concurrent session's trainer-VM work.)

## Scope (what this skill DOES)

1. **Establish the window** (§ "The window").
2. **Run the three reviews in report mode** — gather each review's full analysis
   and capture its response JSON, but **suppress each one's individual Telegram
   ping** (this skill sends one consolidated ping instead) (§ "Running the three
   reviews").
3. **Gather report-specific data** the reviews don't produce — per-trade
   dossiers, market context, per-class PnL + trend (§ "Report-specific data").
4. **Assemble the consolidated JSON** conforming to
   `comms/schema/system_report_response.template.json` (§ "Assemble").
5. **Render + write artifacts** via `scripts/reports/render_system_report.py`
   (§ "Render & deliver").
6. **Send ONE consolidated ping** with the report link (§ "Render & deliver").

The format is canonical in [`docs/reports/system-report-DESIGN.md`](../../docs/reports/system-report-DESIGN.md) —
read it; this file is the operating procedure.

## Out of scope (DO NOT do here)

- **Re-grading / re-deriving AT THE SYNTHESIS LAYER** — once a sub-review has
  produced its grades/analysis, take its JSON verbatim into the
  `health`/`performance`/`ml` sub-objects; don't second-guess or recompute them
  here. This is **NOT** a licence to skip the grading itself: the
  `performance-review` sub-review still MUST run its order-package grading scorer
  first (see "Running the three reviews"). "Don't re-grade" means "don't grade
  twice", **not** "don't grade".
- **Touching `src/`, `config/`, or any live-path file.** Reports don't trade.
- **Owning a *new* backlog.** This skill creates no backlog of its own — but it
  is NOT exempt from draining: it MUST actively work the three sub-review
  backlogs down (mandatory action 6) and record the drive in
  `review_coverage.backlog_drive`. "Surface the roll-up counts" is the floor, not
  the job.
- **Scheduling.** v1 is on-demand. Automatic daily/weekly/monthly is a documented
  phase-2 (a cron-triggered session) — don't try to wire a timer here.

## The window

`--window=since-last|daily|weekly|monthly` (default `since-last`):

| Window | `window_start` |
|---|---|
| `since-last` | the previous report's `reviewed_at` from `comms/reports/index.json` (newest entry, any window class); first-ever run → last 6h. |
| `daily` | `now − 24h` |
| `weekly` | `now − 7d` |
| `monthly` | `now − 30d` |

`window_end` = now. Record `prior_report_id` (the index entry you derived
`since-last` from, or the newest prior report of the same window class). The
prior-window comparison for the trend uses the immediately-preceding equal-length
window — pull `/api/bot/performance` for both the current and prior window where
the endpoint supports it, else compute from `/api/pnl/history`.

## Running the three reviews

Execute each sub-review per its own SKILL.md, against the **live** diag relays,
covering the report window:

- `.claude/skills/health-review/SKILL.md`
- `.claude/skills/performance-review/SKILL.md`
- `.claude/skills/ml-review/SKILL.md`

Capture each one's full response JSON into the `health`, `performance`, and `ml`
sub-objects of the consolidated payload **verbatim** — same shapes as
`comms/schema/{health,performance,ml}_review_response.template.json`.

**Ping suppression (important):** each sub-review normally ends with its own
`send-ping`. When run under `/system-report`, **do not fire the three individual
pings** — set each sub-object's `claude_channel_ping.delivered_via` to
`"suppressed (system-report)"`. This skill fires exactly one consolidated ping.

**Report mode suppresses ONLY the ping — it is NOT read-only mode.** Every other
thing a sub-review does, it STILL does, including its repo-local writes:
- the **`performance-review` MUST run its order-package grading step**
  (`scripts/ops/score_order_packages.py` over the live journal → append the new
  rows to `comms/claude_strategy_scores.jsonl`) **before** the consolidated
  report reads any `claudeScore`; and
- all three **drain their own backlogs**.

**GRADING IS MANDATORY — NO REVIEW IS COMPLETE WITHOUT A FRESH CLAUDE SCORE FOR
EVERY CLOSED TRADE IN THE WINDOW** (operator directive 2026-06-29). The grades
live in `comms/claude_strategy_scores.jsonl` (a repo file the API joins
last-wins), NOT the live DB — so "I can't reach the DB" is **never** an excuse to
skip grading. Two paths, by session type:
- **DB-bearing session (VM / desktop CLI):** run the canonical
  `scripts/ops/score_order_packages.py <trade_journal.db>` — it rewrites the full
  JSONL from the live `order_packages`.
- **Web / PM session (no DB file) — PRIMARY path: the `grade-closed-trades`
  system-action** (added 2026-07-06, see `docs/claude/system-actions.md`).
  Dispatch it (Tier-1, autonomous) via a labelled `system-action` issue with
  `action: grade-closed-trades` (+ optional `since:`/`limit:`/`include_open:`).
  It runs the SAME `_grade_package` rubric on the VM (where the DB already
  lives) and returns **only the ungraded delta** as NDJSON in the issue-comment
  reply — a bounded, small payload, unlike pulling the whole `trades` table
  through the diag relay (a full table runs ~650KB against the relay's ~55KB
  comment budget, which repeatedly truncated/failed full-window grading before
  this fix). Append the returned NDJSON rows to
  `comms/claude_strategy_scores.jsonl` and commit. The response never
  truncates silently — an oversized delta ends with a trailing
  `{"_delta_summary": ..., "truncated": true, "more_available": N}` line; raise
  `limit:` or re-dispatch if you see one.
- **Web / PM session — documented FALLBACK** (only if `system-actions.yml`
  itself is unavailable): pull the window's closed trades via
  `GET /api/diag/journal?table=trades` and run
  **`scripts/ops/grade_closed_trades_from_diag.py <trades.json> --since <window_start>`**
  — it APPENDS one grade per closed trade using the SAME `_grade_package` rubric
  (imported, not re-implemented), and last-occurrence-wins means it supersedes any
  stale open-status grade. (Prop rows are isolated — not in `trades`, not graded
  here.) Then **commit `comms/claude_strategy_scores.jsonl`.** This path is the
  one that previously hit the diag relay's comment-size wall for anything beyond
  a tiny window — prefer `grade-closed-trades` above.
  *(The 2026-06-29 incident this fixes: a web-session review skipped grading
  believing it needed live-DB write, shipping a report whose closed trades read
  ungraded. The diag grader removed that excuse; the system-action above removes
  the size-limit wall the diag grader then ran into.)*

Record the roll-up in `consolidated.backlog_summary` — **computed, never
hand-entered.** Run:
```
python3 scripts/reports/backlog_counts.py --since <window_start>
```
and copy its `{total, open, resolved, drained}` per domain straight into
`backlog_summary`. **Backlog-count regression guard (2026-06-23):** a
hand-assembled summary put the *total* in `health.open` ("132" when real open was
73) and left `performance`/`ml.open` null → "— open" (real opens 28 / 16) — even
though every count is exact from the backlog files. The open/total counts are
always computable; if `backlog_summary` carries a null `open`, or an `open` that
equals `total` for a domain whose file has resolved items, you guessed instead of
running the counter — STOP and run it. (`drained` is precise only when
`resolved_at` is a full ISO timestamp; a date-only `resolved_at` degrades it to
day granularity — write full timestamps when you resolve an item.)

**Grading regression guard (2026-06-23):** treating report mode as read-only
silently dropped grading for a week — the 06-22 and 06-23 system-reports
synthesized per-trade dossiers from grades last refreshed 06-18, so the dashboard
"Claude-graded" count read 0 on every recent package. Grading is a mandatory
write-side step of every system-report, not an optional refresh.

If a relay is unreachable even after a `vm-web-api-recover` retry, emit the
partial report (the failed domain's sub-object carries its own degraded grade)
with `overall_assessment` reflecting the gap — never fabricate findings.

## Report-specific data

Beyond the three reviews, gather (skill: `diag-data`; reuse the REST endpoints —
do not recompute what an endpoint already returns):

- **Per-class PnL + trend** — `/api/bot/performance?window=…` (real + its `paper`
  sub-block) and `/api/pnl/history` for the current AND prior equal-length
  window. Prop from `/api/bot/prop/{status,fills,reconcile}` (isolated journal,
  never `trades`). Fill `consolidated.pnl_by_class.{real,paper,prop}` incl.
  `trend` ∈ up/down/flat and `prior_window_pnl`. **Dollars, reconciled (binding
  — see § "Dollars are the scoreboard"):** the real-money `window_pnl` you report
  MUST be the DOLLAR figure reconciled against `/api/bot/pnl/exchange` (FIFO
  exchange-fills wallet truth) and `/api/bot/pnl/broker-truth` (lifetime
  wallet-truth for accounts the journal under-records, e.g. `bybit_2`) — NOT a
  journal-R sum. If real money is down, say down. Set
  `review_coverage.execution_capture.dollars_reconciled = true` only after this
  reconciliation actually ran.
- **Execution capture** — see § "Execution-capture review". Fill
  `review_coverage.execution_capture` (per-strategy roundtrippers% / giveback /
  hold-vs-expected + aged `anomalies[]`).
- **Trade dossiers** — `/api/bot/trades/closed?since=<window_start>&include_paper=true`
  joined to `/api/bot/order-packages` (by `linkedTradeId`) for `signalLogic` +
  `meta` + `modelScores`, and to the performance review's A–F grade
  (`claudeScore` on order-packages / `comms/claude_strategy_scores.jsonl` by
  `order_package_id`). **Grading-freshness guard (mandatory):** before consuming
  any `claudeScore`, confirm the grading actually ran THIS session — the newest
  `reviewed_at` in `comms/claude_strategy_scores.jsonl` must fall at/after
  `window_start`, and every closed package in the window must now carry a grade.
  If the newest grade predates the window, the performance-review's grading step
  was skipped → STOP and run the scorer before synthesizing; otherwise the report
  (and the dashboard's "Claude-graded" count) reflects stale grades.
  **Adaptive depth:** for `since-last`/`daily` build a full
  dossier for every trade; for `weekly`/`monthly` mark only outliers
  `notable=true` (biggest win/loss, worst grade, any prop rule-distance event)
  and rely on `pnl_by_class.per_strategy` for the rest. Record the resolution in
  `dossier_coverage`.
- **Market context** — enumerate traded symbols live (`/api/bot/strategies` +
  `/api/bot/config` account/strategy `symbols` ∪ open-position symbols, never
  hardcoded). For each, pull `/api/bot/candles` over the window and fill
  open/close/high/low + `pct_change` + a one-line regime `note`. Null (not 0) on
  a candle-fetch failure.

Render any null as em-dash downstream — never `0`/"unknown".

## Execution-capture review (mandatory — 2026-07-30)

**Grading a decision is not the same as measuring whether its edge reached the
account.** A trade can be an A-grade decision and still bleed because the
*execution* gave the edge back — the exact failure that soaked for weeks: the
`BYBIT_TPSL_MODE=full` shared-bracket bug made 5-minute scalps hold 6–14 hours
and round-trip their MFE back to the stop, while every review graded the entries
and moved on. This section makes capture a **first-class, measured** output so
that class of bug screams on the *first* review, not the tenth.

**Measure it (don't eyeball it):** run
`scripts/research/m20_exit_analysis.py --since-days <window>` on the **trainer
VM** (the box with the `datasets-out/market_raw` candle store — via the
`trainer-vm-diag` relay; the script is stdlib-only so it runs on the trainer's
plain `python3`). Real / paper / prop are **never blended**; reconciler /
superseded / adopted-orphan artifact rows are excluded. Per live strategy that
closed ≥1 trade in the window, record into
`review_coverage.execution_capture.per_strategy[]`:

- **`roundtrippers_pct`** — % of trades that went **≥1.0R favorable** (MFE) then
  **closed negative** (the literal "near-TP then snap to SL" trade).
- **`mean_giveback_r`** — mean `MFE − realized_R` (value reached but not kept).
- **`hold_h_actual` vs `hold_h_expected`** — expected hold from the strategy's
  timeframe (a 5m scalp's clean hold is minutes, a 2h pullback's is hours). **A
  leg whose actual hold is an order of magnitude over expected is the execution
  smoking gun** — it means the per-trade bracket isn't firing and the position
  sits open until a reconciler/time event closes it.
- **`state`** ∈ `ok` | `degraded` | `anomaly`.

**Flag + AGE anomalies (the normalization killer).** Any strategy reading
`anomaly` (hold ≫ expected, roundtrippers spiking vs its own history, giveback
dominating realized) goes into `execution_capture.anomalies[]` **with an age**:

- `first_seen` — the UTC date it was first flagged (carry forward from the prior
  report's `execution_capture` — read `comms/reports/index.json` → the prior
  report JSON; do NOT reset it to today).
- `reviews_open` — consecutive reviews it has been open (prior value + 1).
- `backlog_id` — the health/performance-review backlog item tracking it (file
  one if none exists — this is the "if you see something, say something" duty).

**`reviews_open >= 2` is a MANDATORY standalone escalation** — a
`flags_raised[]` entry AND an `operator_priorities` row, exactly like a down
live account. An execution defect that survives two reviews is by definition
being walked past; the age forces it loud instead of letting it soak. (This is
the mechanism that would have caught the bracket bug in days, not weeks.)

## Dollars are the scoreboard (honesty — binding, 2026-07-30)

**Report real-money P&L in DOLLARS, reconciled against broker truth. R is a
diagnostic, never the headline.** R is risk-normalized and excludes fees +
funding, and the `bybit_2` journal under-records (netting + spot/perp +
sub-account — `BL-20260713`), so a positive R-sum can sit on top of a losing
dollar account (a phantom journal row once inflated a review to "+8.9R,
positive" while the wallet was **down**). Non-negotiable:

1. The real-money figure in `pnl_by_class.real` and the headline is the **dollar**
   value from `/api/bot/pnl/exchange` (FIFO exchange-fills) + `/api/bot/pnl/broker-truth`
   (lifetime wallet-truth) reconciled against the journal — **not** a journal-R sum.
2. **If real money is down, the report says down.** No rounding a loss up to R,
   no "net positive" that is really paper.
3. **Real / paper / prop are never blended** (already the contract) — and the
   `*_portfolio` **paper mirror** (`bybit_portfolio` / `alpaca_portfolio`) is the
   honest net-of-everything predictor: surface it, and when the paper mirror and
   real money **disagree** (paper green, live red, or vice-versa), that *gap* is a
   finding — note it in `cross_review_notes[]` (the "translation gap").
4. **Field beats comment for account routing.** Before you call a strategy
   real-money or demo (which sets the stakes of any promote/demote), verify its
   ACTUAL routing from `config/accounts.yaml` (`/api/bot/config` account
   `strategies` lists), not a strategy-config comment — a stale "demo-only soak"
   comment on a leg that `accounts.yaml` routes to `bybit_2` (real money) is
   exactly how a real-money change gets mis-scoped.

## Assemble

Build the consolidated object per
`comms/schema/system_report_response.template.json`:

- `report_id` = `RPT-<UTCYYYYMMDD>-<HHMMSS>-<window>`, `reviewed_at` = now,
  `reviewer` = `claude`, `window`/`window_start`/`window_end`/`prior_report_id`.
- `overall_assessment` and `consolidated.roll_up_grade` = **worst-of** the three
  sub-reviews' `overall_assessment` (`investigate` > `caution` > `healthy`).
- `consolidated.headline` — one paragraph: what happened since the last report.
- `consolidated.operator_priorities[]` — top 3–5 actions distilled across all
  three (highest-severity first; carry each item's `tier` +
  `operator_action_required`).
- `consolidated.cross_review_notes[]` — patterns spanning domains (e.g. a health
  signal→order plumbing flag AND a performance rejection cluster on the same
  symbol).
- `consolidated.tier3_proposals_pending[]` — the Tier-3 items the sub-reviews
  proposed (never enacted), surfaced in one place.
- `consolidated.monitoring[]` — the **Monitoring** section (2026-06-25): the
  backlog items the review is actively *watching* rather than acting on — things
  that need more time (`soaking` / `awaiting-data`) or a decision
  (`awaiting-decision` — a gate is met or it's operator-gated) or a recurring
  `verify`. Curate from the three backlogs' open items whose deferral reason is
  soak/data/decision (NOT stale-doc / code-fix items — those are *actionable*, so
  they belong in `operator_priorities` or a follow-up PR, not here). Each row:
  `{item_id, domain, category, detail, since, next_check}` where `next_check` is
  the concrete trigger that ends the wait (e.g. `n>=30 closed`, `next IB reset`,
  `operator go`). This is the human-readable "what are we waiting on" companion to
  `review_coverage.backlog_drive.deferred` (which is the audit trail).
- `consolidated.review_coverage` — **required** (the Review-coverage guard): the
  `strategy_promotion`, `ml_training_health`, `soak_status`, `execution_capture`,
  `flags_raised[]`, and `backlog_drive` (what was drained vs deferred + why) the
  review produced. A run with any of the required keys (`strategy_promotion`,
  `ml_training_health`, `soak_status`, `execution_capture`, `backlog_drive`)
  missing/empty must STOP and complete the work before rendering.

## Render & deliver

1. Write the consolidated JSON to a temp file, then run (with **`--strict`** — the
   mechanical coverage backstop):
   ```
   python3 scripts/reports/render_system_report.py <consolidated.json> --out-dir comms/reports --strict
   ```
   It writes `comms/reports/<window>/<UTC-ts>/{report.json,report.html,report.md}`,
   updates `comms/reports/index.json` (newest-first), and prints the HTML path.
   **`--strict` exits non-zero (and writes nothing) if a required `review_coverage`
   key is missing/empty or an `execution_capture` anomaly with `reviews_open>=2`
   is not escalated into `flags_raised[]`** — fix the payload and re-run rather
   than shipping an incomplete review. **Commit** the new `comms/reports/**` files
   (so the GitHub link is live and the VM's `ict-git-sync` mirrors them for
   `/api/bot/reports`).
2. Set `artifacts.{json_path,html_path,md_path}`, `artifacts.github_link`
   (`https://github.com/benbaichmankass/ict-trading-bot/blob/main/<html_path>`),
   and **`artifacts.dashboard_link`** — the Reports deep link into the **new
   dashboard SPA** (GitHub Pages):
   `https://benbaichmankass.github.io/ict-trader-dashboard/?report=<report_id>`
   (the SPA reads the `?report=` query param on load and opens that report on
   the Reports page; the canonical dashboard base URL is recorded in `CLAUDE.md`
   § "Dashboard consumer"). The legacy Streamlit deep link
   (`https://ict-trader-dashboard-z67ryan2ttrxjdvk6ozcjc.streamlit.app/?report=<report_id>`)
   uses the same `?report=` scheme and still resolves while that app runs, but
   the SPA is now the primary target. Set on the payload (re-render or patch the
   written JSON so they're recorded).
3. **One** consolidated `send-ping` (per `docs/claude/telegram-pings.md`):
   ```
   action: send-ping
   target: claude
   priority: normal            # 'high' if any sub-review set operator_attention_required
   message: [system-report:<window>] roll-up <grade>: H:<h> P:<p> M:<m>. <dashboard_link>
   ```
   The `<link>` in the ping is the **`artifacts.dashboard_link`** (the SPA
   Reports deep link on GitHub Pages), NOT the GitHub blob — so tapping the ping
   opens the report inside the app, where the operator reads it and can Download
   the HTML. The
   `github_link` stays in `artifacts` as a secondary reference. Keep ≤200 chars.
   This is the only ping; the three sub-reviews' pings stay suppressed.

## What you DO write (and what you don't)

**Write:**
- `comms/reports/**` (the artifacts + index) — commit them.
- Whatever each sub-review writes when run (its own backlog drain, the
  performance review's `comms/claude_strategy_scores.jsonl`) — that's the
  sub-skill's owned write, not this skill's.
- The one consolidated ping (via `send-ping`, fallback
  `docs/claude/pending-pings.jsonl`).
- Read-only diag-trigger issues (`vm-diag-request`, `trainer-vm-diag-request`,
  `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file.
- Fire the three individual sub-review pings (suppressed — one consolidated ping).
- Invent a new backlog or write to the three review backlogs outside of running
  the sub-reviews themselves.
- Ask scoping questions (scope fixed here) or ask the operator to fetch state.
