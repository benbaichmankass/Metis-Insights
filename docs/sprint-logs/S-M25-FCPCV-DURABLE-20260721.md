# Sprint Log: S-M25-FCPCV-DURABLE-20260721

## Date Range
- Start: 2026-07-21 (~04:00Z)
- End: 2026-07-21 (~06:45Z)

## Objective
- Primary goal: work the operator-approved Tier-3 morning dispositions from the
  overnight packet, and specifically execute rec #2 (fc-pcv refresh siblings) —
  which turned into a durable root-cause fix for the frozen fc-pcv training data.
- Secondary goals: reconcile the four morning Tier-3 decisions durably; verify
  the live system after the overnight BTC/SOL/MES promotions.

## Tier
- Tier 1 (new manifests + trainer-side dataset tooling + docs/backlogs). No
  live order-path / config/strategies.yaml / accounts.yaml / risk file touched;
  the new heads register at `shadow` (observe-only, never order-influencing).
- Justification: rec #2 was operator-approved ("take your recommendations");
  the change is trainer dataset tooling + shadow-stage manifests, no live gate.

## Starting Context
- Active roadmap items: M25 (promotion consolidation — promotions EXECUTED
  overnight 07-20), M26 P1 (taxonomy soak accruing), M27 (parallel session).
- Prior sprint reference: `S-M25-PROMOTION-EXECUTION-20260720` (the overnight
  BTC/SOL/MES promotions + the Tier-2 restart-free cache fix).
- Known risks at start: the fc-pcv advisory heads were flagged frozen-data
  (`MB-20260720-FCPCV-RETRAIN-NOOP`); the morning Tier-3 packet had four open
  dispositions.

## Repo State Checked
- Branch/commit reviewed: `claude/ict-ml-continuation-7zp8by`; main from
  `7aae2dee` through `c4fe60d3` (this session's fc-pcv merge) + M27 merges.
- Deployment state reviewed: live trader healthy on the post-nightly sha
  (`/api/diag/status` — heartbeat running, all expected accounts up, mem ~10%);
  the promoted advisory heads confirmed serving (fc-pcv-v1 live rows carry all
  six `fc_*`).
- Canonical docs reviewed: CLAUDE.md, ROADMAP.md M25/M26 rows, the overnight
  morning-packets doc, the ml-review backlog.

## Files and Systems Inspected
- Code/config: `ml/configs/btc-regime-15m-lgbm-fc-pcv-v1.yaml` +
  `sol-...-v1.yaml` (the frozen advisory heads), `scripts/ops/build_trainer_datasets.sh`
  (nightly build), `scripts/ml/build_forecasts.py` + `ml/datasets/forecast_features.py`
  (forecast side-stream recipe + DEFAULT_* params), `scripts/ops/run_forecast_producer.sh`
  (live serve → `publish_live_forecasts`).
- Trainer state (via relays): registry stages, dataset versions on disk,
  forecast side-stream mtimes (frozen ~Jul 1/6), `ict-trainer-forecast.timer`
  (produces the LIVE serve artifact, not the training side-stream).
- Live state (via diag relays): `/api/diag/status`, fc-pcv-v1 + MES live
  shadow-prediction `row_keys`.

## Work Completed
- **fc-pcv frozen-training-data ROOT CAUSE found + durable fix shipped** (PR
  #7252, squash `c4fe60d3`). The fc-pcv heads train on the `fc_*` block, which
  `market_features` as-of joins from a forecast TRAINING side-stream
  (`datasets-out/forecasts/<sym>/15m`). That side-stream was only ever written
  by a *manual* `build_forecasts.py` run — the nightly rebuilt `market_raw` but
  never the forecast side-stream — so it froze ~Jul 1 (BTC) / Jul 6 (SOL),
  pinning the fc-pcv datasets and making the nightly retrains byte-identical
  no-ops. **Live serve was never affected** (`publish_live_forecasts` refreshes
  the live `fc_*` every ~15m; verified all six present in the live advisory
  head's `row_keys`). Fix: `build_trainer_datasets.sh::build_forecast_sidestream`
  rebuilds the side-stream nightly; BTC/SOL 15m `market_features` now build WITH
  `forecast_path` (no explicit params — anchors to `forecast_features.py`
  `DEFAULT_*` like the live serve, so train==serve). Two fresh-data siblings
  `btc/sol-regime-15m-lgbm-fc-pcv-v2` (dataset `v002`, target `shadow`) added.
- **Seed build run + verified** (trainer relays): forecast side-streams rebuilt
  fresh (BTC 43.8k / SOL 41.8k rows = full current history), `v002` 15m
  `market_features` rebuilt with `forecast_path` → **`VERDICT=FC_POPULATED`**
  (all six `fc_*` nonzero over 20k rows). Both v2 siblings trained
  (btc weighted_f1 0.93 / sol 0.67) and **confirmed at `shadow`** (a defensive
  `promote-stage --new-stage shadow` refused as "already shadow"). Mirror
  published to live.
- **Morning Tier-3 dispositions worked** (operator "take your recommendations"):
  SOL trend_vol cells **HELD** (mixed 3/4 walk-forward + a measured 0.81
  label-fidelity caveat); ETH control head `selfonly-v901ctrl` **KEPT** (revised
  from the memo's "retire" — the overnight xa fix made the xasset-vs-selfonly
  comparison newly meaningful); 15m MES head **PARKED** (needs a per-symbol
  multi-timeframe advisory-head resolution first).
- **Live health verified** post-overnight-promotions (trader running, accounts
  up, advisory heads serving with the full feature set).

## Validation Performed
- Tests: `bash -n` on the build script; both new manifests parse (ds v002,
  stage shadow, 13 feats); CI green on the merged PR (11/11 after a re-trigger
  cleared a ~35-min GitHub runner-queue wedge).
- Dry-runs/staging: the seed build ran the exact nightly-patched commands once
  on the trainer before relying on the nightly; `FC_POPULATED` verdict confirms
  the fc_* as-of join populates; the promote-stage no-op refusal confirms the
  siblings are genuinely at shadow (soaking).
- Manual verification: live fc-pcv-v1 `row_keys` carry all six `fc_*` (live
  serve healthy — the concern that the advisory heads served degraded was
  disproven).
- Gaps: v2 siblings have no soak yet (started today); the durability of the
  nightly fix (does it keep the side-stream fresh) needs confirming over the
  next few nightlies — logged as `MB-20260721-FCPCV-V2-SOAK`.

## Documentation Updated
- ROADMAP.md M25 row: appended the 2026-07-20/21 executed-promotions record
  (BTC/SOL/MES → advisory under the gate reframe; the incumbent swaps; the
  restart-free activation; the fc-pcv durable fix).
- `docs/claude/ml-review-backlog.json`: `MB-20260720-FCPCV-RETRAIN-NOOP` →
  `mitigated` (root cause + fix recorded; residual frozen heads noted); added
  `MB-20260721-FCPCV-V2-SOAK` + `MB-20260721-MES-15M-HEAD-PARKED`.
- This sprint log.

## Contradictions or Drift Found
- ROADMAP M25 status cell was stale ("0 promotable this cycle", 07-18) vs the
  executed 07-20/21 promotions — fixed here.
- `canonical-doc-coherence` CI checker: PASS (no doc-vs-doc / structural drift).

## Risks and Follow-Ups
- The v2 siblings must actually stay fresh nightly — verify the nightly forecast
  rebuild + `v002` 15m `fc_*` on the next `/ml-review` (`MB-20260721-FCPCV-V2-SOAK`).
- The frozen v1 fc-pcv heads remain the LIVE advisory heads until v2 matures and
  a Tier-3 gate-checked swap replaces them — no live change from this session.
- 15m MES head parked pending the per-symbol multi-timeframe resolution
  (`MB-20260721-MES-15M-HEAD-PARKED`).

## Deferred Items
- fc-pcv v2 gate-check + swap (needs ~7d soak).
- SOL trend_vol cells (re-run with an offline fc-feature join to remove the
  label-fidelity caveat before reconsidering).
- M26 P2 (transition-score observe soak) — still gated on the P1 taxonomy soak
  accruing ~a week of rows.

## Next Recommended Sprint
- Watch the fc-pcv v2 soak toward its gate-check; on the next `/ml-review`
  confirm the nightly is keeping the forecast side-stream fresh (the fix's
  durability), and drain the two new backlog items.
