# Sprint Log: S-ML-FEATURE-GAPS-20260729

## Date Range
- Start: 2026-07-29
- End: 2026-07-29

## Objective
- Primary goal: Close the **data-blocked ML feature gaps** (roadmap-toolbox
  assessment 2026-07-29 §5 rec #4). Thesis: *every "no-edge" ML head may be an
  input problem, not a model problem — rule that out before concluding a model
  type is dead.* For each named starved head, diagnose **input-problem vs
  genuinely-dead-model**, then fix the data capture (Tier-1/2 observability, no
  order path). Folds into M36 consolidation.
- Secondary goals: land the findings durably (ml-review-backlog + ROADMAP M14
  row + this log); confirm the two prior-session docs PRs (#7897, #7899).

## Tier
- Tier 1 (trainer-side build tooling + observe-only diagnosis + docs).
- Justification: the one code change (`build_microstructure` in
  `scripts/ops/build_trainer_datasets.sh`) is opt-in default-off trainer build
  tooling — no live-VM, no order path, no schema/API change. The order-flow head
  it feeds is `research_only`, so any positive A/B only *proposes* shadow (the
  `shadow → advisory` gate stays Tier-3 / operator). The A/B itself was run
  `--no-register` on a throwaway dataset version + throwaway registry.

## Starting Context
- Active roadmap items: M14 (ML-Optimization, S-MLOPT-S10 order-flow; S11
  funding/OI), M36 (consolidation). Assessment rec #4.
- Prior sprint reference: broker-truth cost-coverage rollout (rec #7, #7891/#7895)
  + macro-research skill (rec #6, #7884) + M1 econ event-study (#7889), all this
  cycle.
- Known risks at start: 1-OCPU / 6 GB trainer compute ceiling (rec #3); the
  named heads had been carried "kept_open / still data-blocked" across many
  `/ml-review` runs without a fresh row-count check.

## Repo State Checked
- Branch or commit reviewed: `main` @ `76e35fe`.
- Deployment state reviewed: trainer VM `ict-orderflow-capture.service` (via the
  `trainer-vm-diag` relay, issues #7900 / #7902 / #7904).
- Canonical docs reviewed: CLAUDE.md, `docs/claude/ml-review-backlog.json`,
  `docs/research/roadmap-toolbox-assessment-2026-07-29.md`, ROADMAP M14 row.

## Files and Systems Inspected
- Code files inspected: `scripts/ops/build_trainer_datasets.sh` (build_family /
  build_bybit_features / build_funding_oi), `ml/datasets/families/market_features.py`
  (the `microstructure_path` join — supported since S-MLOPT-S10), `ml/cli.py`
  (`train` / `compare`), `scripts/ml/eval_split_compare.py`,
  `ml/datasets/orderflow_features.py`, `scripts/ml/orderflow_capture.py`.
- Config files inspected: `ml/configs/btc-regime-5m-lgbm-flow-v1.yaml`,
  `ml/configs/btc-regime-5m-lgbm-v2.yaml`.
- Deployment files inspected: `deploy/trainer/ict-orderflow-capture.service`.
- Docs inspected: ml-review-backlog `MB-20260604-002`, `MB-20260613` (flow head),
  `MB-20260726-OI-HISTORY-LIMIT`; ROADMAP `S-MLOPT-S10`, `S-TRAINER-RESOURCE-OOM`.
- Services or timers inspected: `ict-orderflow-capture.service` (trainer VM).
- GitHub Actions workflows inspected: `trainer-vm-diag` relay.

## Work Completed
- **Diagnosis (the deliverable).** Validated the two named starved heads against
  live trainer state, not the stale backlog notes:
  - **`btc-regime-5m-lgbm-flow-v1` (VPIN / order-flow) — INPUT PROBLEM, not a dead
    model.** The `ict-orderflow-capture.service` side-car is **alive and healthy**:
    `active (running)`, **15,422 forward BTCUSDT 5m bars** accrued (2026-06-04 →
    2026-07-29T11:10Z, ~8 weeks), sensible ofi/vpin values, only benign per-poll
    timeouts (trainer-vm-diag #7900). That is **~4× past the ~4,000-bar A/B
    threshold** the head was waiting on. **Normalization failure surfaced:** the
    data had been sufficient for ~6 weeks while every `/ml-review` carried the item
    "still data-blocked, kept_open" without re-checking the row count — the exact
    desensitized-alarm pattern CLAUDE.md § "If you see something, say something"
    exists to kill.
  - **The join was never wired (F5, ml-infra audit 2026-07-19).** The
    `market_features` builder has supported `microstructure_path` since S-MLOPT-S10
    (`market_features.py:512,585`), but `build_trainer_datasets.sh` never passed it,
    so the head's `ofi/ofi_zscore/vpin/order_imbalance/rel_spread_mean/microprice_dev`
    columns stayed 0.0 and it collapsed to `btc-regime-5m-lgbm-v2`. **Fixed** by
    adding `build_microstructure` (PR #7901), mirroring `build_funding_oi`: opt-in
    (`ICT_BUILD_MICROSTRUCTURE=1`, default off), 5m-only, run LAST so nothing
    rebuilds the BTC 5m shard after it (clobber-safe, the `MB-20260726-FC-CLOBBER`
    ordering discipline).
  - **`btc-regime-1h-lgbm-funding-v1` (OI) — genuinely data-source-limited.**
    `open_interest_change` ~99.6% dead is Bybit's OI-history depth vs the ≥5y funding
    window (`MB-20260726-OI-HISTORY-LIMIT`); operator already decided 2026-07-26 to
    leave it silenced (research_only/offline, zero live-order impact). Diagnosis
    complete; not reopened.
- **A/B run (captured-window, --no-register)** to answer input-vs-model for the
  flow head (trainer-vm-diag #7902 / #7904). Built a captured-window-only 5m
  `market_features` shard WITH the microstructure join and trained both heads on it
  (the correct cut — the full 5y shard both dilutes the flow columns to ~0 for 97%
  of rows AND OOMs the 6 GB trainer when the flow head trains alone,
  `BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`; ~15k windowed rows sidestep both):
  - **The join works** — flow columns are **97–99% populated** over 15,816 windowed
    bars (ofi 97.6%, vpin 99.4%, order_imbalance 98.9%, rel_spread 98.3%,
    microprice_dev 98.3%). Order-flow is confirmed **not** a dead-model / broken-join
    problem.
  - **But the captured window is a low-volatility regime, so the A/B is currently
    UNMEASURABLE.** `regime_label` over the window = **{range: 15,774, volatile: 42}**
    — volatile is **0.27%**, and **support_volatile = 0 in the last-20% eval fold**.
    Both heads collapse to the trivial all-range predictor (`accuracy 1.0,
    f1_volatile 0.0` for *both*), so there is no volatile signal for order-flow to
    lift. **New, precise data gap:** the head is no longer bar-count-blocked (15k is
    plenty) — it is **volatile-regime-coverage-blocked**. It needs the capture to
    accrue across a higher-vol era before `f1_volatile` lift is measurable (the
    label's `vol_threshold=0.005` is rarely crossed in this quiet BTC summer). This
    is a data-distribution fact, not a model or join defect.

## Validation Performed
- Tests run: `bash -n scripts/ops/build_trainer_datasets.sh` clean;
  `tests/ml/test_dataset_audit.py` 21 passed / 1 skipped; `tests/test_microstructure.py`
  8 passed. (Full app-import test collection fails in the sandbox — pyo3 panic /
  missing deps — unrelated to this change; CI covers it.)
- Dry-runs or staging checks: the A/B build + both trainings ran on the trainer VM
  via the relay, `--no-register` + throwaway dataset version + throwaway registry
  root (nothing registered, no live model touched).
- Manual code verification: confirmed the `microstructure_path` join is supported
  by the builder and only unwired in the build script; confirmed `build_microstructure`
  is 5m-scoped and ordered after `build_funding_oi` (1h) / `build_bybit_15m_fc` (15m)
  so it cannot be clobbered.
- Gaps not yet verified: the join has not run inside a real nightly cycle yet
  (default-off flag; deploys on the next trainer main-sync). The A/B verdict is
  "unmeasurable this window," not a lift number.

## Documentation Updated
- Roadmap updates: M14 `S-MLOPT-S10` row updated — capture alive @ 15,422 bars,
  join wired (#7901), A/B unmeasurable (volatile-scarce window).
- Subsystem doc updates: `ml-review-backlog` `MB-20260604-002` + `MB-20260613`
  updated with the diagnosis, the join fix, and the volatile-coverage blocker;
  `MB-20260726-OI-HISTORY-LIMIT` confirmed genuinely-data-limited + operator-parked.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- The backlog carried the flow head as "DATA-BLOCKED … awaits forward L2 capture"
  when the capture had in fact accrued 15,422 bars ~6 weeks earlier — a stale
  status the fresh row-count check corrected. (Filed as the normalization finding
  above.)

## Risks and Follow-Ups
- Remaining technical risks: the flow head still OOMs the 6 GB trainer when trained
  full-history alone (`BL-20260717-TRAINER-SINGLE-MANIFEST-OOM`) and is quarantined
  in the nightly cycle — so even with the join wired, a *full-window* nightly
  retrain won't run until the OOM is resolved (shrink / GPU-burst / drop). The
  captured-window A/B path shown here is the OOM-free way to evaluate it.
- Remaining product decisions (Tier 3): promotion of the flow head past
  `research_only` — only if/when a volatile-inclusive captured window shows lift.
- Blockers: **volatile-regime coverage** in the captured window (data-distribution,
  time-gated) — the capture must span a higher-vol era before the A/B is measurable.

## Deferred Items
- Re-run the captured-window A/B once the capture spans a meaningfully volatile era
  (target: ≥ a few hundred volatile 5m bars, with volatile support in the eval fold).
- OI head deeper-history sourcing — parked per the 2026-07-26 operator decision;
  revisit only if a funding head becomes a promotion candidate.

## Next Recommended Sprint
- A periodic (monthly) `/ml-review` check on the order-flow captured-window volatile
  support; run the A/B when volatile coverage clears the floor. Separately, resolve
  `BL-20260717` (flow-head OOM) so the join can also feed the nightly cycle.
