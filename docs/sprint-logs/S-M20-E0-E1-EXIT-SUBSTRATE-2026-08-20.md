# Sprint Log: S-M20-E0-E1-EXIT-SUBSTRATE-2026-08-20

## Date Range
- Start: 2026-08-19 (continued session; the exit-mechanism thread opened 2026-08-18)
- End: 2026-08-20

## Objective
- **Primary goal:** stop sweeping lever cells and answer the prior question —
  *does this fleet have an exit mechanism at all, and is the decision surface
  capable of supporting one?* That is E0 and E1 of
  [`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md).
- **Secondary goals:** amend the process per the operator's correction on how a
  negative result is dispositioned; add the external-research step; leave E2 —
  the step that has never been run — actually runnable.

## Tier
- **Tier 1** throughout.
- Justification: docs, research tooling, backlog, one additive ML feature-schema
  change, and one observe-only config map. No `src/` order-path file, no
  `strategies.yaml` / `accounts.yaml` / `risk_caps.yaml`, no unit file, no VM
  mutation. The one config touched (`config/cross_asset.yaml`) is read only by an
  observe-only shadow-feature path that the import-linter contract already
  forbids from reaching Execution, and the change is **verified inert for live
  scoring**: `group_needs_cross_asset` computes only for a predictor trained on
  `xa_*`, and only the ETH head is — whose row is deliberately unchanged.

## Starting Context
- Active roadmap items: M20 (exit levers), M31 (position telemetry).
- Prior sprint reference: `docs/research/sol-pullback-stale8-deep-corpus-2026-08-18.md`;
  ~20 lever cells across the pullback family had returned zero shippable results.
- Known risks at start: the repeated `honest_negative` verdicts were being read as
  *"exits cannot be improved"*, which the operator explicitly rejected as a framing.

## Repo State Checked
- Branch/commit reviewed: `main` `5f9fddc` → `467da6d` over the sprint.
- Deployment state reviewed: live book via `/api/bot/positions`,
  `/api/diag/position_telemetry`, `/api/bot/notifications` (vm-diag #9991).
- Canonical docs reviewed: root `CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md`,
  `RESEARCH-CAPABILITY-INDEX.md`, coordination board #6927.

## Files and Systems Inspected
- **Code:** `src/research/intrabar_features.py`, `src/runtime/order_monitor.py`
  (close paths + `_classify_broker_exit`), `src/runtime/position_telemetry.py`,
  `src/runtime/cross_asset_live.py`, `ml/datasets/cross_asset_features.py`,
  `src/web/api/routers/strategies.py`, `scripts/backtest_pullback.py`,
  `scripts/research/build_intrabar_exit_panel.py`, `scripts/check_claim_basis.py`,
  `scripts/check_impossibility_claims.py`.
- **Config:** `config/cross_asset.yaml`, `config/strategies.yaml` (read).
- **Docs:** the process doc, `RESEARCH-CAPABILITY-INDEX.md`, the three backlogs.
- **Services/timers:** none mutated; read-only diag only.
- **Workflows:** `vm-diag-snapshot`, `trainer-vm-diag` (relays, read).

## Work Completed

### Process (the operator's correction)
Struck *"if E2 finds nothing, that is the answer"* and replaced it with **§3.1**, an
explicit regroup-and-widen disposition: check the substrate before blaming the
question; re-enter the survey; change the level; change the target; record the
conditions. Sizing/diversification is named a **parallel** track, not the fallback.

Added **E-lit** (§1.5), an external-research step that runs first and re-runs after
every negative, with a source-quality tier on every row so vendor-blog backtest
numbers are never quoted as evidence. It produced: the two exit-quality metrics
used below; the optimal-stopping lever FORM for E3 (a threshold in the posterior
over market state, with an age term); the observation that the trailing-stop
literature is **drawdown-side** and would fail our net_R-first `beats()` gate (now
an E4 agenda item); and the regime-dependence of correlation motivating E1.

### E0 — the census (#9990 `113741b`, #9997 `32c6a85`)
- `scripts/research/exit_census.py` (new, 39 self-tests): four never-collapsed exit
  classes with the **vocabulary discovered, not assumed**, plus MFE capture rate
  and MAE-to-stop.
- **Live half:** 1,226 closed non-backtest rows, 53 legs. **781 (63.7%) carry an
  `exit_reason` the producer itself declares it could not classify**; real money
  235/438 (53.7%). The live journal cannot say who decides exits.
- **Harness half:** 10,280 rows, 34 legs, **zero unclassified**. **52.4% of exits
  decided by a level or timer fixed at entry**; seven legs exceed the 70%
  falsifier. Of 4,890 path exits, **4,498 (92.0%) are `trail_stop`** — still
  endogenous, so §0.2's root cause survives measurement.
- **MFE capture:** median negative on 27 of 34 legs (9,859/10,280 rows, 95.9%);
  **zero** legs in the 65–80% band the literature calls healthy. Recorded with the
  caveat that capture is jointly determined by win rate and exit timing, so it is
  **not** an isolated exit verdict.

### E1 — the substrate (#9994 `c6fb891`, #10005 `467da6d`, #10006)
- **Step 1:** an absent peer is now *declared absent* — `xa_peer{n}_present` +
  `xa_breadth_present`, 13 → 16 columns, additive, no retrain. `xa_breadth_up = 0.0`
  previously meant *either* every peer fell *or* no peer had data.
- **Step 2:** peer map 1 → 5 symbols, every peer on a measured 90d rho.
  **`ETHUSDT` frozen** — the measurement says its row looks improvable and changing
  it (even reordering; slots are positional) would break live==train for a head
  already scoring.
- **Step 3:** the panel join. Calls the *same* pure function the live scorer calls,
  so the panel's features are ones a live accessor can produce.

## Validation Performed
- **Tests:** 39 self-tests (`exit_census`), 22 (`test_m30_exit_head`), 15
  (`test_cross_asset_features`), 11 (`test_cross_asset_live`).
- **Guards:** `diagnostic-provenance`, `artifact-validity`, `claim-basis`,
  `impossibility-claim`, `collapsed-state`, `silent-empty` — all clean at the end.
  Four of them **failed first and were right**, on my own work.
- **Planted-failure controls, each shown to fire before being trusted:** the
  census's alien-vocabulary refusal; the ETH-peers-frozen test (planted a
  *reorder*, the silent version); the measured-rho test (planted an intuited peer);
  the panel join's ts-alignment positive control and its disjoint-grid negative
  control.
- **Artifact verified end-to-end**, not just green CI: the panel builder run on
  3,000 aligned tz-aware 5m bars gave 142 rows, `row_coverage 1.0`, peer columns
  genuinely varying. **That run caught a defect before it landed** — an unsupplied
  peer slot emitted six constant-zero columns.
- **Transport verified:** the census tool's sha256 on the trainer matched the local
  file byte-for-byte and its self-tests passed *there* before the census ran.
- **Gaps not verified:** MAE-to-stop is unmeasurable on the current corpus; the
  harness census covers the flip-replay corpus only; live and harness populations
  are reported separately and never blended.

## Documentation Updated
- Rules/architecture/trade-pipeline docs: no change required (no pipeline stage touched).
- Roadmap: no row change — M20 substrate work under an existing milestone.
- Subsystem docs: `exit-mechanism-construction-PROCESS.md` (§1.5, §3.1),
  `RESEARCH-CAPABILITY-INDEX.md` (the census tool).
- New: `e0-exit-census-live-2026-08-20.md`, `e0-exit-census-harness-2026-08-20.md`,
  `e1-exogenous-panel-preconditions-2026-08-20.md`.

## Contradictions or Drift Found
1. `reconciler_filled` is the producer's own "could not classify" **and the modal
   value of the live book** — correctly implemented, never reported.
2. `/api/bot/strategies` folds every M20 lever fire into `other` (357 rows, 29.1%,
   18 distinct reasons) alongside orphan artifacts.
3. `xauusd_trend_1h` was swept against **MGC's candles** — proven by an exact
   3.0-bytes-per-row size delta = `len("XAUUSD") − len("MGC")`.
4. `position_telemetry` serves the `-1e18` sort sentinel as a measured peak, and
   the Check-A invariant counts it as *within cap*.
5. `claim-basis-guard` does not scan `detail`/`evidence` — 199 and 102 of 712 rows
   populate them.
6. `cross_asset_live`'s docstring promises NaN degradation; the shared offline
   function zero-fills per slot. live==train held; learnability did not.

All six filed with severity, tier and resolution criteria.

## Risks and Follow-Ups
- **Technical:** two `ib_paper` positions (MGC 95, MES 15) at **0.0 take-profit
  coverage**, MGC also monitor-blind on `candles_unavailable` — paper, but the
  failure shape is real. The equity/ETF correlation gap blocks both the
  mgc/qqq/tlt/spy sweeps and peer features for 12 of 23 traded symbols.
- **Tier-3 awaiting approval:** none opened this sprint.
- **Blockers:** none for E2.

## Deferred Items
- Capture-rate conditional on winners / on reaching a fraction of `cap_R` — the cut
  that would isolate exit timing from win rate. Named as the next measurement,
  deliberately not inferred.
- Peers for the non-crypto twelve — blocked on measurement, not on effort.
- The coordination board's issue **body** still needs a human restore from GitHub's
  edit history (`BL-20260819-BOARD-BODY-OVERWRITTEN-BY-ISSUE-WRITE-UPDATE`).

## Next Recommended Sprint
- **Suggested next sprint:** **E2** — per-feature information vs forward R over the
  widened panel: purged/embargoed, grouped by `trade_id`, against a
  **shuffled-label control**. It has never been run.
- **Why next:** E0 established that 52.4% of exits are decided at entry and that the
  only working mechanism is endogenous; E1 put exogenous peer state into the panel
  with a live accessor behind it. E2 is the first honest test of whether anything in
  that widened panel carries information about forward R.
- **Required verification before starting:** confirm #10006 merged; build a panel
  with `state: joined` and a stated `row_coverage`; pre-register the shuffled-label
  control **before** looking at any feature's score.
- **Disposition if E2 returns negative:** §3.1 — regroup and widen. A negative is a
  statement about the constructs tried over the substrate available, with a date and
  a corpus attached. It does not close the thread.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked (M20 substrate; no row change required).
- [x] Contradictions were recorded — six, all filed with resolution criteria.
- [x] Remaining unknowns were stated clearly.
