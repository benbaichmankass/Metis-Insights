# Recurring validations audit — 2026-09-27 (lane W6-OPS-R)

> Written 2026-09-27 by lane W6-OPS-R
> (dispatched by manager session_01Ljhs6sFAdWHdMDhJpL5aBP). Operator ask, same day:
> *"the ops lane needs to investigate and fix the misfiring recurring validations
> (demotions checks, performance reviews, eg.)"*. Work is tracked by the pipeline
> items named in § 4, not by this memo.

## 1. How this was measured (read before the table)

- **GitHub scheduled workflows:** every file under `.github/workflows/` with an
  `on.schedule` (22 files, parsed from the tree at `origin/main` 2026-09-27).
  Runs read from the Actions API, `event=schedule`, **created ≥ 2026-09-20T08:59Z**
  (7 days before the read at 2026-09-27T08:59Z).
  - *expected* = cron slots in that window **since the file existed** (git log).
  - *lag* = `created_at` minus the most recent slot ≤ `created_at`. For crons
    that fire more often than the lag (hourly / 2-hourly) this is capped by the
    interval and understates the lateness; those rows are graded on the
    *observed/expected* ratio instead.
  - *last success* = newest `event=schedule` run with `conclusion=success` (any age).
- **VM timers:** live trader via the `vm-diag-snapshot` relay, `GET /api/diag/timers`
  (issue #13106, captured 2026-09-27T09:18:25Z, 17 allowlisted timers, 0
  could-not-look). Trainer via `trainer-vm-diag` (issue #13107,
  `systemctl list-timers` + 8 days of `ict-promotion-readiness` journal).
- **The three review sessions** (`/performance-review`, `/ml-review`,
  `/health-review`): `docs/claude/work/SCHEDULE.json` declares each
  **"on demand — no declared fixed interval"**. Nothing in the repo or on either
  VM is supposed to run them on a cadence. What runs on a cadence is the set of
  *producers* they read (health-snapshot, promotion-readiness, the insights
  generators, strategy-review-packets), which are graded below.

Verdicts: **OK** · **LATE** (runs, well off its slot) · **FAILING** (runs, red) ·
**SILENT** (slots with no run, or output that never lands) · **NOISY**.
A row can carry two.

## 2. Findings that explain most of the table

### F1 — GitHub creates this repo's scheduled runs ~4–6 h late, since 2026-08-27 (SYSTEMIC)

MEASURED (Actions API, every `event=schedule` run of `health-snapshot.yml` and
`diag-relay-sweep.yml`, 2026-07-29..2026-09-26): diag-relay-sweep (`0 6 * * *`)
lagged 23–75 min every day 08-07..08-26, then **670 min on 08-27** and 223–377 min
every day since. health-snapshot shows the same step on the same day, with a
time-of-day shape (00Z slot ~170 min, 06Z ~260, 12Z ~300, 18Z ~240).

What it is **not**, as far as this repo can see: the count of scheduled
workflows was **12 on both sides of the step** (2026-08-25 and 2026-08-28,
counted from the tree at each commit), and the governance-cron retirement on
2026-09-21 (28 → 18 scheduled files) did not move the lag. It is not a
concurrency queue either: `created_at` is stamped by the scheduler before any
concurrency group applies. **Why GitHub's scheduler did this is NOT established**
— nothing in YAML schedules fixes it.

Hourly crons are also **dropped**, not just delayed: dashboard-edge-watch 15 of
~67 slots since it landed 09-24 (22%), main-tree-watch 39/168 (23%),
stale-automation-sweep 32 of ~72 since its 09-21 restore (44%).

The VM timers are **on time** (trainer promotion-readiness finished 04:05–04:25Z
on all 8 days 09-20..09-27; `ict-research-results-gate` fired 07:14:36 for 07:12),
which is the control: the lateness is GitHub's, not the boxes'.

**Fix (landed in this lane's PR): `schedule-keeper.yml` +
`scripts/ops/schedule_keeper.py`.** It triggers on **push to main** (dozens a day,
not subject to the scheduler) plus an hourly floor, and `workflow_dispatch`es each
critical validator once its slot is >30 min old with no run since. Each target
gets a `dedupe` job that skips the late scheduled run of a slot the keeper already
ran (only keeper-made dispatches count; a human dry-run dispatch never suppresses
a scheduled run). The 6-hourly keeper run **pages** when a target has no completed
run inside its window — which closes the gap that a *dropped* slot was invisible
(`check_cadence_liveness.py` declares r4-demotion-gate `receipt: None` and relies
on claude-run-failure-alert, which only sees runs that exist).

### F2 — automation output strands: 53 of 77 open PRs are `automation/*` landings

MEASURED 2026-09-27 ~09:20Z (open PR list). Producers post their output through
`commit-to-main` → an auto-merge PR. When `guards`/`pytest-run` is red **on main**
at that moment, or the payload itself fails a check, the PR outlives its
producer's 30-minute wait. `stale-automation-sweep` then refreshes only what it
can prove is safe and reports the rest "for a human read" — and no human read is
scheduled. Three from the last 24 h, all green producer runs whose output did not
land:

| PR | producer | why stuck | content at risk |
|---|---|---|---|
| #12979 | trainer-capture-watch 09-26 05:10 | `guards` red (cadence-liveness STALE on research-loss-detector — itself caused by F1 drops + its own set -e bug — plus strategy-coverage) | a receipt since superseded by the 09-27 05:27 run |
| #12989 | research-queue-dispatch 09-26 11:34 | its own stamp broke `test_w4_dispatcher_dry_run_selects_the_unit` (test since fixed on main, #13009/#13003); now `dirty` | superseded: stamps landed via #13003 (`last_dispatched_at 2026-09-26T16:10:46`) |
| #12996 | macro-valuation-snapshot 09-26 12:17 | `guards` red on base (mandate-autoland self-test `.git` race + strategy-coverage) | **9 append-only rows in `comms/macro/valuation_snapshots.jsonl` not on main** |

Also: guards on `main` itself went red 4 times between 07:28Z and 08:37Z on
09-27 (runs 36303185181..36306771191) — every automation PR opened in such a
window strands.

### F3 — `issues: [opened]` fan-out (NOISY)

MEASURED: **106 of 149** workflow files trigger on `issues`. Every issue opened
(diag relay, system-action, prop report) creates up to 106 runs, all but one
`skipped`. Of the most recent 100 `event=issues` runs (created ≥ 09-26), **98
were `skipped`, 2 `success`**; the API caps `total_count` at 2,500 for the same
query, so the true volume is **at least** 2,500 in ~33 h. A skipped job takes no
runner, so the cost is run-list noise (it buries scheduled runs — replay-pregate
has 4,539 runs) and API/event churn, not runner minutes. Whether it slows the
scheduler (F1) is **NOT established** — the step on 08-27 does not line up with
any change in issue volume that this lane could measure.

## 3. The table

| # | validation | expected | observed (7 d) | last success | output landed? | verdict | root cause | fix |
|---|---|---|---|---|---|---|---|---|
| 1 | **r4-demotion-gate.yml** (MD-DEMOTE-S2-S1, ARMED) | daily 07:41Z (2 slots since it landed 09-25 20:14Z) | 1 run: 09-26 **12:25Z** (+284 min); 09-27 slot **no run at 08:59Z** | 09-26 12:25Z | n/a on no-FIRE (step summary only) | **LATE + SILENT-risk** | F1. A dropped slot was undetectable (receipt: None; failure alert sees only runs that exist) | keeper dispatch + dedupe job + 26 h silence page (this PR). Decision logic untouched — see § 5 |
| 2 | **replay-pregate-nightly.yml** (E6(b) 22/22) | daily 04:00Z | 7/7 runs, +5.2 h median; **7/7 failure** (14/14 back to 09-13) | none in the API window read | 09-17..09-24: landing PR never merged; 09-25/26: `.partial.json` landed | **FAILING + LATE** | The 38-min poll ended with the driver still running on **10/10** runs 09-17..26 (10–15 of 22 heads graded; worst 3.8 min/head → ~84 min needed). Slowness = trainer memory pressure (5568/5909 MB used, 4 GB swap, 09-26) — not a transport drop, not a crash | poll 38→100 min, job 80→145 min; keeper (this PR). Memory cause filed |
| 3 | **strategy-review-packets.yml** | daily 04:40Z (packets ready before the operator wakes) | 7/7, +4.9 h median (~09:30Z) | 09-26 09:30Z | yes (runs succeed) | **LATE** | F1 | keeper + dedupe (this PR) |
| 4 | **soak-book-grade-weekly.yml** (R5) | Sun 03:00Z (1 slot since it landed 09-26 23:19Z) | 0 runs by 08:59Z | never | — | **SILENT (so far)** | F1; first slot pending | keeper + dedupe; 170 h silence page (this PR) |
| 5 | **trainer-capture-watch.yml** | every 6 h at :23 | 27/28, +4.7 h median; 1 failure (09-26 landing) | 09-27 05:27Z | #12979 stranded (F2) | **LATE + FAILING-silently** | F1; **and the page could never fire**: `set -uo pipefail` does not clear the runner's `-e`, so grader exit 1/2 aborted the step before `rc`/`capture_bad`/`controls_broken` were written (PI-20260926-P64CTNIO-0001) | scoped `set +e` (this PR) |
| 6 | **research-loss-detector.yml** (E57) | every 12 h at :23 (5 slots since it landed 09-24) | 5/5, +4.8 h median; 3 failure | 09-27 05:27Z | receipt landed after its own set -e fix | **LATE; alarm correct** | the 3 failures were the detector *correctly* reporting 14 failed `research-exit-head-build.yml` runs of 09-24; F1 lag | none here (F1 via a later keeper target if wanted) |
| 7 | **research-queue-dispatch.yml** | daily 06:20Z | 7/7, +5.6 h median; **4 cancelled** | 09-25 11:59Z | 09-26 stamps stranded in #12989 (landed instead via #13003) | **FAILING (fixed upstream) + LATE** | the stamp PR broke a test that assumed un-stamped units → never merges → job hit its 35-min timeout (`cancelled`). Test fixed on main 09-26 | none needed now; #12989 needs closing (F2) |
| 8 | **macro-valuation-snapshot.yml** | daily 07:30Z | 7/7, +5.3 h median; 1 failure | 09-25 12:50Z | **#12996 stranded with 9 rows not on main** | **SILENT output + LATE** | F2 (red base) + F1 | #12996 needs a refresh/merge (F2 item) |
| 9 | health-snapshot.yml | every 6 h | 27/28, +4.2 h median | 09-27 04:35Z | yes | **LATE** | F1 | VM timer `ict-health-snapshot` (15 min) is on time (last 09:13:11Z) — the in-box view is current |
| 10 | macro-producer-liveness.yml | daily 12:00Z | 7/7, +4.7 h | 09-26 16:09Z | yes | **LATE** | F1 | none (48 h window tolerates it) |
| 11 | diag-relay-sweep.yml | daily 06:00Z | 7/7, +4.8 h | 09-26 10:33Z | yes | **LATE** | F1 | none |
| 12 | broker-bracket-reconcile.yml | every 6 h | 27/28, +4.7 h; 2 failure (09-21/22) | 09-27 05:40Z | yes | **LATE** | F1; failures are 5 days old | none |
| 13 | alpaca-settlement-soak-watch.yml | weekdays 14:30Z | 5/5, +4.1 h | 09-25 18:49Z | yes | **LATE** | F1 | none |
| 14 | main-tree-watch.yml | hourly | **39/168** (23%); 6 failure (09-21/22) | 09-27 08:31Z | yes | **SILENT (drops)** | F1 drops | none; keeper candidate if hourly matters |
| 15 | dashboard-edge-watch.yml | hourly | **15 of ~67** since 09-24 (22%) | 09-27 08:02Z | yes | **SILENT (drops)** | F1 drops | none; keeper candidate |
| 16 | stale-automation-sweep.yml | every 2 h | **32 of ~72** since 09-21 (44%) | 09-27 08:16Z | reports, refuses unsafe refreshes | **SILENT (drops) + gap** | F1; and F2 — its "report for a human read" has no reader | F2 item |
| 17 | econ-calendar-produce.yml | daily 22:30Z | 7/7, +2.3 h | 09-27 00:48Z | #12356 stranded since 09-17 | **LATE** | F1 (smaller at night) | F2 |
| 18 | econ-event-study.yml | Sun 23:10Z | 1/1, +1.9 h | 09-21 01:02Z | yes | OK (lag tolerable) | — | — |
| 19 | purge-artifacts.yml | daily 03:00Z | 6/7, +5.4 h | 09-26 08:22Z | n/a | **LATE** | F1 | none |
| 20 | doc-audit-weekly / oci-inventory / research-backtest-augment | weekly Mon | 1/1 each, +5.5–6.4 h | 09-21 | yes | **LATE** | F1 | none |
| 21 | 106 workflows on `issues: [opened]` | — | ≥2,500 runs / ~33 h, 98% skipped | — | — | **NOISY** | F3 | proposal filed (F3 item) |
| 22 | VM `ict-promotion-readiness` (trainer; /ml-review producer) | daily 04:00Z | 8/8 days, finished 04:05–04:25Z | 09-27 04:12Z | on the trainer (not re-verified to the SPA this session) | **OK** | — | — |
| 23 | VM `ict-drift-retrain` (trainer) | hourly | 10 finishes 00:00–09:00Z 09-27 (01Z and 05Z absent) | 09-27 09:00Z | — | OK (the two absent hours not investigated) | — | — |
| 24 | VM `ict-research-results-gate` (trader) | daily 07:12Z | fired 09-27 07:14:36Z | 09-27 | not re-verified | **OK** | — | — |
| 25 | VM `ict-insights-generator-strategies` (M13 per-strategy analyst; /performance-review input) | every 120 min (monotonic) | last trigger **05:14:11Z**; next elapse ≈10:58Z — i.e. re-armed ≈08:58Z **without firing** | 05:14Z | not checked | **SILENT-risk (inferred)** | INFERRED from next_elapse arithmetic: the timer was re-armed ~08:58Z without a trigger; a restart of monotonic timers on deploy would starve a 2 h timer on a busy deploy day. NOT established | Tier-2 → filed (read `journalctl -u` for the timer, then decide) |
| 26 | VM `ict-db-integrity` (trader) | every 60 min (monotonic) | last trigger **06:13:42Z** at 09:18Z (≥2 slots missed); re-armed ≈08:57Z | 06:13Z | — | **SILENT-risk (inferred)** | same as #25 | same item |
| 27 | VM `ict-ib-gateway-watchdog` | 5 min | `inactive`, no schedule | — | — | UNKNOWN (possibly intended: gateway moved to its own box) | not checked | same item asks the question |
| 28 | `/performance-review`, `/ml-review`, `/health-review` sessions | **on demand** (SCHEDULE.json) | n/a | n/a | n/a | not a cadence | nothing is supposed to schedule them | none — if the operator wants a cadence that is a decision, not a fix |

**Verdict counts (28 rows; a row with two verdicts counts once, under the first):
LATE 12 · SILENT 7 · OK 4 · FAILING 2 · NOISY 1 · UNKNOWN 1 · not-a-cadence 1** (12+7+4+2+1+1+1 = 28).

## 4. What landed, what is held, what needs an observation

- **This lane's PR** (Tier-1, self-land): schedule-keeper + dedupe on 4 targets;
  replay-pregate budget; trainer-capture-watch `set +e`; catalog + cadence +
  failure-alert registrations; `tests/test_schedule_keeper.py`.
- **Tier-2 held:** nothing opened — the only VM-side suspicion (#25/#26) is
  INFERRED, and the first step is a read, filed below.
- **Pipeline items** (under `docs/claude/work/pipeline/`, all `queued`,
  landed-unproven where a fix landed): keeper observation; pregate 22/22
  observation; trainer-capture page observation (update of P64CTNIO-0001);
  stranded automation PRs (F2); issues fan-out (F3); monotonic VM timers (#25–27).

## 5. Exactly what changed in r4-demotion-gate.yml

1. A new first job `dedupe` (`if: github.event_name == 'schedule'`, permissions
   `actions: read, contents: read`) that runs `schedule_keeper.py dedupe` and
   outputs `skip`.
2. The `gate` job gained `needs: dedupe` and
   `if: ${{ !cancelled() && needs.dedupe.outputs.skip != 'true' }}` — fail-open:
   if `dedupe` errors (its step is `continue-on-error`) or is skipped
   (manual dispatch), `gate` runs exactly as before.

Nothing else: the cron (`41 7 * * *`), the `window`/`apply` inputs and their
defaults, the perf fetch, `scripts/ops/r4_demotion_gate.py`, the PR/landing/
mandate steps and `check_mandate_autoland.py` are byte-identical. The keeper
dispatches it with its default inputs (`window=30d`, `apply=true`), which is what
the scheduled run uses.
