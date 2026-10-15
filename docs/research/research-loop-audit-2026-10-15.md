# Research loop audit — 2026-10-15

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-15` · lane `RESEARCH-LOOP-AUDIT-AND-DRIVER` (dispatched by Manager Session 2026-10-14, `session_01VduHYzdaXe27wTDBLSu8ao`) · checklist row `RESEARCH-LOOP-AUDIT-AND-DRIVER` · **Tier-1: nothing here changes a config, a roster, a schedule or an order path.**

**Operator directive, 2026-10-15 ~06:12Z (verbatim):** *"So yes definitely build that. But we also like have the research dispatcher and we have the four times daily research sessions which are supposed to be doing this already. So like make sure that we're not building something that isn't already built and make sure that like it's not just a Band-Aid. It's a building reel infrastructure but like making sure like trying to understand why this isn't already happening the way that at least I thought it was supposed to and if and making sure that we're building the right thing".*

This is PHASE 1 of the lane: the loop **as built**, with code citations and measured last-run evidence, then the root causes. Nothing is built here. Every number is MEASURED on `origin/main` at about 06:15–06:40Z on 2026-10-15 unless it is marked otherwise; each one names the command that reproduces it.

---

## 0. The answer in five lines

1. **The machinery the operator expects exists and is running.** It has a dispatcher (hourly), a collector, a mechanical grader (6-hourly), a template refill (daily), a watchdog (every 15 min) and research-ops (4×/day). Over 24h it fired **79** units and landed **83** results.
2. **Results go ungraded because the grader is opt-in.** It grades only template-generated units or units that set `grading.auto: true`. Every programme unit is hand-written and none opted in. **140** hand-written `once` units have results on `main` and are still `queued`. **123** of them have unanimous mechanical verdicts that the grader could close today.
3. **Intake reads "exhausted" because nothing reads the programme plans.** The automatic refill (`queue_replenish.py`) expands only 5 template families over the roster's legs, and all 5 are at 0 unclaimed points. The 01–06 W1–W4 backlogs are prose tables. One weekly Tuesday Opus lane turns them into units; its first run is 2026-10-20. Research-ops' own intake list (§ E) does not include the plans either.
4. **The "2 weeks of tests" took about 1.5 days of compute.** Of the 132 units minted 2026-10-13/14, **101 have already run**. The runner backend fires 3 units per hourly cycle and is saturated: **12 due units are waiting**. The other backends have nothing due: Kaggle GPU has used **0.19 of 30 h** this week, Kaggle CPU **0** fires in 7 days, and the trainer has **0** due units today against ~19 idle hours per day.
5. **What is missing is the step from verdict to next question.** A pre-minted chain covers only the PASS branch (`requires_result`). A FAIL or indeterminate result, and every W2+ backlog item that has no unit yet, waits for a session to write a unit. Research-ops writes 0–6 units per pass and skips needs-review triage on every pass (§ 3).

---

## 1. The loop as built

| step | component (code) | cadence | last-run evidence (MEASURED) |
|---|---|---|---|
| **Dispatch** | `.github/workflows/research-queue-dispatch.yml` (cron `20 * * * *`) → `scripts/research/dispatch_queue.py` (value-ranked, per-backend capacity from `scripts/research/research_compute.yaml`) | hourly | Runs #283–#293, 2026-10-14 17:58Z → 2026-10-15 05:51Z: one per ~hour; #290 failed; the rest succeeded. Receipt `docs/claude/work/research-queue-dispatch-receipt.json` = run 38682772321, 04:43Z, `fired: true`. Throughput (`check_research_queue_throughput.py --json`): `fired_24h 79`, `fired_7d 327`, `runnable 17`, `queued 246`. |
| **Collect** | same workflow, step "Collect the runner's results" → `scripts/research/collect_runner_results.py`; land job batches results, receipt and stamps onto `main` | hourly | Run 38682772321: collect 04:42–04:43Z; land job 04:43–05:14Z (31 min, of which results 9 min, receipt 7 min, stamps 14 min); "Assert every batched record is readable on main" succeeded. `results_landed_24h 83`, `result_files_unreadable 0`. |
| **Grade** | `.github/workflows/research-queue-grade.yml` (cron `50 */6 * * *`) → `scripts/research/queue_grade.py --write` | 6-hourly | Dry run now (`queue_grade.py --json`): **9** pending changes, so the grader is caught up **on the population it is allowed to see**. `--health`: **37** units carry `needs_review: true`. See § 2a. |
| **Disposition ledger** | `scripts/research/research_disposition.py` (`docs/research/research-disposition-ledger.jsonl`) | by hand (research-ops § D) | `--unread-only` lists unread e35/m20 corpus rows back to 2026-10-04. It covers the e35/m20/gld corpora, **not** E5 unit results; research-ops 2026-10-15 03:05Z: *"its ledger covers e35/m20/gld_compat corpora, none of the units read here"*. |
| **Refill** | `.github/workflows/research-queue-replenish.yml` (cron `50 5 * * *`, also dispatched by the health step when exit 3) → `scripts/research/queue_replenish.py` over `research/templates/*.yaml` | daily + on demand | `queue_replenish.py --target 25` now: `deficit 8; candidates {'e35-bracket': 0, 'm20-exit-lever': 0, 'macro-valuation': 0, 'prop-fit-breakout': 0, 'roster-walkforward': 0}`. Dispatch run 38682772321 health step: *"H1 runnable units 16 < 25 … the templates hold only 0 point(s) for a deficit of 9 -- a refill cannot close this gap"* → send-ping. |
| **Watchdog** | `.github/workflows/research-watchdog.yml` (`*/15`) → `scripts/research/research_watchdog.py` | 15 min | Incidents in `comms/research/ops/watchdog_incidents.jsonl`. **Not re-read in full this session**: research-ops has listed it `not_run` on 4 of its last 5 passes. |
| **Utilisation** | `comms/research/utilization/<run_id>.json` written per dispatch; `scripts/research/research_utilization.py --week` | per dispatch | See § 2d. |
| **Research-ops** | `.claude/skills/research-ops/SKILL.md`, a Sonnet lane 4×/day dispatched by the manager; run records in `comms/research/ops/<ts>.json` | 4×/day | 14 records 2026-10-11 → 2026-10-15 03:05Z. See § 3. |
| **Programme review** | trigger `trig_01Xjp6CTXuj9taPA6uCcKWFo`, Tue 09:53Z: one Opus lane, $12; *"Keep the queue fed ONLY from these backlogs"* | weekly | Created 2026-10-14 18:05Z; **never fired**; next run 2026-10-20 09:53Z. The 01–06 plans were landed by the 2026-10-14 programme lanes, not by this trigger. |
| **Perf-followup** | `.claude/skills/perf-followup/SKILL.md`, dispatched after the Thursday walk-through | weekly | One run record: `comms/perf_followup/perf-followup-20261010-191000.json`. The Thursday walk-through trigger fired 2026-10-15 06:18Z (today). |
| **Frontier scan / ML-plan loop** | `.claude/skills/frontier-research/SKILL.md`; `/ml-review` step 9; records in `comms/ml-plan/` | weekly (Wed prep lane, then Thu walk-through) | One scan: `docs/research/frontier-scan-2026-10-11.md`. ML-plan records `comms/ml-plan/20261011T1700Z/1900Z/2100Z.json`, all 2026-10-11. Prep lane trigger `trig_01LE61kLKDFKptEfTfZ7vnYS` fired 2026-10-14 18:54Z. |
| **Weekly roadmap pass** | Wednesday planning part 2 (`trig_01JcQThYTsTQi7BDJDS1HCLX`) | weekly | The Monday `ROADMAP-PASS` rows are retired (manager SKILL § weekly cycle). Next run 2026-10-21. |

**What the population looks like** (all `research/queue/*.yaml` with `status: queued`, n = 249; script in § 5):

| bucket | n |
|---|---|
| dispatched, results on `main`, `once`, no grading | **145** |
| dispatched, `once`, no result yet / confirmatory | 3 / 2 |
| recurring monthly/weekly, not `needs_review` | 45 |
| recurring, `needs_review` | 36 |
| never dispatched | 18 |
| `research/queue/blocked/` (separate dir) | 66 blocked + 1 retired |

---

## 2. The five questions

### (a) Why results go ungraded

**Root cause A — the mechanical grader is opt-in, and the programme units never opted in.** `scripts/research/queue_grade.py::grade_unit`:

```python
auto = isinstance(unit.get("generated"), dict) or \
    (isinstance(unit.get("grading"), dict) and unit["grading"].get("auto") is True)
if not auto:
    return None
```

The comment beside it gives the reason: one hand-written fan-out unit (`RQ-20260928-005`, 3 legs over 3 dispatches) would be misread from its first row. That case is real but rare, and the exclusion swallowed the whole hand-written population. MEASURED over the 231 queued+dispatched units (199 of them with E5 rows; the e35 units grade from the corpus instead):

| | hand-written | auto |
|---|---|---|
| `once` with E5 rows | **140** | 7 |
| `once`, no rows yet | 3 | 0 |
| recurring | 3 (no rows) | 78 (52 with rows) |

The 140 hand-written `once` units, by the set of verdicts on their E5 rows:

| verdicts | n | grader would do |
|---|---|---|
| `{fail}` | 73 | re-queue for one confirmatory run (the "never kill on one pass" rule, operator 2026-09-27) |
| `{pass}` | 31 | `done`, verdict pass |
| `{indeterminate}` | 15 | `done`, `awaiting: more_data` |
| `{no_action_warranted}` | 4 | `done` |
| `{no_action_warranted, pass}`, `{fail, not_applicable}`, `{indeterminate, not_applicable}`, `{not_applicable}`, `{wiring_only}` | 17 | `needs_review` |

So **123 of 140 (88%)** are mechanically gradable by the grader's own unanimity rule. That includes `RQ-20261014-d6e7-01`: its PASS landed at ~01:04Z and the unit still reads `queued`. Its dependents `d6e7-02/04` still dispatch, because `requires_result` reads the result rows directly. The unit status, though, never moves.

**Root cause A′ — "graded" and "followed up" are separate acts, and only the first is mechanical.** Even for an auto unit, the grader writes `status` and `grading`. The next unit (the PASS follow-on, the FAIL redirect, the extend-n for an indeterminate) is authored by a session. The disposition ledger covers only the e35/m20/gld corpora. So no machine record says "this verdict was acted on" for an E5 unit (§ 3).

**Secondary — recurring e35 units that can never clear their power floor.** 21 queued monthly e35 units are graded `indeterminate` at `base_oos_trades` 0–29 against a floor of 39 (`grade_e35`, `E35_FLOOR = 39`). Most are daily-bar ETF legs with 4–11 OOS trades. A monthly re-sweep adds about one trade per month, so they will not reach 39 for years. Yet they re-fire monthly, consume runner slots and sit in the `needs_review` bucket (36 of the 37 `needs_review` are recurring). `research_disposition.py --report` already labels three of them `power=infeasible`. The grader does not act on that label.

### (b) Why intake reads "exhausted" while 01–06 hold W1–W4 backlogs

**Root cause B — nothing machine-reads the programme plans.** MEASURED: `grep -rln 'research/programmes' scripts/ .github/workflows .claude/skills` returns `scripts/ops/document_index.py`, two research scripts that cite the docs in comments, and `.claude/skills/manager/SKILL.md`. That last one is the weekly Tuesday trigger description. **No producer reads them.**

- `queue_replenish.py` sources = `research/templates/*.yaml`: 5 families (`FAMILY_ROADMAP`: e35-bracket, m20-exit-lever, macro-valuation, prop-fit-breakout, roster-walkforward). Each `grid:` expands over the roster's legs, so it is exhausted once every leg × point exists. Right now all 5 are at 0.
- The health check treats "templates exhausted" as "intake exhausted". `check_research_queue_health.py` exit 1 reads *"a refill cannot close this gap"*. That is true of templates, but the plans hold dozens of questions.
- Research-ops § E intake order is checklist research rows → `IDEAS.md` → perf-followup → soak `NEEDS_DATA`. **The programme plans are not on the list**, and authoring is capped at ~6 units per pass.
- The only plan → unit conversion is a **weekly** Opus lane. The 2026-10-14 programme lanes pre-minted W1 units (and some W2 units as `requires_result` chains). Everything else in W2–W4 is prose. Examples: 02-AI-TRADER W3 #16–#20, 01 W2 "alpaca 1d family-pooled finite-target unit", and every "read X, then decide" decision point.

**Root cause B′ — the plans were written against an assumed cadence the compute does not have.** The backlogs assume a week per W-block. MEASURED: of the 132 units minted 2026-10-13/14, **71 done + 30 dispatched-with-results + 2 dispatched-no-result = 103 dispatched within ~36 h**; 17 queued undispatched; 12 blocked. The W1 block ran in about 1.5 days. The W2 block is gated on W1 verdicts that the grader does not close (A), and on follow-ups that nobody writes until a session does (A′). So the queue drains to the template floor and stops. That reads as "exhausted" while the plan still holds three weeks of questions.

### (c) Units that cannot run automatically, and why

| class | n | why | lever |
|---|---|---|---|
| `research/queue/blocked/` | 66 | a missing instrument. The largest groups are 13 `work_item` (checklist builds), 8 persistent point-in-time collectors (GDELT/Wikimedia/FOMC/EDGAR/Kalshi, `RQ-20261012-5bd1-*`), `ML-LEG-CORPUS` ×2, `EXIT-PATH-EMIT-OFFLINE`, perp funding/OI history | each names its lever; the builds are on the checklist |
| prose `run.workflow` (session-bound) | ≥2 confirmed (`RQ-20260928-001`: *"none — session-local run of scripts/backtest_ict_scalp.py per leg"*; one `trainer-vm-diag (issue label …)`) | the dispatcher can never fire prose | retarget to `research-script-run.yml` + `run.command`, or a lane (DUE-ROUTE-20261015 found the same) |
| `needs_review` | 37 (36 recurring) | non-unanimous or `indeterminate` on a recurring unit; mostly the infeasible-power e35 monthlies of § 2a | triage has been `not_run` on every research-ops pass since 2026-10-13 (§ 3) |
| retry-capped non-verdicts | per `dispatch_queue.py` `MAX_NONVERDICT_RETRIES = 2` | two `producer_failed`/`no_data` rows → *"needs a human"* | shown in the dry run as `retry cap reached` |
| due, waiting on runner capacity | **12–15** right now (`470a-01/05/06`, `4a4c-01..08`, `736c-01`, `77dc-02`, `d6e7-02/04`) | `github_runner.max_concurrent: 3`, 3 fired per cycle | **not blocked**: they read `would_dispatch` and drain at ~3/h. blocker_watch reports them `NEEDS_LANE` because it reads `run.workflow` literally. That is a false positive, filed by DUE-ROUTE-20261015 as row `BLOCKER-WATCH-COMPUTE-ONLY` |

The manager's "17 of yesterday's units were never dispatched (NEEDS_LANE, or gated)" resolves as follows. 13 are capacity-queued on the runner and are not stuck: the cycle at 05:52Z fired 3 and left 12 `due_waiting` (`comms/research/utilization/38689049828.json`). 4 are gated by their own declared `requires_result` (`d6e7-03/05`, on `d6e7-02/04` passing).

### (d) Compute utilisation vs capacity

From `research_utilization.py --week` (34 measured dispatch cycles; per-run files exist only since RESEARCH-ROUTER, 2026-10-13) and the newest per-run file:

| backend | capacity | 7-day use | today | binding? |
|---|---|---|---|---|
| `github_runner` | 3 concurrent | 88 of 102 slot-cycles; 86 fires; 26 cycles with units waiting | 3/3, **12 waiting** | **yes**: saturated |
| `github_runner_data` | 2 | 6 of 50 | 0 due | no units |
| `kaggle_cpu` | 1 | **0 of 8**; 0 fires | 0 due | no units |
| `trainer` (windows 03–05Z, 07–24Z) | 1 | 18 of 34; 15 fires | 0 due | no units right now; services use 28.4% of the box-week |
| `kaggle` GPU | 1 slot, 30 GPU-h/week | 10 of 34 slot-cycles; 8 fires; **0.19 h of 30 h used**, 29.81 h expire 2026-10-17 00:00Z | 0 due | **no units** |
| `runpod` | ledger $10/month | $0, 0 fires | — | fallback only |

Flags over the week: `floor_thin` 34 of 34 cycles, `kaggle_quota_expiring` 4.

**Do not raise the runner cap blindly.** Lane RESEARCH-CAPS (2026-10-13, PR #17919) measured CI at p90 = 16 concurrent jobs, with the shared pool binding at ~20. Raising `max_concurrent` above 3 would queue CI behind research. The cap is a real constraint, not a self-imposed one. **The waste is on the other side:** ~30 GPU-h/week, the Kaggle CPU kernel and the trainer's idle windows have no units. Everything authored lands as `cpu_light` on the one saturated backend. Meanwhile a share of the runner's slots goes to filler: the infeasible e35 monthlies (§ 2a) and the confirmatory reruns of 73 FAILs that nobody has graded yet (once graded, they re-queue themselves).

### (e) The intended loop vs the built loop

**Intended** (operating plan § 3; RESEARCH-MAP §§ 2, 5; research-ops SKILL; the operator 2026-09-28: *"running 24/7 with or without Claude … keep replenishing it"*; 2026-10-14: programmes carry research forward autonomously):

```
plan question ─▶ pre-registered unit ─▶ run (cheapest fitting compute) ─▶ result
      ▲                                                                       │
      │                       the result IS the decision (rule registered first)
      └── next question from the plan's tree ◀── advance / refine / kill ◀───┘
          (prioritised by roadmap goal, G1 net P&L first; frontier + perf-followup feed in)
```

**Built:**

| intended step | built? | where it diverges |
|---|---|---|
| plan question → unit | **weekly, by hand** (Tuesday lane), plus ad-hoc programme lanes | no machine-readable question tree; replenish reads templates only (B) |
| unit → run on the right compute | **yes**, token-free (RESEARCH-ROUTER) | authored units almost all declare `cpu_light`, so GPU/Kaggle-CPU/trainer have no supply (d) |
| result → verdict | **only for template/auto units** | opt-in grader (A) |
| verdict → next question | **PASS branch only**, when pre-minted as `requires_result` | FAIL/indeterminate/unminted W2+ need a session; research-ops caps authoring at ~6 per pass (A′, § 3) |
| verdict → programme tree, roadmap | by hand (research-ops writes `ml-plan-items.yaml` for ML items; the weekly review rewrites the plans) | the plans' § 2 "measured state" and Q-status are prose with no writer between Tuesdays |
| prioritise by G1 / value | **yes at dispatch** (value score × theme share) | there is nothing of high value to rank when supply is templates; value ranking cannot create questions |
| health signal | `runnable ≥ 25` | counts units, not plan coverage or backend supply, so "templates exhausted" pages as if research had run out |

---

## 3. Research-ops: what it does vs what its SKILL says

Last 8 run records (`comms/research/ops/20261013T1530Z.json` → `20261015T0305Z.json`):

| record | runnable before→after | results read (p/f/i) | units authored | `not_run` (selected) |
|---|---|---|---|---|
| 1013T1530Z | 5 → 10 | 0/9/4 | 3 | blocked/ audit by reason, needs_review triage (36), stranded-PR audit, top-up to 25 |
| 1013T2100Z | 34 → 34 | 3/3/0 | 0 | needs_review triage, stranded-PR audit, actions_list |
| 1014T0040Z | 14 → 27 | 0/0/0 | 1 | mechanism checks, results read, blocked/ audit, needs_review triage |
| 1014T0300Z | 25 → 36 | 5/5/0 | 1 | watchdog read, `--unread-only` (27 rows), blocked/ audit, needs_review triage |
| 1014T0930Z | 23 → 29 | 2/9/6 | 1 | stranded PRs, watchdog read, blocked/ audit, needs_review triage |
| 1014T1500Z | 6 → 12 | 3/11/7 | 6 | stranded PRs, watchdog read, blocked/ audit, needs_review triage (37) |
| 1014T2110Z | 27 → 28 | 2/4/9 | 1 | stranded PRs, watchdog read, blocked/ audit, needs_review triage, pipeline routing |
| 1015T0305Z | 20 → 22 | 3/5/0 | 2 | stranded PRs, `--record` (ledger does not cover the units read) |

**Divergences, each with its cause:**

1. **It reads results but cannot close them.** § D says "apply the rule verbatim" and record with `research_disposition.py --record`. The ledger does not cover E5 units, and closing a hand-written unit is a hand edit that holds for a manager merge (E57, `scripts/ci/check_pr_landing.py::stamp_only_violation`). So reads end up as notes and handoffs, while the 140-unit pile grows. Hold latency is **not** the bottleneck (#18098 merged in 21 min). The bottleneck is that nothing mechanical writes the outcome.
2. **Needs-review triage and the blocked/ audit by reason have been `not_run` on 8 of 8 passes.** A ~$5 Sonnet pass reading 9–21 results does not reach them. They are the steps that would retire the infeasible e35 monthlies (§ 2a).
3. **Top-up authoring is 0–6 units per pass, from sources that do not include the plans** (§ 2b). At ~79 fires per day, even 6 units per pass × 4 passes is 24 units per day, below consumption.
4. **Its floor (25 runnable) is the wrong target.** The SKILL derived it from `fired_24h = 32` (2026-10-10) and says to re-derive it *"if `fired_24h` moves by more than a third"*. Today `fired_24h` is **79**, up 147%. No pass re-derived it.

---

## 4. Root causes (named) and the proposed build

| # | root cause | evidence | fix (extend before add) |
|---|---|---|---|
| **RC1** | The grader is opt-in. Hand-written units are never machine-graded. | `queue_grade.py::grade_unit` `auto` gate; 140 / 123 (§ 2a) | **Extend `queue_grade.py`:** grade every `once` unit by the existing unanimity rule unless it opts **out** (`grading: {auto: false}`) or declares a multi-run fan-out (`lands.min_rows > 1` already guards that; add `lands.runs`). The E58 self-land path already verifies grade PRs by reproducibility, so no new trust is added. Coordinate with RESEARCH-GRADE-BACKLOG: that lane clears today's pile; this stops it re-forming. |
| **RC2** | No machine-readable question tree. Nothing turns plan backlogs into units between weekly reviews. | § 2b grep; replenish = 5 templates; research-ops § E omits the plans | **Extend `queue_replenish.py` with a sixth source, programme nodes**, and do not add a new workflow. Each programme gets a sidecar `docs/research/programmes/<NN>.questions.yaml` listing question nodes: `id`, `programme`, `roadmap`, `goal` (G1…), `value`, `compute`, `after` (unit or node + verdict), and either a fully pre-registered `unit:` spec or `needs_authoring: <one line>`. Replenish materialises a node's unit deterministically once its `after` verdicts are graded, so it self-lands under E58 exactly like a template unit. Nodes with no spec form an **authoring queue**, ranked G1-first × value × idle-backend bonus. That queue is research-ops' first intake source and the Tuesday review's input. Note: a node `unit:` spec is pre-registered the moment the sidecar lands, and the sidecar lands by human review (it is a registration, like any hand unit). |
| **RC3** | Verdict → next question exists only for pre-minted PASS chains. | § 2e; research-ops § 3.1 | Same mechanism: a node's `after` can name any verdict (`pass`, `fail`, `indeterminate`), so the plan authors the refine/redirect branch **before** the run, as the operator's refinement-first default requires. A graded verdict is written back to a generated `docs/research/programmes/STATE.json` (node → answered / unlocked / retired, with the unit and verdict). The weekly review and the RESEARCH-MAP read that file instead of re-deriving it. |
| **RC4** | The health signal counts units. It pages "exhausted" when templates run out and never sees idle backends. | `check_research_queue_health.py` H1; `floor_thin` 34/34; Kaggle 0.6% used | **Extend the health check and utilisation:** report the authoring-queue depth and due units per backend vs free capacity. "Templates exhausted" with a non-empty authoring queue is an authoring task (research-ops), not an intake page. Re-derive the floor from `fired_24h` mechanically (the SKILL already asks for this; make it computed). |
| **RC5** | Supply is mis-shaped. Authored units default to `cpu_light` on the saturated runner, while GPU/Kaggle-CPU/trainer starve. Infeasible recurring units consume slots. | § 2d; § 2a secondary | (i) The authoring-queue ranking gets a bonus for nodes whose compute class lands on an idle backend. (ii) The grader marks a recurring e35 unit `power: infeasible` when `base_oos_trades` < floor/3 and the unit is daily-bar, and stretches it to a quarterly cadence. It is **not** killed: refinement-first. That frees runner slots. (iii) `PLANNING.md` § 1b gains one line: say why a unit cannot use `published_data` (Kaggle CPU) before choosing `cpu_light`. |
| **RC6** | Research-ops is scoped as a ~$5 reader. Mechanical work (closing units) is left to it by hand; judgement work (triage, authoring) is crowded out. | § 3 | **Update the SKILL:** grading becomes mechanical (RC1), so passes drop the hand-close step. Their budget goes to (1) needs_review triage, (2) authoring from the programme authoring queue (cap raised for nodes with a spec template), (3) the computed floor. Record `authoring_queue_depth` in the run record. |

**What this is not.** It is not a new dispatcher, scheduler or cron: the dispatcher, router, collector, grader and refill stay as they are and are extended. It changes no schedule. The replenish cron (daily 05:50Z, also triggered by the health step) is enough once the health step triggers it on unlocked programme nodes, not only on template points. If a cadence change turns out to be needed (for example, refill after every grade), it is a workflow change and goes as a hold PR. It is not a runner-cap raise (RESEARCH-CAPS measured the pool). It is not a second grading of the current pile (RESEARCH-GRADE-BACKLOG owns that).

**Is a "programme driver" genuinely missing?** Yes, as a step, not as a service. The step that reads a plan question, checks its prerequisites' verdicts and emits the next pre-registered unit does not exist anywhere (§ 2b, § 2e). It belongs inside the existing refill producer, because that producer already has the determinism and E58 self-land path the step needs. Building it as a separate service would duplicate stamping, landing and id-minting, and invite a second writer.

**Build order (Phase 2, after manager confirmation):** RC1 → RC5(ii) → RC2 + RC3 (schema + replenish source + STATE writer + the two first sidecars from 01 and 02, the plans with the most W2+ prose) → RC4 → RC6 skill update. All Tier-1 (research tooling, docs, skills, CI scripts). Any `.github/workflows/*` cadence edit is a hold PR.

---

## 5. Reproduce

```bash
python3 scripts/ci/check_research_queue_throughput.py --json
python3 scripts/research/queue_grade.py --json ; python3 scripts/research/queue_grade.py --health
python3 scripts/research/queue_replenish.py --target 25          # dry run: candidates per family
python3 scripts/research/dispatch_queue.py | grep would_dispatch  # due vs capacity
python3 scripts/research/research_utilization.py --week
python3 scripts/research/research_disposition.py --unread-only
# hand vs auto population (§ 2a): load each research/queue/*.yaml with status queued + last_dispatched_at;
# auto = generated: dict or grading.auto is True; rows = queue_grade.e5_rows(root, uid, None)
```
