# Sprint Log: S-M25-PROMOTION-EXECUTION-20260720

## Date Range
- Start: 2026-07-20 (~06:00Z)
- End: 2026-07-20 (~20:30Z; MES re-cert tail runs to ~00:30Z 07-21)

## Objective
- Primary goal: execute the operator-pre-approved M25 BTC/SOL vol-head
  promotions the moment serving-fidelity certification allowed (operator:
  "the promotions are approved too, just ping me when you do it").
- Secondary goals: land M26 P1 (conflict-taxonomy soak), resolve the
  serving-parity skew (MB-20260720-LIVE-SERVING-PARITY-SKEW), ship the
  frozen-dataset retrain skip guard (MB-20260720-FCPCV-RETRAIN-NOOP),
  MES re-certification under the same pre-approval after CME reopen.

## Tier
- Tier 1 (instrument fixes, tests, docs, backlog) + Tier 2 (lightgbm-pin
  deploy + trader restarts, operator-OK'd in chat) + Tier 3 (four
  promote-stage actions, operator-pre-approved in chat 2026-07-20).
- Justification: each tier-gated step carries the operator approval verbatim
  in its dispatch issue (#7095 deploy, #7175 restart, #7171/#7173 promotions).

## Starting Context
- Active roadmap items: M25 (promotion consolidation, gate reframe 07-19),
  M26 P0→P1, MB-20260720-LIVE-SERVING-PARITY-SKEW open as a P1 env-skew
  suspect (lightgbm unpinned).
- Prior sprint reference: S-M26-P0 / WS-A close-out (#6953), gate-reframe
  work 2026-07-19.
- Known risks at start: live_parity failing 100% on both candidates
  (believed to be a lightgbm version skew); GitHub CI appearing dead
  (later disproven); multi-session merge contention.

## Repo State Checked
- Branch or commit reviewed: `claude/ict-ml-continuation-7zp8by`; main from
  89c9b480 through d6619519 across the day.
- Deployment state reviewed: live VM pre-deploy at 89c9b480 → post-deploy
  99499847 (10:30Z, verified in the pull-and-deploy log) → subsequent
  git-sync deploys; trainer reset to each merged main before every
  gate-check.
- Canonical docs reviewed: CLAUDE-RULES-CANONICAL (session start),
  M25-promotion-consolidation-DESIGN (gate reframe), sprint template.

## Files and Systems Inspected
- Code files inspected: `ml/promotion/live_parity.py`, `ml/promotion/gates.py`,
  `ml/cli.py`, `ml/shadow/factory.py`, `ml/predictors/shadow.py`,
  `src/runtime/regime_bar_scoring.py`, `scripts/deploy_pull_restart.sh`,
  `scripts/ops/publish_trainer_mirror.sh`, `scripts/ops/sync_trainer_data.sh`,
  `scripts/ops/run_training_cycle.sh`.
- Config files inspected: `config/accounts.yaml` (breakout_1 de-dup),
  `requirements.txt` (lightgbm pin).
- Deployment files inspected: `deploy/ict-trader-live.service`,
  `deploy/ict-web-api.service` (interpreter = pip target check).
- Docs inspected: `docs/research/M25-btc-sol-promotion-packets-2026-07-20.md`.
- Services or timers inspected: `ict-trader-live.service` (2 restarts:
  10:30Z deploy, 20:00:57Z activation), trainer publish/mirror.
- GitHub Actions workflows inspected: `pytest-run.yml` (timeout/trigger),
  `trainer-vm-diag.yml` (cmd parsing), `vm-diag-snapshot.yml` (path rules),
  `branch-protection-sync.yml` (required contexts).

## Work Completed
- **M25 promotions EXECUTED + activated (Tier-3, operator-pre-approved):**
  `btc-regime-15m-lgbm-fc-pcv-v1` shadow→**advisory** (drives the live BTC
  vol gate), `btc-regime-15m-lgbm-v2` advisory→shadow,
  `sol-regime-15m-lgbm-fc-pcv-v1` shadow→**advisory** (SOL's first advisory
  head), `sol-regime-15m-lgbm-v1` shadow→candidate. Gate-check read
  `ready:true` on BOTH heads (all 8 required gates PASS) immediately before
  execution (relay #7173); registry stages verified (#7174); mirror published
  19:57Z; trader restarted 20:00:57Z (#7175) to invalidate the in-process
  predictor caches; first-decision verified — the heads' own log rows flip
  `shadow`→`advisory` exactly at the restart boundary (#7177/#7178).
- **Serving-parity mystery CLOSED as instrument bugs (two), live serving
  proven byte-perfect:** (1) `score_fidelity` float-coerced int features
  (`dayofweek`/`hour_of_day`) before predicting — lightgbm is
  dtype-sensitive, so every re-score shifted 3e-2..1.2e-1 → phantom "100%
  mismatch vs every artifact" (PR #7155; decisive probes #7151/#7153: raw-row
  replay matched live 20/20 with delta 0.00e+00 on both heads). (2) The
  dead-feature check judged liveness over the 50-row (~12.5h) fidelity
  sample, inside which `dayofweek` is structurally constant → widened to a
  multi-day window, `DEFAULT_DEAD_WINDOW_N=672` (PR #7167).
- **lightgbm==4.6.0 exact pin deployed** (PR #7082 + Tier-2 pull-and-deploy
  #7095) — not the skew cause, kept as live/trainer stack equalization.
- **M26 P1 conflict-taxonomy soak deployed** (in #7082): every hold-policy
  flip suppression logs a classified row (coexist ≥4× TF ratio /
  transition_vote) to `runtime_logs/conflict_taxonomy_soak.jsonl`.
- **Frozen-dataset retrain skip guard shipped** (`scripts/ops/
  dataset_unchanged_check.py` + cycle wiring + 7 tests, in #7082).
- **Main fixed twice:** #7060's prop de-dup left `test_prop_account_config`
  red on main (aligned in #7082); false Actions-outage backlog item corrected
  (real cause: dirty PR → no merge ref → PR workflows silently skip).
- **Backlogs:** MB-20260720-LIVE-SERVING-PARITY-SKEW → resolved (false
  alarm, full evidence chain); BL-20260720-GH-ACTIONS-PUSH-EVENTS-DEAD →
  resolved (false alarm + lesson); BL-20260720-MERGE-PROTOCOL-LAPSE filed
  (operator-flagged; claim-less merges raced 3×, claimed merges 0×).

## Validation Performed
- Tests run: full `tests/ml` (989 passed, 3 skipped), targeted parity/gates
  suites per change, conflict-taxonomy suite (19), breakout wiring (6);
  CI green on every merged PR (#7082 26 checks, #7155, #7167).
- Dry-runs or staging checks: gate-check re-runs after each instrument fix;
  conditional promotion script only executed on `ready:true` × 2.
- Manual code verification: deploy pip target == service interpreter
  (`/usr/bin/python3` both — later superseded by venv drop-in per unit);
  post-deploy HEAD verified in the deploy log; registry stages read back
  post-promotion; first-decision advisory rows read from the live log.
- Gaps not yet verified: `regime_ml_vol_shadow` agreement rows from the NEW
  head not yet sampled (next BTC gate decision will produce them); MES
  re-cert pending 23:50Z.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none (no contract change).
- Roadmap updates: none this log (M25 status row update deferred to the
  close-out PR if drifted).
- Subsystem doc updates:
  `docs/research/M25-btc-sol-promotion-packets-2026-07-20.md` → status
  updated through execution (serving-fidelity resolution recorded).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Main red on `test_breakout_prop_wiring` after #7060 (fixed in #7082).
- The parity gate's own docstring claimed the `_num` coercion measured "the
  row the artifact would actually see" — inverted; the artifact sees the raw
  row (fixed with the code).
- Merge-protocol drift: `session-board.json` unused vs the live board
  (BL-20260720-MERGE-PROTOCOL-LAPSE).

## Risks and Follow-Ups
- Remaining technical risks: the promoted BTC head's first enforced vol-gate
  decisions are unobserved yet (rollback: `REGIME_ML_VERDICT_MODE=shadow`
  env flip, or single promote-stage reversals); frozen-dataset heads (6/29)
  still need per-head unpin-vs-accept decisions.
- Remaining product decisions (Tier 3): SOL `trend_vol` cell authoring (now
  unblocked by the SOL advisory head); ETH 15m head promotion path;
  `BYBIT_TPSL_MODE=partial` flip (other session's lane).
- Blockers: none.

## Deferred Items
- Predictor-cache mtime invalidation (Tier-2) so future promotions need no
  restart.
- M26 P2 transition score (needs ~a week of taxonomy-soak rows).
- Per-head frozen-dataset remediation pass.

## Next Recommended Sprint
- MES re-cert execution record (tonight, pre-approved), then SOL vol-gate
  cell authoring + ETH 15m gate-checks under the fixed instrument — the two
  highest-leverage conversions of certified capability into live decisions.
