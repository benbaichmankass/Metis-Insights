# Sprint Log: S-M30-BACKTEST-FIRST-20260727

## Date Range
2026-07-27 (single session).

## Objective
Execute the operator's M30 re-scope to **backtest-first discovery**: stop
discovering on the row-starved ~376-row live journal (where Studies 1–5 all
nulled under the M18 coin-flip prior) and build the bridge that runs discovery on
the **backtest engine** (large N + native candle path). Two tasks: **(1)** build
"C1-for-backtests" — bridge a backtest harness into the existing C2 stats
machinery; **(2)** audit why the L3 paper eval cohort was implausibly small.

## Tier
Tier-1 throughout — observe-only research tooling + docs. No order path, no
`config/`, no live behavior, no VM mutation. One additive, default-off hook on a
backtest harness (behavior-neutral).

## Starting Context
The prior session (`claude/m30-deep-quant-research-y9uwlh`) posted a board
CORRECTION superseding the live-journal studies. Ledger Studies 0–4 were all
null/leads; the binding constraints were decision-time feature **density**
(block-sparse; the dominant book carried only 2 graded feats — Study 2) and the
~376-row real-book wall. The C1 panel builder, C2 analyzer, P2 sweep driver, and
the pure `excursions.py` MFE/MAE math were all merged.

## Repo State Checked
`main` @ `d8426c3` at start; branch `claude/m30-backtest-first-discovery-lzapv4`.
Coordination board #6927 read; `▶️ START` posted before first change. No
concurrent session on the research toolchain.

## Files and Systems Inspected
- `scripts/research/{build_research_panel,build_exit_panel,analyze_research_panel}.py`
  (the C1/C2 contract), `src/research/{component_vector,excursions}.py`.
- The backtest harnesses (mapped via a sub-agent): `scripts/backtest_system.py`,
  `scripts/backtest_{trend,fade,squeeze,ict_scalp}.py`,
  `src/backtest/run_backtest_vwap.py`, `src/backtest/backtester.py`,
  `scripts/ops/m15_ws_b_fold_report.py`. **Finding:** feature-richness depends on
  whether a harness calls the **live signal builder** — `ict_scalp` (live
  `order_package`, carries full `meta` + `entry_index`/`exit_index` on each
  `Trade`) and `backtest_system` (live, but drops meta at close) are C2-gold; the
  inline-entry harnesses (trend/fade/squeeze) are feature-poor.
- The L3 paper-eval population (`ml/datasets/families/setup_candidates.py`,
  commit `52d51df`) + the `trades` schema + `config/accounts.yaml` paper accounts.

## Work Completed
- **Task 1 — `scripts/research/build_backtest_panel.py` (new bridge).** Runs a
  backtest harness in-process and emits, per simulated trade, the **same C1
  schema** the journal panel produces: decision-time features via
  `component_vector.extract` + outcomes `win`/`r` + **native MFE/MAE/giveback**
  via `excursions.compute_excursions` over the in-memory candle path. Feeds the
  **existing** C2 analyzer unchanged (purged WF-CV + BH-FDR). Harness-adapter
  architecture; flagship **ict_scalp** adapter implemented (9 feature cols vs the
  journal's 2 — Study 2's feature-capture-breadth constraint solved on this
  substrate). One additive, default-off `return_trades` hook on
  `scripts/backtest_ict_scalp.py` exposes the full `Trade` objects in-process.
  7 new offline unit tests + the 51 existing ict_scalp/component/excursion tests
  pass; ruff clean. Merged **#7744** (`7df3146`).
- **Task 2 — L3 paper-ledger volume audit** (trainer-vm-diag relay #7743).
  Root cause: **not a filter bug**. The paper cohort (~219 clean) is bounded by
  genuine soak-book **closed-trade yield** — the full-roster soak generates mostly
  `rejected`/`exchange_rejected` order attempts (2594+380 across the DB) that
  never open. L3 admits all soak books as designed. Live+paper top out ~596 clean
  closed rows.
- **Study 7 — first powered backtest-substrate run** (relay #7747). ict_scalp on
  the real 647k-bar 5m BTCUSDT feed, recent 150k slice → **282 simulated trades**.
  The multivariate + OOS pass **computes** (the journal never could). **Clean
  NULL:** no FDR survivor; OOS `win` AUC 0.44 (< chance), `giveback_r` R² −0.177,
  `r` R² −0.063 — all fail. Confirms the coin-flip-entry prior on backtest volume.
  Merged **#7748**.
- **Ledger** (`docs/research/technical-quant-research-ledger.md`): Studies 5
  (superseded), 6 (platform built), 7 (powered null) + the Task-2 audit.

## Validation Performed
- Bridge run end-to-end locally (5k sample → 4-trade panel) → C2 reads it
  `leakage clean:true, manifest_asserted:true`; both `--outcome win` and
  `--outcome giveback_r` run.
- Leakage verified: `component_vector.extract` reads only decision-time specs, so
  the outcome keys the harness stamps on `meta` (`mfe_r`/`exit_price`/`bars_held`)
  can never enter a `feat_` column (unit-tested `feat_mfe_r`/`feat_exit_price`
  absent). WF-CV orders by `closed_at` (exit ts) → respects backtest time order.
- Study 7 reproduced VM-side on the real feed: 282 rows / 9 feats / 100%
  excursion coverage, all three outcomes computed.
- `python scripts/ci/check_canonical_doc_coherence.py` → all checks pass.

## Documentation Updated
- `docs/research/technical-quant-research-ledger.md` — Studies 5/6/7 + Task-2 audit + queued next-turns.
- `ROADMAP.md` — M30 row updated (pivot + Studies 5–7, doc-freshness); **new milestone M36 — Consolidation & Integration** (row + phased section) encoding the operator's forward work plan.
- This sprint log.

## Contradictions or Drift Found
- The ROADMAP **M30 row was stale** (said "Studies 0/1/2", predating the pivot) —
  **fixed** this session. No other canonical contradiction found; the mechanical
  coherence scan passes.

## Risks and Follow-Ups
- Study 7's null is at N=282 (~1.4yr); the **exit-timing angle is untested at
  scale** — the immediate next run is the full 647k feed. (Not a risk to live —
  observe-only.)
- Relay hygiene: the trainer VM uses `python3`/`.venv/bin/python` (not `python`)
  and preempts in-progress requests — keep research relays bounded.

## Deferred Items
- M30: full-feed exit-timing study, per-regime-cell conditioning, `backtest_system`/vwap adapters, the C3 backtest→walk-forward bridge, P6 SHAP.
- All folded into **M36** (Track D) as the forward plan.

## Next Recommended Sprint
**M36 — Consolidation & Integration** (the operator's directed forward program):
deepen M26 (transition role), M27 (scalp+chop + GLD-into-Alpaca), merge M28⊕M29 +
the crowding/positioning overlay, and wire M30's outputs into the macro sleeves +
the M16 conviction master model. Handoff prompt authored for a fresh deep-research
session.

## Wrap-Up Check
- [x] All work merged to `main` (#7744, #7748).
- [x] Ledger + ROADMAP + sprint log updated; coherence scan green.
- [x] Coordination board `✅ DONE` posted.
- [x] No live-VM / config / order-path change.

## Addendum — session continuation (2026-07-27 pm): the binding rule + M36 C4 run

Two follow-on deliverables in the same session (PR #7758, branch
`claude/m30-backtest-first-discovery-lzapv4`):

1. **Codified "backtest history first" as a binding rule** (root-cause fix for the
   operator-flagged recurring "wait weeks for data to accrue" anti-pattern). New
   lead section in `docs/research/RESEARCH-RIGOR-STANDARD.md` (phantom-vs-genuine
   forward-soak classification test + M30/M28-P4 worked examples) + a binding
   pointer section in `docs/CLAUDE-RULES-CANONICAL.md`. Fixed the residual stale
   M28-P4 "blocked on unwired producer" bullet in `ROADMAP.md`. Swept the
   remaining accrual mentions → the exit/fc-geometry soaks + regime-head items are
   *genuine* forward soaks (already name their irreducible reason); no phantom
   candidates left.

2. **Built + ran M36 Track C · C4** — the conditioned-lifecycle backtest gate
   (`M36-C4-conditioned-lifecycle-run-2026-07-27.md`). New pure module
   `thesis_conditioned.py` (`conditioned_exit_on_path` drives the shipped C2/C3
   over the price path; exits only ever earlier than baseline → no look-ahead) +
   `equity_and_maxdd` + runner `scripts/macro/thesis_c4_run.py` (full grid, no
   in-sample cell selection) + 10 unit tests. **Ran on the committed 21yr history
   + real off-VM candles** (trainer relay #7760), 1,104 theses. **Verdict: NULL on
   net edge** — the conditioned lifecycle does not beat the value baseline
   (Δnet −0.0010…+0.0004), so **nothing graduates**; a modest ~19% maxDD reduction
   confirms the conditioner is safe + reductive but pointless without an
   edge-positive base thesis. **The blocker is M28 value-thesis construction, not
   the exit lifecycle** — re-run C4 once a construction beats its own P4 baseline.
   Scorecard `comms/macro/thesis_c4_scorecard.json`.
