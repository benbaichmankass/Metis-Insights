# research/queue — planning and intake rules (binding)

Short on purpose. The mechanics (themes, fair share, aging) are in [`README.md`](README.md) § Scheduling and the
weights in [`../THEMES.yaml`](../THEMES.yaml). This file says how work gets in, how weights move, and what the
recurring planning session does.

## 1. An idea becomes a unit
- The operator's ideas land in **`docs/claude/work/IDEAS.md`**, the general intake the manager syncs daily from the
  operator's Google Doc. That file is the manager's; the planning session reads it and does not create or reshape it.
  Each row there is triaged to a `research/queue` unit (a question) or a checklist row (a build). This file
  is only the research half.
- **Mint the id with `python3 scripts/research/next_rq_id.py --fetch`** (`RQ-YYYYMMDD-<h4>-NN`, session-hashed; never
  hand-pick a number or a `9xx` band). See `README.md` § Allocating a unit id.
- **The manager drafts the unit.** `theme` and `priority` are required (the validator refuses a queued unit
  without them). **A decision rule is registered before any run** (`decision_rule.registered_before_run: true`,
  committed in the same PR that adds the unit); `check_research_queue_decision_rule.py` enforces it.
- **An idea too vague to register gets a SCOPING unit, not a drop.** If it cannot yet state a statistic, a bar
  and a minimum n, queue a unit whose question is "what could we measure, at what n, and what would it cost",
  with its own rule. A dropped idea is a finding nobody can re-ask.
- **A FAIL branch closes the CONFIGURATION, never the family** (operator, 2026-10-16: "a few tests don't prove
  a line of thinking is wasteful -- it may mean we tested it wrong"). Write the FAIL disposition as what was
  tested and failed, plus the refinement that follows. Retiring a family needs a review record
  (`scripts/research/family_retirement_policy.yaml`, `research/family_reviews/`), and `family-retirement-guard` refuses
  family-closure wording on an added line that cites none.
- **Direction / multi-class units register a proper score** (IDEA-REVIEW-1016, 2026-10-16): on a 3-class direction
  label the class prior scores ~0.22 macro-F1 while a coin scores ~0.33, so "macro-F1 lift vs prior" can be pure
  chance. Register `logloss` (or `brier`) vs the prior; `ml_bakeoff.py` now records a `metric_caveats` line on any
  macro-F1/accuracy-vs-prior result with k >= 3 (the verdict is unchanged -- the rule is registered).
- Every unit says what it does not do. An upper-bound or scoping unit says it is not a promotion case.
- Units that need a prior result to be worth running use `requires_result`, not prose.

## 1b. A unit declares WHAT KIND of test it is and what it is WORTH — never WHERE it runs (RESEARCH-ROUTER, 2026-10-13)
Operator, 2026-10-13: Claude writes the unit (id + test structure); a dispatcher that spends no Claude tokens sends
it to the right compute and spends scarce resources on the highest-value work. The policy is data:
[`scripts/research/research_compute.yaml`](../../scripts/research/research_compute.yaml); the code is `scripts/research/compute_router.py`
(placement + value) called by `dispatch_queue.py`. **A session authoring a unit writes four things for this:**

```yaml
compute: cpu_light      # cpu_light | cpu_long | needs_vm_data | gpu | gpu_preferred | auto   (auto = derive from routing:)
routing: {needs_gpu: false, needs_trainer_resident_data: false, peak_memory_gb: 2.0, est_minutes: 20}   # as before
value:                  # optional; every field optional; omitted fields fall back to priority-derived defaults
  usd_at_stake_per_month: 400   # expected net-P&L impact / money at stake if the answer is acted on (state the basis)
  confidence: 0.4               # P(the result changes a decision), 0..1
  bucket: own_losses            # own_losses | structural | information | ai_trader | frontier  (STRATEGY-REALIGN mix 30/25/20/15/10)
  basis: "one line: where the dollar figure comes from"
run:
  command: ["python3", "scripts/research/<harness>.py", "--out", "{out_dir}"]
  inputs: {research_unit: RQ-...}      # no run.workflow: the router fills it
```

- **Classes:** `cpu_light` (<= ~30 min) and `cpu_long` (to 5.5 h) -> GitHub-hosted runner; `needs_vm_data` -> the
  trainer VM (only when the unit reads trainer-resident data and fits 6 GB); `gpu` -> Kaggle GPU (waits for the
  weekly quota, never runs on CPU); `gpu_preferred` -> Kaggle, else the runner (the script must support both).
  A class that contradicts the unit's own `routing:` block is refused (`BLOCKED_ROUTE`), not guessed.
- **`run.workflow:` still works and beats `compute:`** (every existing unit keeps its hard-coded runner). Do not
  add one to a new unit: picking a runner by hand is exactly what this replaces. RunPod (`gpu-burst-train.yml`) is
  never auto-routed; it stays an explicit, operator-armed, ledger-capped choice.
- **Ranking** (within a theme; theme weights stay the operator's across themes):
  `score = usd_per_month x confidence x bucket_boost x hand_written_boost x age_boost / ((1 + est_hours) x backend cost_weight)`.
  Every dry-run line prints its derivation. A unit waiting past `value.starve_hours` sorts first in its theme.
  `bucket_boost` = target share / observed share of the unit's bucket over the share window (clamped 0.5-2.0), which is how the 30/25/20/15/10
  mix steers fires. A dollar figure is an estimate with a stated basis, never a measurement.
- **Capacity** is per backend (`max_concurrent`) plus a total, and the Kaggle weekly GPU quota has a floor.
  A unit whose backend is full or out of quota is `deferred` with the reason and retried next cycle; it does not
  block units bound for other backends.

- **Published data (ML-DATA-OFF-TRAINER, 2026-10-13).** A unit whose `ml_bakeoff.dataset` paths are all in `scripts/research/market_data_export.py::ALLOW`
  (BTC/ETH/SOL 15m+1h `market_features`/`market_raw`, published daily as the private Kaggle dataset `metis-market-data`) declares
  `routing: {needs_trainer_resident_data: false, published_data: metis-market-data}` and `compute: auto`, with no `run.workflow`. The router CHECKS
  the claim (a path outside the allowlist is `BLOCKED_ROUTE`) and sends it to `research-script-run-data.yml` (runner + dataset fetch) or, with
  `needs_gpu`, to Kaggle with the dataset mounted. **Check `published-data.json` beside the result**: it carries each shard's `last_ts`; a shard that
  disagrees with its own metadata is withheld by the exporter and the unit fails closed (`ML-SHARD-TRUNCATED`: BTC/SOL 15m were cut short on 2026-10-13).
  Units that read OURS data (trade_outcomes, setup_labels, conviction_meta, execution_quality, account_context) still need `needs_vm_data`.
- **Say why a unit cannot take an idle backend before choosing `cpu_light`** (RESEARCH-LOOP-AUDIT RC5, 2026-10-15). MEASURED
  2026-10-15: the runner backend sat at 3/3 with 12 due units waiting while the Kaggle GPU had used 0.19 of 30 weekly hours and
  Kaggle CPU and the trainer had nothing due -- almost every authored unit declared `cpu_light`. When a unit's data is in
  `metis-market-data` (`published_data` -> Kaggle CPU) or is trainer-resident (`needs_vm_data`, idle windows 03-05Z/07-24Z),
  or its model is GPU-shaped, declare that class; if it must be `cpu_light`, say why in one line under `routing:`
  (`why_runner: ...`). `check_research_queue_health.py` prints the idle backends every dispatch cycle.
- **The trainer is its own windowed queue.** `backends.trainer.queue.windows_utc` (derived from the measured hour-of-day occupancy, `comms/cloud/trainer_workload_profile.json`)
  releases `needs_vm_data` units only into idle windows (03-05Z, 07-24Z) and only when `est_minutes x 1.5` fits before the window closes; otherwise `deferred` with the next opening.
- **RunPod is a ledger-capped fallback for `compute: gpu`**, after Kaggle, and only for a unit that declares `routing.runpod: {manifest, est_cost_usd}` (the pod trains a registered
  ML manifest, not a script). It opens the labelled `gpu-burst-train` issue; the $10/month ledger gate and `GPU_BURST_ARMED` still decide whether a pod launches.
- **Utilization is measured every fired cycle** (`scripts/research/research_utilization.py`, one file per run under
  `comms/research/utilization/`, landed with the liveness receipt): per backend capacity, in flight, fired, waiting and
  free slots, each with an explicit `read_state` (`measured` | `could_not_look` -- a backend we could not count is never
  reported idle); Kaggle GPU hours used / remaining / expiring before the weekly reset; GPU-burst $ month-to-date; the
  trainer VM's spare capacity when COMPUTE-UTIL-TELEMETRY has landed. `python3 scripts/research/research_utilization.py --week`
  is the Wednesday planning view: slot-cycles available vs used vs idle, value dispatched, flag counts.
- **Idle-capacity fill:** a recurring unit that sets `idle_fill: true` may be pulled forward when its backend has more
  free slots than units due for it (it sorts after every due unit). Set it only when an early re-run is harmless.
- **Thin floor:** when a backend has free slots and nothing due for it (or Kaggle GPU hours are about to expire with no
  gpu unit due) the dispatcher prints ONE `::warning::research-capacity floor_thin|kaggle_quota_expiring ...` and records
  it in the utilization file; research-ops step A reads it and treats it as top-up work (author units whose compute
  class lands on the idle backend).

## 2. Weights
- `research/THEMES.yaml` is the only place weights live. They are a share of fire slots, not an order.
- Set at the operator's direction (2026-09-30: regime 5, live_strategy 4, ml_health 3, new_strategy_prop 2,
  infra 2, macro 1). Changed by a PR that edits that one file and states the evidence for the change.
  2026-10-05, operator popup verbatim "Prop first (Recommended)": new_strategy_prop 2 -> 6, the single highest weight;
  nothing else moved (closes PI-20261005-MG7BGR46-0001).
- Revisited by the planning session each time it runs (section 4). Two rules it applies: the live pipeline
  outranks research breadth, and no single topic may starve the rest, so a weight never goes below 1 and a theme
  holding due work is checked against its fired count.

## 3. Aging
`aging_hours` (THEMES.yaml) lowers a waiting unit's effective priority one step per period. It is the
starvation backstop inside a theme; fair share is the backstop between themes. If a unit is old and still
unrun, read why (precondition, gate, route) before lowering anything.

## 4. What the recurring research-planning session does, every time
> **Routine cadence = the `research-ops` skill (2026-10-10).** Steps 1, 2 and 5 below (throughput, grade
> landed results, retire stale units) now run 4x/day as `.claude/skills/research-ops/SKILL.md`, a bounded
> lane the manager dispatches. This section remains the *weekly planning* session: re-weighting (step 3),
> drafting from `IDEAS.md` (step 4) and the one-page plan (step 6). Do not re-do what the last research-ops
> record (`comms/research/ops/`) already holds.

1. **Read throughput and results since the last session:** `python3 scripts/ci/check_research_queue_throughput.py --json`
   (fired 24 h / 7 d, results landed, runnable, idle hours) and the `research/results/` records landed since.
2. **Grade landed results** against their registered rules (`research_disposition.py --record`; never hand-edit
   the ledger). A graded-terminal `once` unit is set `done` with a reason.
3. **Re-weight themes** against the system-level plan: live pipeline over research breadth, no topic starving
   the rest. One PR to `research/THEMES.yaml` if the weights move.
4. **Draft new units** from the rows of `docs/claude/work/IDEAS.md` whose `routed_to` names a research unit or
   that are still `new` and research-shaped. Register the rule first (section 1).
5. **Retire stale units** with a stated reason (`status: retired`): superseded, answered elsewhere, or a
   precondition that can no longer be met. Closing a dead unit with a reason is worth more than carrying it.
6. **Write a one-page plan** (what ran, what landed, what the weights are and why, what is queued next, what is
   blocked and on what) that the manager brings to the operator. The page is the deliverable; the PRs above are
   its receipts.

## 5. Active geometry — the ranked plan (lane ACTIVE-GEOMETRY-PLAN, 2026-10-07)

**Operator framing (2026-10-06, PI-20261006-APBY4NTV-0003):** "active trade management" is
**active geometry** for ALL accounts — live, paper, mirror and prop. Both legs of the bracket
are managed state through the trade's life, conditioned on regime, volatility, time-in-trade
and the strategy's own thesis. Observed: the SL moves, the TPs never move; critical for shorter
timeframes. Canonical text: `docs/ARCHITECTURE-CANONICAL.md` § "TP doctrine" (clauses 2–3).

**MEASURED 2026-10-07** (diag `journal?table=trades` + `journal?table=order_packages`, pulled
~17:30Z; closed trades with `timestamp >= 2026-09-01` and a matching package; an amend is
`order_packages.sl`/`.tp` differing from the entry-time `exit_plan.stop.price`/`.final.price`
by more than 0.05%; the comparator is proven on the SL column, which finds 131 positives):

| class | closed trades | SL amended | TP amended |
|---|--:|--:|--:|
| real money (`bybit_2`, `alpaca_live`) | 32 | 4 | 0 |
| mirror (`bybit_portfolio`, `alpaca_portfolio`) | 52 | 16 | 0 |
| paper | 668 | 111 | 0 |
| prop | 0 closed in `trades` (20 rows since 2026-09-01, all `rejected`) | — | — |

Prop fills live in the prop journal, not the `trades` table; the `ict-prop-executor@tradeify_1`
and `@velotrade_1` journals (3,000 lines each, 2026-10-06 12:58Z → 2026-10-07) carry 0
`trail_amend` / `trail_skip` rows with the per-tick `{"trail": …}` line present on every tick
(0 positions held). The scan lane's 2026-10-06 figure (SL 126 of 734, TP 0) reproduces one day
later as 131 of 752 / 0 of 748. **Re-run the pull rather than quoting these numbers.** The 4
`squeeze_breakout_4h` closes carry no `exit_plan` and are excluded from the TP denominator.

**What the fleet can do mid-trade today, by class** (read from source 2026-10-07; `python3
scripts/ci/check_tp_doctrine.py --json` for the live grade — 0 of 52 routings compliant at
`142ec79e`, `tp_revision` fails on all 52 because no `monitor()` has ever returned `{"tp": …}`):

| class | SL side (exists) | TP side (exists) | TP side (missing) |
|---|---|---|---|
| real money + mirror (Bybit / Alpaca) | chandelier trail, trail_decay, stale_stop, giveback, vol_trail via `monitor()` → `{"sl"}`; amend path plumbed | entry-time `tp_r` (finite or 50R sentinel; clamped at the 9.9% venue cap); the `{"tp"}` verdict path is plumbed end-to-end (`monitor_verdict` → `order_monitor._apply_update` → `execute.modify_open_order`) | **a producer** (`target_expectation.evaluate_extension` is the pure decision half; no unit supplies `thesis_intact`); **a harness lever** (no `backtest_*.py` extends or re-prices a target) |
| prop (dxtrade browser / REST, phone) | `prop_trail.run_trail_step` amends the SL (dxtrade paths; latch/state unreadable — PI-20261006-KX6ZKFNA-0003); phone path: none (PI-…-0002); close-type levers alert-only (PI-…-0001) | entry-time `tp_r: 6.0` | `run_trail_step` passes `take_profit=None`; `dxtrade_api.modify_bracket` can PUT a TP; phone path has only a human-reported amend ticket (BUILD B2) |
| paper (incl. scalps) | as real money; scalps amend the SL often (72 of 192 closes) | `tp_at_r 1.5` fixed; ladder answered `honest_negative` ×6 (2026-08-10) | same producer + lever gap; scalp harness `not_capped_capable` (mi307) |

**INFERRED from `docs/research/mi307-offline-mfe-2026-09-18.json`** (uncapped harness MFE
distribution, per leg): the real-money "declared" targets mostly rest at the venue cap
(`xrp_pullback_2h` 3R reached 24.6% of 268 trades, clamp binds 78%; `trend_donchian_xrp_4h`
3R 15.8% of 146, clamp 70%; `ada_pullback_2h` 4R 24.1% of 253, clamp 71%), and the prop 6R
target is reached by 0.9% of 1,004 (`eth_prop`) and 1.9% of 370 (`sol_prop`) — a near-sentinel
the guard cannot see because it keys on 50R. Calibration-first, the predictive target on every
one of these legs sits at **2.0–2.5R**, which is why the revision lever (extend while the thesis
holds) is the question, not a nearer fixed target (E65 already showed nearer static brackets
lose the tail: `docs/research/prop-exit-evidence-2026-09-24.json`).

**The ranked plan.** Every TP-side revision question needs ONE harness build first; the
entry-time half is already queued (RQ-20261006-060/061, the monthly e35 template).

1. **GEOM-B1-HARNESS (build, Tier-1, first):** target-extension and retarget levers in
   `backtest_trend.py` / `backtest_pullback.py` / `backtest_ict_scalp.py` (decision half imported
   from `target_expectation.evaluate_extension`), `tp_extend` / `tp_retarget` lever columns in
   `m20_fleet_exit_sweep.py` + the coverage matrix, `levers`/`cells` inputs on the two sweep
   workflows. Unblocks all four units below.
2. **RQ-20261007-001** (real money, p1, blocked) — thesis-conditioned extension on `bybit_2`'s
   four legs, calibration-first then P2 gate.
3. **RQ-20261007-002** (prop, p1, blocked) — calibrated target + extension vs the 6R near-sentinel
   under the three prop rulesets; a COSTED-NULL names the EV cost of doctrine compliance for the
   operator to decide on.
4. **BUILD B1 producer** (Tier-2/3, `PI-20261006-5FUGHVX8-0001`) — `thesis_intact` per unit
   (`trend_donchian`, `htf_pullback_trend_2h`, `ict_scalp`) and a `{"tp"}` verdict, observe-only
   soak (`target_extension_soak`) before the flip; **BUILD B2** — prop TP amend on the dxtrade
   paths and a server-computed amend ticket on the phone path (lanes TP-DOCTRINE and
   PROP-TRAIL-PHONE own these; not duplicated here).
5. **RQ-20261007-003** (live roster, p2, blocked) — ATR-rescaled / stall-pulled target vs the
   entry-time one, calibration first.
6. **RQ-20261007-004** (short timeframes, p2, blocked) — the same on the ict_scalp 5m/15m family.
7. **GEOM-CENSUS (build, Tier-1):** commit the amend census above as a script with a weekly
   pipeline rerun, so "the TP never moves" stays a number after the producer ships.

Mirrors carry the real-money verdicts by roster equality (`tests/test_paper_portfolio_accounts.py`);
no separate units. New strategies: the geometry question (where does the move run out, what
revises it, what thesis extends it) is a required design output at wiring time — see
`.claude/skills/exit-refinement/SKILL.md` § "The TP doctrine binds every lever".
