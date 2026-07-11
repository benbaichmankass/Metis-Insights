# M20 — Exit Refinement — session prompt

> Paste the block below into a fresh session to run the M20 Exit Refinement
> work. It is the operator-directed #1 next-strategy-development priority: lift
> net PnL through more accurate exit timing. **Not greenfield** — two observe-only
> exit shadow-soaks are already accruing on the live trader; the session's job is
> to read them, resolve their counterfactual exits against realized price, and —
> only if the evidence clears the gate — propose the Tier-3 graduation.
>
> Prereq before starting: confirm the soaks have accrued enough rows (see Step 1).
> If they're still thin, the session's deliverable is a "not enough data yet, come
> back on <date>" note, not a forced conclusion.

---

## Session prompt

**Objective.** Determine whether the bot can increase net-of-cost PnL by exiting
trades more accurately, and if so, propose the exact Tier-3 change. The two
candidate mechanisms are already instrumented observe-only; this session turns
their soak data into a verdict.

**Read first (canonical + context):**
- `docs/CLAUDE-RULES-CANONICAL.md` (operating rules) + the ROADMAP.md **M20** row.
- The live-trade-management / PnL-resolution contract:
  `docs/audits/live-trade-management-contract-2026-06-16.md` — exits must always
  mean "broker-confirmed flat", and real/paper/prop never blend.
- The two soak designs + the honest-negative that motivates the live test:
  - ExitPlan ladder: `docs/sprint-logs/S-DTP-EXITPLAN-2026-06-17.md`, backlog `PB-20260617-002`.
  - fc-geometry: the M19 T0.4 row in ROADMAP.md + `docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md`, backlog `MB-20260705-FC-SLTP-GEOMETRY`.

**Inputs (pull them yourself via the diag relays — do not ask the operator):**
1. **`exit_ladder_soak`** — `GET /api/bot/exit-ladder/soak?limit=...` (also
   `/api/diag/log_file?name=exit_ladder_soak`). One row per executed order: the
   materialized laddered exit (partial-TP rungs + final + stop) vs the single flat
   SL/TP actually placed (`differs_from_single_target`). Split by venue (api/prop).
2. **`fc_geometry_soak`** — `GET /api/bot/fc-geometry/soak?limit=...`. One row per
   live opening order: placed SL/TP + the decision-time quantile-forecast snapshot
   (`fc_*`), with `fc_present` as the honest coverage denominator. The censoring-aware
   counterfactual resolution runs trainer-side (`scripts/ml/fc_geometry_resolve.py`).
3. Realized outcomes: `/api/bot/trades/closed` + `/api/bot/order-packages` +
   candles for the counterfactual bar-path resolution.

**Steps:**
1. **Data-sufficiency gate.** Count soak rows per venue/symbol and how many have a
   resolvable realized outcome. If either soak is too thin to conclude (few
   differing rows, or fc coverage near zero), STOP with a dated "re-check" note —
   do not overfit a verdict onto a handful of trades.
2. **Resolve counterfactual exits** against the realized bar path for each soaked
   order: what would the laddered exit / fc-scaled SL/TP have returned vs the flat
   SL/TP actually placed? Use the trainer-side censoring-aware resolver for
   fc-geometry; be explicit about censored (partially-identified) exits.
3. **Score net-of-cost.** Aggregate per mechanism: net-R / net-$ (fees + funding
   in), win rate, maxDD, hold time — **under each account's ruleset** (prop →
   cost-aware EV + survival via `run_ev_montecarlo`; standard → net-of-fee). The
   flat SL/TP actually placed is the baseline.
4. **Classic exit-timing levers** (secondary, harness-backed): trailing-stop
   geometry, partial-TP rung placement, and time-stops. Reuse the
   S-RESEARCH-FRAMEWORK / S-RECOMB harnesses (`scripts/ops/research_sweep.py`,
   `recombination_sweep.py`) rather than bespoke code; report Δnet-R per lever.
5. **Verdict + proposal.** If a mechanism beats flat SL/TP on net-of-cost PnL AND
   maxDD, OOS ≥ in-sample, propose the exact Tier-3 change (which cells, which
   venue, the `*_MODE` graduation flag, rollback = off). If not, record the honest
   negative and the re-check trigger.

**Guardrails / tiering.** All analysis is Tier-1 (reads + research). Graduating any
live exit change is **Tier-3** — propose the exact diff, operator-approve before
merge, ship behind a `*_MODE` flag (`off`/`annotate`/`apply`), never a default-off
`*_ENABLED` gate. Do not change `src/`, `config/`, or any live-path file to
"just try it" — the soak IS the test.

**Deliverables.** A sprint log under `docs/sprint-logs/` (per
`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`), a research memo under `docs/research/`
with the per-mechanism net-of-cost table, any Tier-3 proposal (draft PR, marked
draft, pinged), and a ROADMAP.md M20 status update. Run `doc-freshness` at the end.
