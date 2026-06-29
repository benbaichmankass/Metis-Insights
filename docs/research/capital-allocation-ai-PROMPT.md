# Capital-Allocation AI — new-session spawn prompt

> **What this is.** A self-contained prompt to start a **research + proposal**
> session that designs the ML infrastructure for a **portfolio-level "AI capital
> allocator"**: a decision layer that sees the full set of candidate trades
> coming from ALL strategies/symbols/accounts on a given tick and allocates
> capital to the EV-optimal subset — fee/cost/funding-aware, risk- and
> correlation-aware — so money is always on the best available opportunity and
> never stranded on a worse trade when a better one exists.
>
> **Provenance.** Direct follow-on from the 2026-06-29 optimization
> investigation (Units A/B/C — pairwise flip EV, RiskManager-only sizing, prop
> exit banking; design docs merged in #4994). Those units improved the
> per-strategy / per-account capital-allocation primitives; this session levels
> up to the **portfolio (cross-strategy) allocation brain** that sits above them.
>
> **Discipline.** Tier-3, PROPOSE-ONLY. The output is a phased DESIGN doc + a
> ROADMAP entry, not live changes. Nothing graduates without backtests +
> operator approval. Paste the block below into a fresh session.

---

```
SESSION GOAL — Propose the ML infrastructure for a portfolio-level "AI capital
allocator": a decision layer that sees the full set of candidate trades coming
from ALL strategies/symbols/accounts on a given tick and allocates capital to
the EV-optimal subset — fee/cost/funding-aware, risk- and correlation-aware —
so money is always on the best available opportunity and never stranded on a
worse trade when a better one exists. This is a RESEARCH + PROPOSAL session:
produce a phased DESIGN doc + ROADMAP entry. Tier-3, PROPOSE-ONLY — no writes
to src/, config/, or any live-path file; nothing graduates without backtests +
operator approval.

START by reading (in order):
- docs/CLAUDE-RULES-CANONICAL.md (how you operate, permission tiers, Prime Directive)
- docs/ARCHITECTURE-CANONICAL.md (trade/comms pipeline, the intent → coordinator → risk → execute path)
- root CLAUDE.md (the regime/conviction/shadow env-var stack + the no-default-off-gate rule)
- The three optimization design docs this lineage just produced (merged #4994):
    docs/research/pnl-optimal-conflict-resolution-DESIGN.md   (Unit A — pairwise flip EV)
    docs/research/position-sizing-confidence-DESIGN.md         (Unit B — RiskManager-only sizing)
    docs/research/prop-dynamic-exits-faster-banking-DESIGN.md  (Unit C — exit banking)
- Then scan the skills catalog (skill-first lookup is binding): backtesting,
  new-strategy, ml-review, delegate-work, sprint-format.

THE PROBLEM (frame it precisely against the current code):
Today the pipeline is per-strategy / per-account INDEPENDENT. Each strategy
emits an order package; src/runtime/intent_multiplexer.py + Coordinator.
aggregate_intents / multi_account_execute (src/core/coordinator.py) net sides
and resolve same-symbol conflicts (FLIP_POLICY in src/runtime/intents.py); then
a per-account RiskManager.position_size (src/units/accounts/risk.py) sizes each
survivor on its own. There is NO global step that compares the full opportunity
set and asks "of everything actionable right now, which trades deserve the
limited capital / risk budget, ranked by net expected value?" Capital is
effectively allocated first-come and per-cell, not by comparative, cost-aware
EV across all candidates together.

PHASE 1 — INVENTORY what decision-capable ML/infra we ALREADY have, and what it
can and can't do for allocation:
- Regime heads (advisory, per-SYMBOL): regime_bar_scoring.py, regime/ml_vol_verdict.py,
  the candidate→shadow→advisory registry. (Gate signals, not EV rankers.)
- Conviction sizing (Design-B): src/runtime/conviction_sizing.py (c_strat / c_reg;
  currently off/annotate). The nearest thing to a per-trade conviction score.
- Pairwise flip EV: src/runtime/flip_ev.py (Unit A) — EV of one held vs one new,
  NOT N-way portfolio selection.
- Offline EV+survival: scripts/prop/account_compat_matrix.py → src/prop/montecarlo.py
  (run_ev_montecarlo) — cost-aware EV per (strategy, account), offline only.
- The training SUBSTRATE that already exists and is being logged live:
  order_packages.model_scores (per-model decision scores at signal time),
  account_context_snapshots (pre-decision equity/DD/open-count per order pkg),
  shadow_predictions.jsonl, and closed-trade realized PnL/R in trade_journal.db.
  → Assess whether these are sufficient to TRAIN a cross-strategy trade-quality
  ranker, and what's missing (labels, fee/funding fields, correlation inputs).

PHASE 2 — IDENTIFY the gaps for a real allocator:
- No N-way, capital-constrained selector (knapsack under margin + per-account
  risk-cap + daily-loss budget).
- No correlation/covariance-aware risk budgeting across simultaneous positions.
- Fees/funding/swap are modeled in backtests but are NOT a live per-decision input
  to selection/ranking.
- No learned "expected net-R given decision-time features" model ranking competing
  order packages head-to-head.

PHASE 3 — PROPOSE the build (phased, shadow-first, graduation-gated):
- The hook point: the Coordinator already assembles every candidate intent each
  tick — that's where an allocator observes the full opportunity set.
- A cost-aware EV scorer per candidate (start rules-based: confidence ×
  historical net expectancy − round-trip fees − funding/swap per cell; graduate
  to a learned ranker trained on the substrate above).
- A capital/risk allocator: given scored opportunities + free margin + per-account
  risk budget + correlation, select the best subset (greedy EV/risk → constrained
  optimization). Must respect existing caps and the Prime Directive (no auto-off,
  no default-off enable gate; observe-only until graduated).
- Datasets/features/targets for the learned ranker; which new models to train on
  the trainer VM and how they ride the candidate→shadow→advisory ladder.
- A SHADOW-SOAK plan: log what the allocator WOULD choose vs what the system did
  (like the conviction_sizing / exit_ladder soaks), measure regret (did we leave
  EV on the table?), then graduate with a backtest A/B.

DELIVERABLES:
1. docs/research/capital-allocation-ai-DESIGN.md — the phased design (inventory →
   gaps → proposed models/datasets/allocator → shadow-soak → graduation gates).
2. A ROADMAP.md entry for the program.
3. ml-review-backlog entries for the concrete model/experiment proposals.
4. Honest "what we have vs what we must build/collect" table; flag any data gap
   that blocks training before proposing the model that needs it.

CONSTRAINTS: propose-only (Tier-3). No live src/config changes. Pull any live
state you need yourself via the diag relays (don't ask the operator to fetch).
Every model influences orders ONLY at the advisory stage after operator-approved
promotion. Use delegate-work if the inventory/design fans out.
```
