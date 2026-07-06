# Sprint Log: S-M19-OVERNIGHT-2026-07-06

## Date Range
2026-07-05 (eve) → 2026-07-06 (morning). Operator-approved autonomous
overnight session (D2 spike research + other research + ≤$5 GPU burst budget).

## Objective
Execute the M19 next-direction program's research queue overnight: run D2
spike A on fresh data, widen the fc family to SOL (side-stream + A/B),
re-probe the T1.1 TCN negative at denser labels (GPU bursts), exercise the
D4a powered-RG4 rails end-to-end, and verify the D1 soak deploy — with
consolidated evidence by morning.

## Tier
Tier 1 throughout — offline research, candidate-stage manifests, docs,
backlog, observability reads. $ spend: GPU bursts inside the operator's ≤$5
overnight allowance (July ledger was $0.04/$10 pre-session). No live-path,
config/, or order-path change; no promotion proposed.

## Starting Context
Ranked plan D4▸D1▸D2▸D3 approved 2026-07-05 ("looks good, let's move
forward"); #5626 (recommendation report) / #5627 (D4a rails + D1 soak build)
merged that evening. **Honest session note:** the session stalled
~21:55Z→06:20Z — the `send_later` self-timer tool failed repeatedly
("permission stream closed") and no timer chain got armed, so the overnight
program ran COMPRESSED from ~06:20Z using a `create_trigger` one-shot chain
instead (the operator's explicit "short timers, don't rely on webhooks"
instruction is now implemented via `create_trigger`/`update_trigger`).

## Repo State Checked
`main` @ 3b8437c at branch time (evening) → work landed as #5628 (spike-A
harness + SOL fc manifest, merged 22:0xZ after a canonical-config-loaders fix
517f21e), #5635 (burst build_params override, merged 06:33Z), #5645 (this
consolidation). Branch `claude/m19-next-direction-research-zl2544` restarted
from origin/main after each merge.

## Files and Systems Inspected
`scripts/check_canonical_config_loaders.py` + `src/config/accounts_loader.py`
(guard fix); `scripts/ml/gpu_burst/{_remote,runpod_burst}.py` +
`tests/scripts/test_gpu_burst.py`; `ml/configs/btc-regime-15m-tcn-v1.yaml`,
`eth-regime-15m-lgbm-{fc-pcv-v1,base-pcv-v003}.yaml`;
`scripts/ops/{build_trainer_datasets,sync_trainer_data,run_training_cycle,run_forecast_producer}.sh`;
`ml/datasets/cli.py` + `families/trade_outcomes.py`;
`deploy/ict-git-sync.service` + `scripts/deploy_pull_restart.sh` (deploy
restart semantics). **Live pulls:** diag #5633 (`/api/diag/version` →
`0b9c7bbc`), #5634 (fc_geometry_soak log). **Trainer relays:** #5632 (sync +
build + spike-A + SOL check), #5639/#5643/#5644 (SOL build start / chain arm /
RG4 — see Contradictions).

## Work Completed
- **#5628 CI fix (517f21e):** spike-A harness's hand-rolled
  `yaml.safe_load(accounts.yaml)` replaced with the canonical
  `load_accounts_dict` (guard + its pytest wrapper were the only 2 failures in
  7,065 tests); merged.
- **D2 spike A EXECUTED** (relay #5632, fresh sync + `trade_outcomes` v002
  rebuild with `include_snapshots=true`): **negative, structural** — only
  6/230 paper rows predate the primary real holdout window (0 at wider cuts),
  so leak-free pooling adds almost nothing; no arm clears the majority
  baseline (605 rows: 375 real @ 26.7% win / 230 paper @ 34.4%; AUC 0.70
  primary / 0.62 fold-0.3; recall_win 0). Full write-up:
  `docs/research/D2-spike-A-pooled-labels-evidence-2026-07-06.md`; backlog +
  ROADMAP updated (accrual-gated re-run).
- **D1 deploy verified:** live web-api at `0b9c7bbc` (includes #5627);
  `deploy_pull_restart.sh` restarts services on git-sync, so the trader
  process runs the soak writer. Soak log `present:false` — expected-clean (no
  live opening order since deploy). The D1 clock is armed.
- **#5635 (merged 06:33Z):** GPU-burst driver now threads a manifest's
  `dataset.build_params` into the on-pod market_features build (unknown keys
  refused pre-rental; default byte-identical); + the two TCN
  label-sensitivity arm manifests (`btc-regime-15m-tcn-vt003/vt004-v1`,
  class_weight omitted → per-arm auto-weighting, explicitly not
  promotion-eligible). 37 burst tests pass.
- **GPU bursts fired:** arm A (vt003, issue #5641) launched on a pod and
  **failed at manifest load** ($0.0167 billed) — `DatasetRef` rejected the new
  `dataset.build_params` key when `python -m ml train` parsed the manifest
  on-pod (#5635 threaded it through the driver but not the schema). Fixed in
  this PR (`ml/manifest.py`: optional `build_params` field, round-trip
  tested); both arms re-fire after this merges. Arm B (#5642) was separately
  **cancelled by the gpu-burst workflow's concurrency group** (see
  Contradictions).
- **SOL fc side-stream:** forecast build started detached on the trainer
  (#5639, pid confirmed); `sol-regime-15m-lgbm-base-pcv-v530` control manifest
  authored (same v530 dataset, base features — a cleaner control than the
  BTC/ETH cross-dataset precedent); chain (v530 build → fc + base trains)
  armed once trainer SSH recovered.
- **Timer chain:** `create_trigger` one-shots re-armed each wakeup (the
  operator's "don't rely on webhooks" instruction).

## Validation Performed
- Spike-A numbers quoted verbatim from the relay output (#5632), not
  reconstructed; harness smoke-tested green on synthetic data pre-merge.
- Burst tests: 37 pass locally; guard + ruff clean on every push.
- Deploy verification via live diag reads (#5633/#5634), not inference.
- **Gaps not yet verified at log time:** RG4-with-rails output (two attempts
  failed on trainer SSH pressure; re-run pending); burst arm results
  (ingest/eval pending); SOL chain completion (hours-scale forecast build).
  Each is tracked to a collection relay / next session.

## Documentation Updated
- `docs/research/D2-spike-A-pooled-labels-evidence-2026-07-06.md` (new)
- `docs/claude/ml-review-backlog.json` (`MB-20260705-META-LABEL-WALL`
  evidence + accrual-gated trigger)
- `ROADMAP.md` (D2 row: RAN 2026-07-06, negative-structural, accrual-gated)
- This log.

## Contradictions or Drift Found
- **Trainer-relay fragility under load (new, logged):** a long-running inline
  relay command dies with the SSH session (`broken pipe`, #5639's RG4 leg),
  and a memory-pressured trainer (chronos build on the 1-CPU/6GB box) refuses
  SSH outright (`banner exchange timeout`, #5643/#5644). Pattern fix applied:
  anything >~1 min on the trainer runs `nohup`-detached with a later
  collection relay.
- **gpu-burst workflow concurrency landmine (new, logged):** the workflow
  triggers on EVERY `issues.opened` (skipping unlabeled ones) but its single
  `concurrency: gpu-burst-train` group means ANY new issue cancels a PENDING
  queued burst — arm B (#5642) was cancelled by an unrelated trainer-diag
  issue's skip-run. Workaround: fire one burst at a time, re-fire after the
  running one completes. A per-issue concurrency key (e.g.
  `gpu-burst-${{ github.event.issue.number }}`) plus keeping the spend gate
  serialized would remove the landmine — logged to the health-review backlog.

## Risks and Follow-Ups
- Trainer box memory is tight while the chronos build runs; the SOL chain's
  trains queue behind it (nice'd). If the box OOMs, the chain aborts loudly
  in `/tmp/sol_chain.log` — collect next session.
- The TCN sensitivity arms are probes at non-production labels: whatever
  they show, promotion-eligibility requires re-establishing at the
  production label (or a deliberate Tier-3 label change with backtest
  evidence).
- Timer-tool failure root cause (`send_later` permission stream) is
  environmental; `create_trigger` is the working path in this session shape.

## Deferred Items
- Collect: RG4-with-rails output, burst arm A/B bundles + eval, SOL chain
  results (fc vs base A/B on v530).
- Burst-bundle ingest + candidate registration for the TCN arms (existing
  `ingest_bundle.py` path) once both bundles exist.
- gpu-burst concurrency-key fix (Tier-1 CI change, backlogged).

## Next Recommended Sprint
Collection + read-out session: pull `/tmp/rg4.log` + `/tmp/sol_chain.log` +
the two burst bundles; eval the TCN arms vs the 0.005 run and (if either
improves) train the matching LightGBM control at the same label; write the
fc-family SOL A/B read-out; then back to the D4 soak watch (powered RG4
~mid-July).

## Wrap-Up Check
- [x] Code inspected directly (guard, loader, burst driver, deploy scripts — file:line in Files/Systems).
- [x] Docs reviewed/updated (evidence doc, backlog, ROADMAP, this log).
- [x] TRADE-PIPELINE untouched (no pipeline-stage change).
- [x] Roadmap checked + updated (D2 row).
- [x] Contradictions recorded (relay fragility, burst concurrency landmine).
- [x] Unknowns stated plainly (RG4/burst/SOL results pending at log time).
- [x] No live-path change; spend inside budget; no promotion proposed.
