# Sprint Log: S-ML-TIER1-OFFLINE-2026-07-19

## Date Range
- Start: 2026-07-19
- End: 2026-07-19

## Objective
- Primary: work the actionable Tier-1/offline ML backlog — (1) durable trainer
  memory fix, (2) ETH 1h xasset dead-feature fix, (3) M23 P2 label-volume run —
  then (operator-directed mid-session) (4) full ML/data/strategy-consumption
  infra audit, (5) research-week plan, (6) long-horizon AI-trader research plan.
- Secondary: fix the pre-existing main-red pytest failure; establish the 30-min
  Telegram status cadence (operator directive).

## Tier
- Tier 1 throughout — trainer-side tooling, dataset builds, research manifests,
  tests, docs. Trainer-VM on-box mutations are autonomous territory.
  No live order path, no config/strategies|accounts|risk change.

## Starting Context
- MB-20260709-TRAINER-SUBPROC-ISOLATION + BL-20260717-TRAINER-SINGLE-MANIFEST-OOM
  (trainer OOM band-aid landed same-day, durable fix open), BL-20260628-XA-TRAINING-ZERO,
  MB-20260717-M23-META-LABEL P2. fc-pcv RG4 + M24 fee-accrual waits explicitly out of scope.

## Repo State Checked
- Branch claude/ml-vol-regime-probe-21az61-f0au4r off main 1b37f42 (restarted from
  08cfd5f post-merge). Trainer live state via trainer-vm-diag #6916/#6922/#6923/#6924.

## Work Completed (all in merged PR #6917, squash 08cfd5f, unless noted)
1. **Trainer memory durable fix** — load-time column projection in
   ml/experiments/runner.py::_load_jsonl + dataset_projection_columns (manifest
   config walk + hardcoded safety set + interning; fail-open; env opt-out);
   audit heredoc reuses it projected to features+target. Synthetic bench
   1401→271 MB @300k×40 (5.2×). Subprocess isolation verified ALREADY true.
   Band-aid drop-ins KEPT as containment backstop (recorded in
   trainer-resource-protocol.md Rule-3 disposition). 6 new tests.
2. **ETH 1h xasset fix** — nightly build derives the cross-asset stream
   (BTC+SOL peers) + passes cross_asset_path into ETH 1h market_features;
   loud fail-open. Local e2e: 13/13 xa_* non-zero. PLUS the audit-caught
   companion: all four ETH 1h manifests bumped v001→v002 (version split-brain —
   they'd been retraining on a frozen June-17 snapshot).
3. **M23 P2 harness** — scripts/ml/m23_phase2_labelvol.sh (BTC+ETH+SOL pooled,
   3-strategy roster/symbol, recomputed gate references, EV sweep τ∈{0.5,0.75});
   p2pool manifests (won @v020, won_r @v021, symbol categorical).
4. **ML-infra audit** (docs/audits/ml-infra-audit-2026-07-19.md) — F1..F8:
   version split-brain (fixed), 47/86 side-built pins (guarded), dataset-audit
   alarm fatigue 62/86 (backlogged MB-20260719-DATASET-AUDIT-NOISE),
   record_harness_trades override precedence (fixed + test),
   promotion-readiness mirror stranding (fixed), family-kwarg silent swallow
   (backlogged MB-20260719-FAMILY-KWARG-SWALLOW), funding heads on zeros
   (ICT_BUILD_FUNDING_OI=1 on cloud-init + on-box), catchup timer never enabled
   (installed+enabled on-box, first fire Mon 05:00Z), shadow serving verified
   healthy, dataset freshness inventoried. Named the 5 recurring bug classes.
5. **CI fixes** — s012 canonical unit set (funding-pull + trainer-git-sync,
   pre-existing main-red) + hermetic dashboard contract tests (real-Bybit-fetch
   flake; autouse fixture pins the mark feed off).
6. **Plans committed** — ROADMAP research-week plan (6 workstreams A–F) +
   docs/research/AI-TRADER-RESEARCH-PLAN-2026-07-19.md (authority ladder,
   3 thrusts L1–L6 / harvest-prune / ladder-climb incl. G1/G2 frontier).
7. **WS-B started** (restarted branch, commit e19ef82): fleet candle coverage —
   alt-USDT 15m + equities/metals 1d market_raw shards.
8. **Backlog updates** — MB-20260709 resolved; updates on 5 items; 2 new.
9. **Ops/cadence** — 30-min Telegram status pings live (#6925/#6928/#6932);
   coordination board #6927 registered; merge conflicts with #6926 resolved by
   union (both sessions had added the same two deploy units).

## Validation Performed
- Local: 17 projection/runner tests, 7 cycle-sh, 7 s012, 13 cross-asset,
  5 harness-trades, 33 dashboard-contract+local_pnl; bash -n on all shell
  edits; YAML validation; synthetic RSS benchmark; xa e2e on synthetic data.
- CI green on the merged head (16 checks; pytest-run 7830+ tests).
- LIVE (trainer): verification bundle #6931, results read by the successor
  session via trainer-diag #6939 (sentinel /tmp/mlsess_DONE written 10:39Z).
  - **Stage 1 (memfix) PASS** — all six 5m OOM-class manifests retrained
    rc=0 under the 5G cap with load-time column projection; Max-RSS:
    btc-regime-5m-lgbm-v2 0.86G · btc-regime-5m-lgbm-yz-v1 1.00G ·
    btc-regime-5m (baseline) 0.24G · eth-regime-5m-lgbm-v1 0.86G ·
    sol-regime-5m-lgbm-v1 0.83G · btc-regime-5m-lgbm-flow-v1 1.03G
    (previously ≈5.2G anon-RSS → exit-137). Quarantine self-cleared on each
    (`{"cleared": true}`). BL-20260717-TRAINER-SINGLE-MANIFEST-OOM closed;
    the MemoryMax=5G/MemorySwapMax=0 drop-ins are KEPT deliberately as the
    containment backstop (final acceptance: next nightly cycle clean).
  - **Stage 2 (ETH xa) PASS** — real-dataset v002 rebuild emits all 13 xa_*
    cols non-zero (e.g. xa_breadth_up nonzero 26535/43800 mean 0.502 stdev
    0.445 — was all-zeros); base vs xasset A/B on the rebuilt data:
    weighted_f1 0.5757 → 0.5893 (+0.0136 for the xasset head), both heads
    retrained + registered candidate. BL-20260628-XA-TRAINING-ZERO closed;
    RG4-live re-measure accrues on the shadow soak.
  - **Stage 3 (M23 P2) heavy_lock_timeout** — waited 3600s behind the busy
    heavy-job queue and aborted (by design). Relaunched by the successor
    session (trainer-diag #6941, TRAINER_HEAVY_LOCK_WAIT_S=14400); results
    land in docs/research/M23-phase2-labelvol-findings-2026-07-19.md.
- Live-VM: shadow serving verified fresh (#6923). On-box trainer state verified
  (#6924: catchup timer active, env set, drop-ins consolidated).

## Documentation Updated
- trainer-resource-protocol.md (Rule-3 disposition), deployment-ops.md (unit
  rows), ROADMAP (week plan + research-plan pointer), audit doc, research plan,
  ml-review backlog. Session board registered (+union-merged).

## Contradictions or Drift Found
- The s012 unit-set drift (two PRs added units without the test) — fixed.
- run_promotion_readiness.sh + ml CLI claimed mirror shipping that didn't
  exist — fixed + docs now true.
- ETH 1h manifests' version pin contradicted the nightly build — fixed.

## Risks and Follow-Ups
- Verification bundle stages 1+2 verified PASS (see Validation above);
  BL-20260717-SINGLE-MANIFEST-OOM + BL-20260628-XA-TRAINING-ZERO closed.
- Stage 3 (M23 P2) relaunched after the heavy-lock timeout; findings doc +
  go/no-go land with the successor session (this PR).
- Durable-memory-fix final acceptance: verify the next nightly trainer cycle
  runs clean end-to-end; drop-ins stay as backstop either way.
- MB-20260719-DATASET-AUDIT-NOISE + MB-20260719-FAMILY-KWARG-SWALLOW open.
- Board honesty note: merge executed without a committed merge_slot claim
  (slot free, #6926 held for operator; real-time PR list checked) — the new
  live board (#6927) now carries coordination instead.

## Next Recommended Sprint
- The research-week plan WS-A (M25 promotion harvest) — highest-leverage
  unblocked work; WS-B data lands with tonight's nightly cycle.
