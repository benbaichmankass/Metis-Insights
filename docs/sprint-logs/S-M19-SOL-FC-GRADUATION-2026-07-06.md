# S-M19-SOL-FC-GRADUATION — SOL fc shadow graduation + session wrap-up (soak inventory)

## Date Range
- **Start:** 2026-07-06 (~10:50 UTC, follows S-M19-OVERNIGHT-2026-07-06 in the same session)
- **End:** 2026-07-06

## Objective
- **Primary:** execute the operator-approved SOL producer extension + fc-head shadow graduation (the follow-through on the overnight program's SOL fc-vs-base A/B win), verified end-to-end.
- **Secondary:** wrap the session "as done as possible" — fix the trainer git-auth ops debt durably, record every running soak/clock with its gate + watch surface so the next (research) session starts clean, and run doc-freshness.

## Tier
- **Tier 1** for the repo changes (producer script default, workflow extension, docs/backlogs). The **candidate→shadow promotion** is the operator-pre-authorized observe-only step (explicit chat approval 2026-07-06: "yes, let's continue with the sol producer"); shadow never influences an order. The trainer-side mutations (producer script, promotion, publish kick, credential bootstrap) are autonomous trainer scope per `docs/claude/trainer-vm-mode.md`.

## Starting Context
- **Prior log:** [`S-M19-OVERNIGHT-2026-07-06`](S-M19-OVERNIGHT-2026-07-06.md) + its morning addendum — the SOL fc-vs-base purged-CV A/B resolved as a clear fc win (evidence: [`SOL-fc-family-AB-evidence-2026-07-06`](../research/SOL-fc-family-AB-evidence-2026-07-06.md)), with candidate→shadow gated on the SOL forecast side-stream reaching production.
- **Known broken:** the trainer VM's anonymous `git pull` (repo went private 2026-07-06; `BL-20260706-TRAINER-GIT-AUTH-BROKEN`) — interim workaround was relay base64 file-writes.

## Repo State Checked
- Branch `claude/m19-next-direction-research-zl2544` restarted from `main` @ `8ef9015` (post-#5694 merge); PR **#5702** carries this session's changes.
- Trainer tree pinned at `0b9c7bbc` (git-auth broken) — every trainer-side change this session shipped via the relay no-git path and matches the PR content byte-for-byte.

## Files and Systems Inspected
- `scripts/ops/run_forecast_producer.sh` (the `FORECAST_SYMBOLS` default — the only place the symbol list lives), `scripts/ops/install_trainer_publish_units.sh` (unit bodies), `scripts/ml/publish_live_forecasts.py`, `ml/cli.py::promote-stage`, `.github/workflows/vm-git-credential-bootstrap.yml`, `config/regime_policy.yaml` (`trend_vol` cells — BTC-derived), the BTC/ETH graduation record [`S-M19-FC-SHADOW-T12-P1-2026-07-03`](S-M19-FC-SHADOW-T12-P1-2026-07-03.md).
- Trainer relays #5697–#5699 (SOL A/B completion), #5701 (producer + promotion); live diag #5714 (shadow_stats), #5715 (fc feature-row check).

## Work Completed
1. **SOL producer extension (operator-approved).** `FORECAST_SYMBOLS` default → `BTCUSDT,ETHUSDT,SOLUSDT` in `run_forecast_producer.sh` (repo, PR #5702) AND applied on the trainer via relay #5701. First production wrote a valid `SOLUSDT.json` (fresh `as_of_ts`, all six `fc_*` populated); the 15-min `ict-trainer-forecast.timer` keeps it warm.
2. **SOL fc head promoted candidate→shadow.** `python -m ml promote-stage sol-regime-15m-lgbm-fc-pcv-v1 --new-stage shadow` (stage_history 11:12:55Z, approval + A/B evidence in the reason); publish kicked, `published → 141.145.193.91`.
3. **Live soak verified end-to-end** (diag #5714): the head accrues on the money box — first shadow prediction **11:14:41Z, under 2 minutes after promotion** (hot-reload, no restart), 2 preds by 11:30, scores 0.64/0.75 non-degenerate. fc-conditioning (populated `fc_*` in `feature_row`) checked via #5715, mirroring the BTC verification record.
4. **Trainer git-auth durable fix.** `vm-git-credential-bootstrap.yml` extended with a `target: live|trainer` selector (trainer → `158.178.209.121`, recovery = plain `git reset --hard origin/main`, no deploy script; target validated against a fixed enum before interpolation). Dispatched post-merge for the trainer; closes `BL-20260706-TRAINER-GIT-AUTH-BROKEN` once verified.
5. **ETH/SOL vol-gate readiness recorded** (not executed — it needs research): base heads accruing healthily at shadow (`eth-regime-15m-lgbm-v1` 856 preds / `sol-regime-15m-lgbm-v1` 820, both since 06-28); remaining go-live steps written into `MB-20260628-VOLGATE-GOLIVE` (per-symbol cell evidence → per-symbol head advisory promotion → cell authoring, all Tier-3/evidence-gated) — first candidate work item for the next research session.

## Validation Performed
- Producer: relay #5701 journal shows the 11:12 run writing **3** artifacts (prior runs wrote 2 — the delta is exactly the change); `SOLUSDT.json` content verified (fc_row populated, `as_of_ts` current bar).
- Promotion: registry JSON re-read post-promote — `target_deployment_stage: shadow`, stage_history entry present.
- Soak: diag #5714 shadow_stats — `sol-regime-15m-lgbm-fc-pcv-v1 @ shadow, count 2, first_seen 11:14:41`.
- Workflow YAML: `yaml.safe_load` clean; target resolution is enum-validated before any interpolation into the SSH command.
- CI green on PR #5702 before merge.

## Documentation Updated
- `ROADMAP.md` T0.4 row — SOL win + graduation appended.
- `docs/research/SOL-fc-family-AB-evidence-2026-07-06.md` — disposition flipped to APPLIED with the verification trail.
- `docs/claude/ml-review-backlog.json` — `MB-20260705-FC-ADVISORY-READINESS` (3-symbol soak) + `MB-20260628-VOLGATE-GOLIVE` (readiness + remaining steps) evidence appended.
- This log.

## Soak / clock inventory (the standing watch-list as of session close)

Every running accrual clock, its watch surface, gate, and owning backlog item —
the `/system-review` soak_status coverage + `/ml-review` should walk this list.

| # | Clock | Watch surface | Gate / next read | Backlog item |
|---|---|---|---|---|
| 1 | **fc shadow soak — BTC+ETH+SOL 15m fc heads** (BTC 298 preds since 07-03 · ETH 169 since 07-04 · SOL started 11:14Z today) | `/api/diag/shadow_stats?model_id=<id>`; powered replay `scripts/ml/rg4_targeted.sh` (D4a rails: freshness gate + POWERED/UNPOWERED verdict) | **Powered RG4 ~mid-July**: ≥40–50 labeled volatile bars/symbol across ≥5 distinct episodes, logit-CI AUC vs frozen detector; then head-pinned money-gate walk-forward; then Tier-3 fc→advisory proposal | `MB-20260705-FC-ADVISORY-READINESS` |
| 2 | **D1 fc-geometry soak** (one row per live opening order; deployed 07-05) | `/api/bot/fc-geometry/soak` · diag `log_file=fc_geometry_soak`; trainer resolver `scripts/ml/fc_geometry_resolve.py` (censoring-aware) | Months-scale n; gate = real net-R/maxDD improvement under account rulesets; any geometry change Tier-3 | `MB-20260705-FC-SLTP-GEOMETRY` |
| 3 | **D2 label-wall accrual** (spike-A closed structural-negative 07-06) | `trade_journal.db::trades` real vs paper counts by window | Re-run pooled meta-label spike when pre-cutoff paper n_train ≈ real n_train (paper ramped 2026-06+ → likely weeks) | `MB-20260705-META-LABEL-WALL` |
| 4 | **ETH/SOL vol-gate base-head soak** (eth 856 / sol 820 preds @ shadow since 06-28) | `/api/diag/shadow_stats`; cells in `config/regime_policy.yaml::trend_vol` (BTC-only today) | Per-symbol vol-split cell-attribution study (mirror `A-vol-gating-OFFcell-design-2026-06-27`) → per-symbol walk-forward → head shadow→advisory + cell authoring (both Tier-3) | `MB-20260628-VOLGATE-GOLIVE` |
| 5 | **Exit-ladder soak** (laddered-vs-single-target per executed order) | `/api/bot/exit-ladder/soak` · diag `log_file=exit_ladder_soak` | P4 (graduate ladder to the real exit) = Tier-3 + backtest-gated on accrued soak data | `PB-20260617-002` (performance backlog) |
| 6 | **Allocator soak** (M18 P0c would-pick vs routed + regret, ≥2-candidate ticks) | `/api/bot/allocator/soak` | M18 P1 swaps in the cost-aware EV_net scorer (buildable); P2+ (allocator selects) backtest-gated | M18 roadmap rows |
| 7 | **Conviction sizing/arbitration soaks** (observe-only unified-confidence) | diag `log_file=conviction_sizing` / `conviction_arbitration` | P4/P5 graduation operator+backtest-gated (symmetric sizing FAILED its A/B — stays off) | roadmap M14/conviction rows |
| 8 | **News-layer soak** (rss active, veto armed) | `/api/bot/news/recent` | `NEWS_INFLUENCE_MODE` graduation Tier-3 | news roadmap rows |

## Contradictions or Drift Found
- None new. The trainer git-auth item moves from "interim relay workaround" to "durable fix dispatched" — both recorded in the same backlog item.

## Risks and Follow-Ups
- The trainer bootstrap's `git reset --hard origin/main` intentionally discards the relay-written interim files — safe ONLY because #5702 merged first (identical content on main). Recorded here in case a future session repeats the pattern: **merge before bootstrap-reset**.
- SOL fc soak is 2 predictions old — the 3-symbol volatile-episode arithmetic in `MB-20260705-FC-ADVISORY-READINESS` now accrues ~⅓ faster, but the powered-RG4 date stays ~mid-July (per-symbol thresholds).

## Deferred Items
- **ETH/SOL vol-gate go-live** (research: per-symbol cell evidence + walk-forward; then Tier-3 promotions) — first candidate for the next research session.
- **M18 P1 EV_net scorer** (buildable non-research follow-up if wanted).
- **D3** stays dormant on its trigger.

## Next Recommended Sprint
The next session is a **fresh deep-research session** (operator-directed). Prompt handed to the operator at session close; the soak inventory above is its starting state.

## Wrap-Up Check
- [x] Code inspected directly (producer script/unit installer/CLI promote-stage/bootstrap workflow — file:line in Files/Systems).
- [x] Docs reviewed/updated (ROADMAP, evidence doc, backlogs, this log).
- [x] TRADE-PIPELINE untouched (shadow is observe-only; no pipeline stage changed).
- [x] Roadmap checked + updated (T0.4 row).
- [x] Contradictions recorded (none new).
- [x] Unknowns stated plainly (SOL soak young; powered RG4 still ~mid-July).
- [x] Promotion stayed within the pre-authorized shadow ceiling; shadow→advisory remains Tier-3.
