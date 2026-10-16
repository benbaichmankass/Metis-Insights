---
name: research-ops
description: The routine research-OPERATIONS pass, run 4x/day (02:47, 08:47, 14:47, 20:47 UTC) as a bounded Sonnet lane. Checks the research pipeline is flowing (dispatcher, collector, receipts, stamps, stranded PRs), keeps the runnable floor of queue units topped up, audits every blocked / not-runnable unit and unlocks or retires it, reads every result landed since the last pass and applies its pre-registered rule, and files the follow-ups (research units and builds). Does NOT replace topic research (perf-followup, ML research, readouts on a specific question) — it only keeps the pipeline healthy and moving. Use when the manager dispatches a RESEARCH-OPS-<date>-<slot> lane, or the operator says "/research-ops", "check the research pipeline", "is research operations on track".
---

> **Doc status:** `live` · category `instruction` · last verified `2026-10-15` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md)

# /research-ops — keep the research pipeline flowing

> **Operator, 2026-10-10 (verbatim):** *"I think we need to create a routine research session skill with instructions for checking the research pipeline, ensuring the que has items that are running, verify blocked items so they can be unlocked, analyzing results and generating the follow up tasks (research and otherwise), that should probably be run 3-4 times a day, just to ensure that we are on track. This doesn't come instead of other research sessions on specific topics, but more to ensure that research operations are working smoothly and effectively"*

## What this is, and what it is not

A short, bounded **operations check**. The git infrastructure keeps the queue moving
(`research/queue/README.md` § "The queue refills and grades itself"); this pass checks
that it *is* moving, unblocks what is stuck, reads what landed, and files what comes next.

**It does NOT replace topic research.** `perf-followup` (performance-review follow-ups),
ML research, macro readouts and any lane on a specific question stay their own sessions.
Research-ops never answers a topic question itself — it files the unit that asks it.
It also never edits config: a `pass` that implies a roster/params/risk change becomes a
**Tier-3 proposal as a checklist note for the manager**, not a config edit.

What this skill now owns **on the routine cadence**, so no other doc has to be run by hand
for it: `performance-review` § "The research pipeline" Stages 1–2 (healthy? running?) and
the routine half of Stage 3 (read what landed); `research/queue/PLANNING.md` § 4 steps 1, 2
and 5 (throughput, grade landed results, retire stale). Weights (§ 4 step 3) and drafting
from `IDEAS.md` (step 4) stay in PLANNING.md and are used here only as the top-up source.
Do not duplicate those docs — read them, link them, follow them.

## What changed on 2026-10-15 (RESEARCH-LOOP-AUDIT, `docs/research/research-loop-audit-2026-10-15.md`)

The audit found this pass spending its budget closing results by hand while the steps only a session can do
(needs_review triage, the blocked/ audit, authoring from the plans) were `not_run` on 8 of 8 passes, and its
intake list did not include the research programme plans at all. The mechanism now carries the mechanical half:

- **Hand-written units are graded mechanically when their rows attest their own rule** (#18189, from lane
  RESEARCH-GRADE-BACKLOG): a `once` unit whose every E5 row carries the unit's own `decision_rule.id` is closed by
  `research-queue-grade.yml` (a FAIL closes it; no confirmatory re-run). Do not hand-close those. What stays yours:
  units whose rows carry a generic producer rule (e.g. `RULE-M20-EXIT-LEVER-IS-OOS-GATE`), recurring hand units,
  `needs_review` (step C), and units you opted out (`grading: {auto: false}` or your own `grading:` block -- the
  grader never overwrites it). You read results for their FOLLOW-UP (step D).
- **The programme plans are machine-read**: `research/programmes/*.yaml` question nodes are `queue_replenish.py`'s
  sixth source. A `unit:` node is emitted on its own once its prerequisite verdicts close (any branch, PASS or FAIL);
  a `needs_authoring:` node is in the **authoring queue** -- your FIRST intake source (step E).
- **Recurring e35 units whose power cannot reach the floor** are re-asked every 90 days by the grader
  (`recheck_after_days`), not monthly, and never land in `needs_review` -- do not retire them for that.
- **The floor is computed** (`check_research_queue_health.computed_floor`: 19 h of supply at `fired_24h`, never below
  25); the health check prints it, the idle backends and the authoring-queue depth every dispatch cycle.

## Ceiling and "done"

- **Model Sonnet, dollar ceiling ~$5 per run** (manager hard cap $6). One question per
  lane: *is research operations working, and what is stuck?*
- **Done =** (1) every step below ran, or the step names what blocked it; (2) every finding
  ended in exactly one of: a fix landed, a queue unit id, a `PI-…` pipeline id, a checklist
  row id, a retirement with a reason, or `"no action, because <reason>"`; (3) the run record
  (step F) is committed; (4) the one-line report was sent. A run that found nothing is done
  only if the report shows its denominators (a quiet report must show it could have found
  something — `blocker_watch.py` prints its own).
- **Stop early, say so.** Out of budget → close-out as a HANDOFF; write the record with what
  ran and mark the rest `not_run`. Never end silently.

## Step 0 — read the baseline

```bash
ls -1 comms/research/ops/*.json | sort | tail -1     # newest = the previous run's record
```

Its `runnable_after` / `results_read` / `units_authored` are this run's baseline; its
`handoff` list is your first work. No record yet (first run) → baseline is "now", say so.

## A. Pipeline health

```bash
python3 scripts/ci/check_research_queue_throughput.py --json
```

Record `runnable, queued, fired_24h, results_landed_24h, hours_since_last_dispatch,
idle_alarm`. `result_files_unreadable > 0` is "we could not look", not "nothing landed".

Then check the mechanism itself, not only its output:

```bash
python3 scripts/ci/check_research_queue_dispatch_liveness.py   # receipt: docs/claude/work/research-queue-dispatch-receipt.json
python3 scripts/ci/check_research_queue_health.py              # the alarm the dispatcher runs
git log --oneline -15 -- research/queue docs/claude/work/research-queue-dispatch-receipt.json   # stamps + receipts landing on main
```

- **Utilization and capacity flags.** Read the newest file in `comms/research/utilization/` (and
  `python3 scripts/research/research_utilization.py --week`). A `floor_thin` or `kaggle_quota_expiring` flag means a
  backend is idle with nothing due for it: that is top-up work in step B/E (author units whose `compute:` class lands
  there, with a `value:` block). `read_state: could_not_look` is a finding about the measurement, not an idle backend.
- **Dispatcher / collector / grader ran and landed.** The workflows are
  `research-queue-dispatch.yml` (hourly :20), `research-queue-grade.yml` (6-hourly :50) and
  `research-queue-replenish.yml` (daily 05:50). Read recent runs with the GitHub MCP
  (`actions_list` on the workflow) — success AND that their stamp / receipt / result commits
  reached `main`. A green run that landed nothing is not a landed run.
- **No stranded `automation/research-queue-*` PRs.** List open PRs by that head prefix. A
  stamp PR open past `STRANDED_STAMP_HOURS` (`scripts/research/dispatch_queue.py`) is the
  failure #17428 (RQ-DISPATCH-STALL) made self-healing: the dispatcher now *adopts* the
  stamps stranded in stale open stamp PRs. Read #17428 before judging — an old stamp PR
  whose units show a fresh `last_dispatched_at` on `main` is adopted, not a defect; one
  whose units are being re-fired is. Known cause class: checks cancelled ~15 min in, never
  re-requested (`PI-20261006-LCEVL8D5-0001`).
- **The watchdog runs between your passes (RESEARCH-WATCHDOG, 2026-10-13).** `research-watchdog.yml` checks the
  dispatcher, queue floor, each backend, the collector and stranded stamps every 15 min with no tokens, self-heals once,
  and escalates on the standing draft PR "ops-flags (do not merge)" + Telegram. **Read
  `comms/research/ops/watchdog_incidents.jsonl` (newest rows since the previous run record) first**: every
  `escalated` row without a later `resolved` row for the same `check` is an open incident and the FIRST INCIDENT of this
  run; `self_healed` rows are stalls the watchdog already fixed (record them, root-cause if repeated). Thresholds:
  `scripts/research/research_watchdog.yaml`. Contract and checks: `scripts/research/research_watchdog.py` docstring.
- **A broken dispatcher, collector or grader is the FIRST INCIDENT of the run.** Fix it in
  Tier-1 (docs/tooling/CI scripts) if you can this run; otherwise file **one** pipeline item
  with `due_when` + `origin.rerun` (the command above that shows it broken). Do not proceed
  to top-up work that the broken mechanism would not run anyway — say so in the record.

## B. Runnable floor

**Floor: computed, never below 25** — `check_research_queue_health.computed_floor(fired_24h)` =
max(25, ceil(fired_24h x 19 h / 24)): ~19 h of supply at the observed fire rate, so the queue stays
non-empty across three consecutive 6 h passes and one missed daily replenish (the 2026-10-10 derivation
gave 25 at `fired_24h = 32`; at 79 it is 63). It is computed every dispatch cycle -- read it from the health
check's first line, never retype it. A page still needs runnable < 25 (`H1`); between 25 and the computed floor
the health check warns `H1-THIN` (nothing left to refill or author) or `H1-AUTHOR` (the authoring queue holds
ready questions -- that is your step E work).

- `runnable >= floor` → nothing to do. Record it.
- `runnable < floor` → first let the template + programme refill do its job
  (`research-queue-replenish.yml` fills from `research/templates/*.yaml`; the health check
  exits 3 when templates can fill the gap). If templates cannot fill it, or `runnable`
  stays below the floor after the unlock work in step C, top up from the sources in step E
  until at or above the floor. **Runnable ≠ queued**: a unit whose `requires_result` is
  unmet, whose cadence has not elapsed, or that already ran `once` is queued, not runnable.
  Count with the throughput script, never by `ls`.

## C. Blocked / not-runnable audit -- and the `needs_review` triage, FIRST

**Do the `needs_review` triage before step D, every pass, until the bucket is empty.** It was `not_run` on
every pass from 2026-10-13 to 2026-10-15 because results were read first and the budget ran out. Population:
`python3 scripts/research/queue_grade.py --health` → `needs_review`. Per unit, one disposition: the rule's
reading of mixed / non-mechanical rows (close it with a session `grading:` block naming the verdict and the
rows -- the grader then never touches it again), a redesign unit, or a retirement with a reason. Minimum per
pass: the whole bucket, or 8 units if it is larger -- oldest first; say how many remain in the record.

Population: every `status: queued` unit that is **not runnable**, plus every unit in
`research/queue/blocked/`. Reuse the manager's watcher, do not re-implement it:

```bash
python3 scripts/ops/blocker_watch.py            # NOT_RUNNING / NEEDS_LANE / STALE_BLOCK findings
python3 scripts/research/dispatch_queue.py      # DRY RUN (the default): per-unit due / not_due / blocked reason, power + route grading
ls research/queue/blocked/
```

Group each unit by **reason** and name it in the run record:

| reason | what to check | unlock when |
|---|---|---|
| `requires_result` unmet | `research/results/<unit>/*.jsonl` for a `read_state: measured` record with the required verdict | the prerequisite landed with that verdict → it unlocks itself; if it landed with the *other* verdict, retire (moot) |
| precondition (prose `dispatch_precondition`) | it is **not evaluated** — a prose gate is not a gate | convert to `requires_result` or drop the prose; say which |
| ML unit, arm `unavailable` | `measurement.arm_status` / the engine note (`UNAVAILABLE ARMS`) names the missing library or weights | the library is installed on the trainer venv (Tier-2 lever row, filed once) → re-queue with `last_dispatched_at: null`; the rule is never edited |
| missing driver / lever | the harness or script the unit needs does not exist yet (the `blocked/` `blocked_on` block) | the build landed (`git log -- <path>`, checklist row `done`) → `git mv` out of `blocked/`, fill `run.workflow` + `lands.store` |
| gate | `power_state` `undeclared` / `unverifiable` (fix the declaration), route `unroutable` (declare peak memory / drop the GPU+trainer conflict) | the declaration is repaired |
| bad route | `run.workflow` is prose (`NEEDS_LANE`) — the dispatcher can never fire it | re-declare `research-script-run.yml` + `run.command` (README § RQ-RUN), or hand to a lane |
| waiting for compute | decision line says `waiting for compute:` (backend full, or Kaggle quota under the floor / unreadable) | nothing to unlock: it retries every cycle. Repeated `quota unreadable` = the `KAGGLE_API_TOKEN` secret or `kaggle quota` format (`compute_router.parse_quota`) is the incident |
| class contradicts routing | `BLOCKED_ROUTE` `compute: ... must agree` | make `compute:` and `routing:` agree (or use `compute: auto`) |

For each group:

1. **Unlock** what is unlockable — a prerequisite landed, a lever now exists, a stale
   `requires_result`. Move/edit the unit file in this PR (units authored by a hand edit hold
   for a human per E57 — expect that, say so).
2. **Retire** what is moot with `status: retired` and a stated reason (superseded, answered
   elsewhere, precondition can no longer be met). Closing a dead unit with a reason is worth
   more than carrying it. Never touch units belonging to another live research lane's pass
   (check the checklist for lanes in flight on the same ids).
3. **File each missing lever ONCE** — a checklist row (a build) or a pipeline item — keyed
   so a later run finds it instead of filing a twin: search the checklist and
   `scripts/ops/pipeline.py --stats`/`--all` for the lever's name first.
4. **Never leave a unit blocked without a named lever and an owner.** A `blocked_on` that
   names neither is a finding of this run; fix it before moving on.

## D. Results since the last run

```bash
git log --since="<previous record's finished_at>" --name-only --pretty= -- research/results comms/research \
  | sort -u                                                     # landed since the baseline
python3 scripts/research/research_disposition.py --unread-only  # landed with no disposition
python3 scripts/research/research_disposition.py --report
```

For every result: read the verdict fields **and the unit's own pre-registered
`decision_rule`**, check `power_state`/n first (an `underpowered` / `accruing` read says
"cannot answer yet", never a verdict), then apply the rule **verbatim** — never a rule
written after seeing the number:

- **pass →** the follow-up the rule names: another queue unit, or — if it implies a
  strategy/params/risk/roster change — a **Tier-3 proposal written as a note on the owning
  checklist row for the manager**. Never a config edit, never a claim a leg is promoted.
- **fail →** the rule's disposition plus a REFINE or redirect follow-up (retire the dependent
  units whose `requires_result` can no longer be met). **A FAIL closes the CONFIGURATION it tested** (honest
  negative on that cell: lever values, leg, window, features) -- write it that way. It never retires the idea:
  a FAMILY retirement needs a review record (`scripts/research/family_retirement_policy.yaml`: test-design check,
  breadth across configs/windows/instruments, outside evidence with sources, a reason the idea is dead), and
  `family-retirement-guard` refuses family-closure wording or a `retires_family:` without one. When a family has
  several config FAILs and no review, the follow-up is a review or a differently-designed test, not a kill.
  Write ONE line per FAIL in the run record's `results_read` (and the unit's follow-up note):
  **"configuration closed: <what was tested>; family <name> stays open; next test = <unit id or the question>"**.
  A unit may carry `family: <name>` (and `retires_family:` defaults to false); only a C-verdict review record
  with all six protocol items (`research/family_reviews/README.md`) retires one.
- **indeterminate →** extend n or redesign, in one line, and a unit that does it.

Record each disposition with `research_disposition.py --record` (it routes to `append` and
keeps the refusals; **never hand-write the ledger**). Template-generated and
`grading: {auto: true}` units are graded by `research-queue-grade.yml` — check they were,
and handle only `grading.needs_review: true` ones (`grep -l "needs_review: true" research/queue/*.yaml`).

**ML results (units with an `ml_bakeoff:` block, run by `scripts/research/ml_bakeoff.py` on the trainer or a runner).**
Read them exactly like strategy results, plus three checks that are specific to them: (1) `measurement.arm_status` —
an `unavailable` candidate means the verdict covers only the arms that ran (a FAIL with an unavailable arm is already
INDETERMINATE by the engine; do not upgrade it), file the install lever once; (2) `measurement.headline_checks` and the
control rows — if a no-neural control (HAR-RV) matches the incumbent, that is the headline and goes in the note;
(3) `measurement.licences` — a `research_only` arm that wins puts the **licence question to the manager**, never a serve proposal.
The follow-up per verdict (pass → Tier-3 candidate-manifest note + next unit; fail → exact configuration into
`families.<f>.history`; indeterminate → extend/redesign; `producer_failed`/`no_data` → not a verdict, fix the route) is the table in
`.claude/skills/frontier-research/SKILL.md` § "The research → test → improve loop" — the one place it is written.

**ML plan write-back.** If a result's unit id (or the checklist row a lever clears) is a `ref` in
`docs/plans/ml-plan-items.yaml`, set that plan item's `state`/`close` text in the same PR and note
the verdict beside the roadmap's "AI trader (ML) — build plan" measured-state table; run
`python3 scripts/ops/ml_plan_status.py` before pushing (it must show no `ORPHAN`/`CLOSED-OPEN`
for what you touched). The weekly `/ml-review` owns the wider plan review
(`ml-review` § "The ML plan loop"); you only keep the plan honest about what you graded.
**Family history:** if the graded plan item carries a `family:`, also append the verdict
(`PASS`/`FAIL`/`INDETERMINATE`) to `families.<family>.history` in the same file — that history is
what `ml_plan_status.py --diversity` reads for `FAIL-STREAK`, and what the weekly frontier scan
(`.claude/skills/frontier-research/SKILL.md`) uses to decide whether a family is exhausted or merely
untested. A FAIL records the exact configuration tested; it does not close the family.

**Programme write-back.** If the graded unit was emitted from a programme node (`programme_node:` block) or is
named in a node's `after`, check `python3 scripts/research/queue_replenish.py --programme-state` shows the node
moving (`answered`, the next node `ready`/`moot`). A verdict whose next question is not in the tree yet is an
authoring task: add the node (a `needs_authoring:` line is enough) to `research/programmes/<NN>.yaml` in this
pass's PR -- that is how the plan learns between weekly reviews.

**Generate the follow-ups, both kinds:** research → queue units (step E rules); otherwise →
checklist rows / pipeline items for builds, bugs and tooling gaps. Every result ends in a
unit id, a `PI-…`/checklist id, or `no action, because …`. A finding in chat or a memo is
not filed (CLAUDE.md § "Two categories").

## E. Intake sources for the top-up

Draw new units from, in this order, until the floor is met or sources are exhausted:

0. **The programme authoring queue** -- `python3 scripts/research/queue_replenish.py --authoring-queue`
   (ready `needs_authoring:` nodes, ranked goal-first -- G1 net P&L before everything -- then value). Author a
   node by REPLACING its `needs_authoring:` line with a full pre-registered `unit:` body in
   `research/programmes/<NN>.yaml` (same rules as below; the replenisher emits the unit on its next run, and the
   node's tree state stays true). Prefer nodes whose compute class lands on a backend the health check lists as
   idle. Cap: ~10 nodes per pass (they are plan questions, already scoped) on top of the ~6 hand units below.
1. The research rows on the checklist — `ARB-RESEARCH`, `CROSS-SIGNAL-RESEARCH`,
   `NEWS-VETO-EVAL`, `PM-SURPRISE-*`, `WEPE-BUILDOUT`, `SIGNAL-CATALOG`,
   `RESEARCH-FOLLOWUPS`, `RESEARCH-TOOLING-GAPS`, … (list them with
   `python3 scripts/ops/checklist.py --render` and read the `note`).
2. `docs/claude/work/IDEAS.md` rows whose `routed_to` names a research unit, or that are
   still `new` and research-shaped (the manager owns that file — read, don't reshape).
3. The latest `/perf-followup` run record (`comms/perf_followup/`) and
   `/performance-review` output (`comms/reviews/performance-review-*.json`,
   `research_pipeline`, `proposed_tweaks`).
4. Soak `NEEDS_DATA` dispositions (`scripts/ops/mandate_resolver.py` auto-files the pipeline
   row; the data-getting task is a unit — "either we have enough data to decide, or getting
   that data becomes a task").

**Authoring rules** (`research/queue/PLANNING.md` § 1 is binding; summary only):
- Mint ids with `python3 scripts/research/next_rq_id.py --fetch` — never hand-pick.
- `theme` (a key of `research/THEMES.yaml`) and `priority` 1–3 are required; weights are the
  operator's — **do not change `THEMES.yaml`** in this pass.
- Use **existing drivers** (a real `run.workflow` or `research-script-run.yml` + a script that
  writes `verdict.json`). A question whose instrument does not exist is a `blocked/` unit
  **plus** the lever filed once (step C.3) — not a dispatchable unit.
- `decision_rule` with `registered_before_run: true`, committed in the same PR as the unit;
  `what_it_does_not_do` stated (an upper-bound or scoping unit says it is not a promotion
  case). Too vague to register a rule → a SCOPING unit, not a dropped idea.
- A unit that needs a prior result uses `requires_result`, not prose.
- **Declare the compute class and the value; never pick the runner** (RESEARCH-ROUTER, 2026-10-13;
  `research/queue/PLANNING.md` § 1b is the authoring contract). Write `compute: cpu_light | cpu_long |
  needs_vm_data | gpu | gpu_preferred | auto`, a consistent `routing:` block and, when you can state it, a
  `value:` block (`usd_at_stake_per_month`, `confidence`, `bucket`, `basis`). Do **not** write
  `run.workflow` on a new unit: the token-free dispatcher resolves class -> backend from
  `scripts/research/research_compute.yaml` (runner / trainer / Kaggle GPU), enforces each backend's capacity and the
  Kaggle weekly quota, and ranks due units by value per cost. GPU work goes to Kaggle through
  `research-script-run-kaggle.yml` and comes back through the same collector, so a result is read exactly like
  any other. A class change or a capacity edit is a `scripts/research/research_compute.yaml` change (Tier 1, reviewed),
  not a unit edit.
- Cap authoring per run (~6 units) — this is operations, not a research session. Excess goes
  to the run record's `handoff` list for the next pass or a topic lane.

## F. Record and report

Write ONE durable, machine-readable record the next run reads as its baseline:
`comms/research/ops/<YYYYMMDDTHHMMZ>.json` (newest filename wins):

```json
{
  "run": "RESEARCH-OPS-2026-10-10-2047", "finished_at": "2026-10-10T21:30:00Z", "by": "<session id>",
  "floor": 63, "floor_basis": "computed_floor(fired_24h 79)", "authoring_queue_depth": 5,
  "idle_backends": ["kaggle", "kaggle_cpu", "trainer"], "needs_review_triaged": 8, "needs_review_left": 29,
  "throughput": {"runnable_before": 8, "runnable_after": 27, "queued": 105, "fired_24h": 32,
                 "results_landed_24h": 32, "hours_since_last_dispatch": 4.9, "idle_alarm": false},
  "mechanism": {"dispatch_receipt": "ok", "collector": "ok", "stranded_stamp_prs": [], "incident": null},
  "results_read": {"pass": 0, "fail": 0, "indeterminate": 0, "units": []},
  "unlocked": [], "retired": [{"unit": "RQ-…", "reason": "…"}],
  "levers_filed": [{"lever": "…", "ref": "PI-…|<checklist id>"}],
  "units_authored": [], "followups_filed": [],
  "not_run": [], "handoff": ["what the next pass or a topic lane must pick up"]
}
```

Commit it in the run's PR (or, if the pass changed nothing else, with the same
self-landing declaration as any Tier-1 PR — `.github/pr-landing/<slug>.json`,
`.github/pr-automerge-requests/<slug>.txt`, `claim_merge_slot.py --branch-claim`).
Then send the **one-line report** to the manager, e.g.:

`RESEARCH-OPS 2026-10-10-2047: runnable 8→27 (floor 25), fired 32/24h, results p0/f0/i0, unlocked 2, retired 1, levers 1, units +6, incident none — PR #NNNN`

## Guards to run before you push

The ones CI runs on research/skill/doc changes: `python3 scripts/ci/run_guards.py` (or the
named subset: the research-queue guards — `check_research_queue_decision_rule.py`,
`check_research_queue_id_bands.py`, `tests/test_research_queue.py` — `canonical-doc-coherence`,
the skill/document-index guards) and `python3 scripts/ci/check_pr_landing.py --base origin/main`.

## What this skill never does

Never `send_later` / `create_trigger` (the manager owns the schedule); never edit
`config/*.yaml` or `THEMES.yaml`; never hand-edit a disposition ledger or a stamp; never
re-fire a unit by hand to "kick" the dispatcher; never halt or disable the queue or any
workflow (CLAUDE.md "no halting") — a failing mechanism is diagnosed, fixed or flagged once.
