# Sprint Log — S-WEEKLY-REVIEW-EXEC-2026-08-01

Post-weekly-review operator-decision execution: report landing, SLV floor,
SOL advisory swap, fabricated-exit backfill apply, MES baseline backfill via
the backtest pipeline, nightly pin + staleness waiver.

## 1. Date Range

- **Start:** 2026-08-01 ~11:45 UTC (continuation of the weekly `/system-review`
  session `full-system-review-zajauh`, whose review work is recorded in
  `comms/reports/weekly/20260801T090000Z/` — this log covers the EXECUTION arc
  that followed)
- **End:** 2026-08-02 ~06:30 UTC

## 2. Objective

- **Primary:** land the weekly report cleanly (merge + operator ping despite the
  token-incident fallout), then execute the operator's decisions from it:
  (1) hand-run the fc-pcv v2 gate evidence, (2) fix `slv_trend_1h`'s
  degenerate-confidence entries, (3) run `backfill-fabricated-exits --apply`,
  (4) get the 3 never-trained MES baseline manifests trained.
- **Secondary:** keep every decision recorded on the right backlog row; leave
  no silent alarms behind.

## 3. Tier

Mixed, each change at its own tier with its own gate:
- **Tier 1** — report artifacts, backlog JSON updates, trainer-side dataset/
  build/staleness scripts, gate-check pre-sync (#8297, #8264, #8319, #8326).
- **Tier 2** — `backfill-fabricated-exits apply:1` (money-DB write; operator
  "run" in chat carried in issue #8253).
- **Tier 3** — `config/strategies.yaml` `slv_trend_1h.min_confidence 0.0→0.3`
  (PR #8255, merged only after explicit operator "approved" in chat) and the
  `sol-regime-15m-lgbm-fc-pcv-v2` shadow→advisory promotion (operator
  "approved" in chat on the ready:true packet; executed trainer-side #8307).

## 4. Starting Context

- Weekly `/system-review` RPT-20260801-090000 complete on the branch but
  unmerged; both Telegram channels dead mid-token-rotation; the parallel audit
  session resolved the token incident at 08:37Z (#8246) WHILE the report was
  assembling, making its rank-1 operator priority stale.
- fc-pcv v2 advisory swap 4 days overdue, gate evidence blocked by the
  promotion-readiness OOM (MB-20260719).
- `slv_trend_1h` on its 2nd consecutive review of should_skip-grade entries
  (PB-20260801-SLV-TREND-DEGENERATE-CONFIDENCE).
- 3 MES baseline manifests never trained in 64.6d
  (MB-20260801-MES-BASELINE-MANIFESTS-NEVER-TRAINED).

## 5. Repo State Checked

- `metis-insights` main advanced across the session from `618f0c62` →
  `71e0eb08` (parallel sessions active: #8246 incident closure, #8250/#8327
  macro snapshots, #8323 R4 gate reporter + BTC drift-hold).
- Live VM deploy verified twice via `/api/diag/version`: `5e378cd7` (report
  merge, 12:00Z) and `0093d2ba` (SLV floor, 14:09Z — trader restarted,
  heartbeat running, `slv_trend_1h` loaded).
- Trainer at `5e378cd7` → `759c37c7` across the trainer jobs; registry
  advisory count 2 → 3 after the SOL promotion.

## 6. Files and Systems Inspected

- `config/strategies.yaml` (slv_trend_1h block), `src/units/strategies/trend_donchian.py:404`
  (min_confidence refusal — enforcement verified in code before proposing),
  `config/regime_policy.yaml` reasoning via registry state (no SOL cells).
- `ml/datasets/backtest_recorder.py`, `ml/datasets/families/{trade_outcomes,setup_labels,execution_quality}.py`,
  `scripts/ml/record_harness_trades.py`, `scripts/backtest_trend.py` (CLI + loader),
  `ml/manifest.py` (strict dataclass — drove the notes-marker design),
  `scripts/ops/{build_trainer_datasets.sh,manifest_training_staleness.py,sync_trainer_data.sh,run_training_cycle.sh}`,
  `scripts/ml/gate_check_candidates.sh`, `ml/configs/mes-*.yaml`.
- Live VM via diag relay: `/api/diag/{version,status,audit_query,journalctl,broker_account_status,exchange_positions}`
  (issues #8258, #8263, #8265, #8285, #8287, #8288, #8290–#8292, #8295).
- Trainer VM via trainer-vm-diag: #8254, #8256, #8257, #8259–#8262, #8284,
  #8286, #8293, #8294, #8296, #8306, #8307, #8313, #8316, #8318.

## 7. Work Completed

1. **Weekly report landed** (PR #8248 → `5e378cd7`): merged `main` mid-flight
   (backlog conflict with #8246 resolved keeping both updates chronologically),
   amended the report's now-stale "alerting dark / re-paste tokens" claims with
   a `[RESOLVED 08:37Z…]` addendum across all four artifacts, amended the
   queued weekly ping to the corrected message; delivery verified via the
   `notify_on_pull` git-relay state advance (#8252).
2. **slv_trend_1h min_confidence 0.3 floor** (PR #8255 → `0093d2ba`, Tier-3
   operator-approved): recommendation delivered (regime gate NOT
   production-ready for SLV — no advisory head, no authored `trend_vol` cells;
   the floor is wired in `trend_donchian`), ETF-pilot wiring-test pin updated in
   the same PR after its pytest failure, deploy live-verified.
3. **backfill-fabricated-exits apply** (#8253, Tier-2 operator-OK'd): 14 rows
   repaired to MEASURED from own exchange fills; the 11 mirror-tier rows
   (ESTIMATED) left as a separate opt-in, offered to the operator.
4. **fc-pcv v2 gate evidence** (operator-approved hand-run): per-model
   `gate_check_candidates.sh` (the MB-20260719 OOM-safe shape) ran clean.
   Frozen live_parity counts root-caused to the trainer's stale shadow-log copy
   (nightly retrain resets the parity window; the ~05:00Z-synced copy can never
   show 20 post-retrain rows) — fixed by pre-syncing in
   `gate_check_candidates.sh` (PR #8297). Fresh-sync verdicts: **SOL v2
   ready:true blocking:[]**; BTC v2 blocked on `drift_clean` only.
5. **SOL advisory swap executed** (Tier-3, operator-approved):
   `sol-regime-15m-lgbm-fc-pcv-v2` shadow→advisory at 2026-08-02T04:10:36Z,
   `stage_history` carries attribution + gate evidence, advisory fleet 2→3,
   mirror re-published to live (#8307; first attempt #8306 failed on CLI
   syntax — `promote-stage` takes `--new-stage/--by/--reason` — disclosed).
   SOL advisory head restored; zero live-order impact until SOL `trend_vol`
   cells are authored (that Tier-3 follow-up is unblocked again).
6. **MES baselines root-caused then TRAINED via the backtest pipeline**
   (operator-directed after my wait-or-retire framing missed the S-MLOPT-S7
   design): journal has ZERO MES trades ever (`mes_trend_long_1d` healthy,
   long-only 1d Donchian-30 never fired in ~65d — in-distribution: the 10y
   config-exact backtest enters ~2.6×/yr). Chain: `backtest_trend.py`
   config-exact over `data/ES_F_1d.csv` (2,514 daily bars 2016→2026) → 26
   trades (wr 65.4%, net +24.9R, positive 9 of 11 years) →
   `record_harness_trades` into a schema-bootstrapped TEMP db (`is_backtest=1`
   only) → `include_backtest=true` builds (26 rows each) →
   **`mes-trade-outcome-winrate-baseline-v0` (f1 0.75) and
   `mes-setup-quality-baseline-v0` (mae 1.12) trained + registered at shadow**
   (#8318). First pass (#8316) accidentally used 5m bars (bad file pick, 552
   wrong-timeframe trades) — disclosed, temp db rebuilt, retrained on 1d.
7. **Nightly pin + accepted-wait waiver** (PR #8326, operator decisions):
   `build_trainer_datasets.sh` merges live MES rows + the standing backtest db
   into a scratch db and rebuilds the MES-scoped families each cycle
   (`include_backtest=true`; missing db → explicit skip, never silent);
   `manifest_training_staleness.py` gained the `awaiting_source_trades` waiver
   (marker in manifest `notes:`, never-trained branch only, summary counter) —
   applied to `mes-execution-quality.yaml` (KEPT, waiting for live fills:
   slippage is measured, not modeled).
8. **Corrections owned in-session:** (a) a phantom "ETF sleeve dark" alarm —
   chased through 6 diag issues before the local repro settled it: 2026-08-01
   is a SATURDAY; retracted loudly on the board; (b) the 5m-data backtest pass;
   (c) the promote-stage CLI syntax; (d) PR #8326 initially opened dirty
   (stacked on squash-merged history) — rebuilt via cherry-pick on fresh main.

## 8. Validation Performed

- Every VM-affecting change verified post-state via diag: report-merge deploy
  (`git_sha 5e378cd7` + ping delivery), SLV floor deploy (`0093d2ba`, trader
  active, strategy loaded), SOL promotion (registry stage + stage_history +
  advisory count + mirror publish), backfill apply (run output: 14 written),
  MES trains (registry files present, metrics real).
- Local: `bash -n` on both edited shell scripts; `py_compile` on the staleness
  sweep; `TrainingManifest.from_yaml` loads the annotated manifest; the waiver
  regex matches it; YAML parse + value assert on strategies.yaml.
- CI: green on every merged PR (#8248 25/25, #8255 25/25 after the pin fix,
  #8264, #8297, #8308, #8319; #8326 checks re-running on the rebuilt head at
  close, auto-merge armed).
- **Gaps not yet verified:** (a) the 2026-08-03 00:55Z nightly cycle exercising
  the #8326 pin (expect MES builds row_count 26; staleness
  `never_trained: 0 / awaiting_source: 1`) — Sunday 18:00Z wake armed;
  (b) first post-deploy `slv_trend_1h` sub-0.3 refusal (needs a live signal;
  market opens Mon 13:30Z; PB row awaiting-data); (c) BTC v2 drift re-check
  (KS 0.2012 at the parallel session's 04:35Z pull — marginal; same wake);
  (d) fresh-entry MES execution-quality data still awaits live fills by design.

## 9. Documentation Updated

- Backlog rows (all via merged PRs): `MB-20260721-FCPCV-V2-SOAK` (gate
  evidence, stale-mirror lesson, SOL swap execution),
  `MB-20260801-MES-BASELINE-MANIFESTS-NEVER-TRAINED` (root cause → backfill →
  decisions), `MB-20260719-PROMOREADY-OOSEDGE-OOM` (workaround exercised),
  `PB-20260801-SLV-TREND-DEGENERATE-CONFIDENCE` (recommendation + ship),
  `BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE` (merge-conflict resolution kept
  both sessions' updates).
- This sprint log + ROADMAP Historical Sprint Ledger row (same PR).
- No canonical-doc contradictions introduced: no schema/endpoint/architecture
  changes; the CLAUDE.md `REGIME_ML_VERDICT_MODE` row's "SOL currently has NO
  advisory head" paragraph is now stale — updated in this PR (see §10).

## 10. Contradictions or Drift Found

- `CLAUDE.md` § `REGIME_ML_VERDICT_MODE` said SOL has no advisory head pending
  the v2 swap — true until 2026-08-02 04:10Z, now superseded by the executed
  swap. Fixed in this PR (one sentence updated to record the restoration).
- The MES baseline manifests' header comments document a `build-dataset`
  invocation with `--datasets-root` (a `train`-only flag) — a stale-precedent
  landmine that cost one failed trainer round-trip (#8254). NOT fixed here
  (three manifests share it); logged in the MB row's runbook update instead.
- The weekly report shipped with an in-window-but-stale operator priority
  (token re-paste) because a parallel session resolved it during assembly —
  handled with a post-evidence addendum rather than a rewrite; pattern worth
  remembering for future report/parallel-session races.

## 11. Risks and Follow-Ups

- **BTC v2 swap decision** — drift KS marginal (0.2012 vs 0.20) and improving;
  re-check armed (Sunday 18:00Z wake) with the parallel backlog-cont session
  also watching the same row; whoever fires first records on MB-20260721.
- **SOL `trend_vol` cell authoring** (Tier-3) — the restored advisory head has
  zero live-order impact until cells exist; standing follow-up, now unblocked.
- **Mirror-tier backfill** (11 ESTIMATED rows) — offered, awaiting operator
  interest; not urgent (read-side filter already quarantines).
- **26-trade training population** — the MES baselines are per-strategy means
  on a small honest sample; fine for observability baselines, not evidence for
  anything order-influencing.

## 12. Deferred Items

- Pinning a periodic refresh of `backtest_trades_mes.db` itself (the harness
  re-run) — the standing db is static until the MB-row runbook is re-run;
  acceptable while `mes_trend_long_1d`'s config is unchanged.
- The `/system-review` breach-sweep directive's historical window remains with
  the review cadence (recorded on the token-incident row by the audit session).

## 13. Next Recommended Sprint

Author the SOL `trend_vol` cells (Tier-3, operator-gated) now that the
advisory head is restored — the entire Design-A vol-gate benefit for SOL is
gated on those cells existing; the BTC A/B evidence pattern
(`docs/research/A-vol-gating-AB-evidence-2026-06-27.md`) is the template.
Required verification: walk-forward the SOL cells before proposing the merge.

## 14. Wrap-Up Check

- [x] Code inspected directly (trend_donchian gate, manifest loader, family
      builders, cycle scripts — all read before changing/claiming)
- [x] Docs reviewed/updated (backlog rows, this log, ledger row, CLAUDE.md
      SOL-head sentence)
- [x] TRADE-PIPELINE unchanged (no pipeline-stage change — trainer-side only)
- [x] Roadmap checked + ledger row added
- [x] Contradictions recorded (§10)
- [x] Unknowns stated (§8 gaps — none silently claimed)
- [x] Board START/DONE + VM-lane claims posted throughout; merge slots
      claimed/released per protocol
