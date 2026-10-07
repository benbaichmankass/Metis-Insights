# research/queue — planning and intake rules (binding)

Short on purpose. The mechanics (themes, fair share, aging) are in [`README.md`](README.md) § Scheduling and the
weights in [`../THEMES.yaml`](../THEMES.yaml). This file says how work gets in, how weights move, and what the
recurring planning session does.

## 1. An idea becomes a unit
- The operator's ideas land in **`docs/claude/work/IDEAS.md`**, the general intake the manager syncs daily from the
  operator's Google Doc. That file is the manager's; the planning session reads it and does not create or reshape it.
  Each row there is triaged to a `research/queue` unit (a question) or a checklist row (a build). This file
  is only the research half.
- **The manager drafts the unit.** `theme` and `priority` are required (the validator refuses a queued unit
  without them). **A decision rule is registered before any run** (`decision_rule.registered_before_run: true`,
  committed in the same PR that adds the unit); `check_research_queue_decision_rule.py` enforces it.
- **An idea too vague to register gets a SCOPING unit, not a drop.** If it cannot yet state a statistic, a bar
  and a minimum n, queue a unit whose question is "what could we measure, at what n, and what would it cost",
  with its own rule. A dropped idea is a finding nobody can re-ask.
- Every unit says what it does not do. An upper-bound or scoping unit says it is not a promotion case.
- Units that need a prior result to be worth running use `requires_result`, not prose.

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
