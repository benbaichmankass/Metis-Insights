# Sprint Log: S-OFFLINE-VOL-AXIS-20260730

## Date Range
- Start: 2026-07-30
- End: 2026-07-30

## Objective
- **Primary goal:** Build the OFFLINE ML vol axis — path **(A)** of
  `BL-20260730-2D-VOL-CELLS-UNAUDITABLE` (severity high). Reproduce
  `src/runtime/intents.py::_decision_vol_regime` offline so the six live 2-D
  `trend_vol` cells become measurable, and so a 1-D regime grade can no longer
  be mistaken for one that covered the vol axis.
- **Secondary goals:** merge the outstanding PR #8051 (the scoping row for this
  work); establish, by trainer relay, what the replay actually needs.

## Tier
- **Tier 1.** Research tooling + docs + backlog only.
- **Justification:** adds two `scripts/research/` entry points and a test file;
  touches no `src/`, no `config/`, no live path, no order path. It **proposes no
  cell change** — it is the instrument, not a verdict. (The eventual act of
  authoring/retiring a cell off its output stays Tier-3.)

## Starting Context
- **Active roadmap items:** the authored-regime-cell re-audit line
  (`BL-20260730-REGIME-CELL-UNAUDITABLE` → `-2D-VOL-CELLS-UNAUDITABLE` →
  `-AUTHORED-CELL-REAUDIT-REGISTER`).
- **Prior sprint reference:** `S-M1-SURVEY-CONSENSUS-NULL-20260730` (the session
  that scoped this work and deliberately did **not** build it).
- **Known risks at start — both were named in the backlog row and both held:**
  1. Building on `src/runtime/regime/vol_detector.py` would measure a population
     live never gates on, whose documented behaviour is *opposite* (that
     function's own docstring: the cells "were authored under the ML label and
     LOSE money under the frozen label"). The shortcut is pure, injectable, and
     looks like an hour's work — which is exactly why it was pre-registered as a
     **no**.
  2. `docs/research/artifacts/ict_scalp_phase0/btc{5m,15m}_volspec.json` name
     `btc-regime-{5m,15m}-baseline-v1`, not the current advisory head — stale as
     well as wrong-mechanism.

## Repo State Checked
- Branch/commit reviewed: `main` @ `dc6b0a0` → merged `d4c1261` (#8051); work
  branch `claude/offline-vol-axis-regime-5u1p7k`.
- Deployment state reviewed: trainer VM inventory via the `trainer-vm-diag`
  relay (issues #8054, #8055, #8057, #8059) — registry entry, artifact, built
  datasets, forecast side-streams, venv.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md` (full), root
  `CLAUDE.md`, `docs/research/RESEARCH-CAPABILITY-INDEX.md`, the three backlog
  rows above.

## Files and Systems Inspected
- **Code inspected (read, not inferred):** `src/runtime/intents.py`
  (`_decision_vol_regime`, `_shadow_regime_gate`),
  `src/runtime/regime/ml_vol_verdict.py` (whole),
  `src/runtime/regime_shadow.py::feature_row_for_predictor` / `regime_spec_of`,
  `src/runtime/runtime_flags.py::_ml_vol_verdict_threshold`,
  `ml/predictors/lightgbm.py`, `ml/predictors/shadow.py`,
  `ml/shadow/backfill.py::_instantiate_predictor`, `ml/registry/model_registry.py`,
  `ml/datasets/forecast_features.py`, `ml/datasets/families/market_features.py`,
  `scripts/research/regime_tag_emitted.py`, `scripts/research/analyze_exit_head.py`
  (the replay precedent), `scripts/ops/check_research_index.py`.
- **Config inspected:** `ml/configs/btc-regime-15m-lgbm-fc-pcv-v1.yaml`.
- **Docs inspected:** the capability index, the three review backlogs,
  `.claude/settings.json` (the merge/board guards).

## Work Completed

1. **PR #8051 merged** (`d4c1261`) under the full merge protocol — board read,
   `🔒 CLAIM` posted on #6927, branch synced to `origin/main` last, 15/15 green
   on the synced head, `🔓 RELEASE` posted.

2. **`scripts/research/ml_vol_label_replay.py` (new)** — replays the **advisory**
   regime head offline into per-bar `calm`/`volatile` labels.
   - Head selection and the threshold resolve through the **router's own**
     `discover_advisory_stage_regime_specs` / `_advisory_entry_for_symbol` /
     `_ml_vol_verdict_threshold`, **not** a hardcoded model id — BTC's advisory
     head was already swapped once (2026-07-20), so a pinned id would silently
     drift. `--model-id` remains available to replay one specific historical head.
   - `verify` sub-command compares replayed labels against the **live gate's own**
     `regime_ml_vol_shadow` / `regime_hard_gate` audit rows.
   - Batched scoring is **verified against per-row `predict_proba`** (1e-9) on a
     stride sample and aborts on mismatch — speed never buys a different answer.

3. **`scripts/research/regime_tag_emitted.py` (extended)** — `--vol-labels`,
   `--only-vol`, a `by_cell` breakdown keyed like the authored cells
   (`"trending/calm"`), per-cell `exp_r`/`long_exp_r`/`short_exp_r`, a
   `vol_coverage` block, and an explicit **`vol_axis: present|absent`**
   declaration on every run (JSON *and* console).

4. **Guardrails against the exact failure class this work exists to end**
   (§ "Green is not evidence"): empty/all-`unknown` labels → **exit 2**, not a
   vacuous 2-D grade; unlabelled trades land in `*/unknown` with coverage %
   printed, never folded into `calm`; `verify` with no overlap returns
   `no_overlap_nothing_verified`, which is **not** a pass; a malformed labels
   line raises rather than grading a holed population; fidelity flags
   (`in_sample: true`, `live_verified: false`) are written **into the labels
   manifest**, not just logged.

5. **Capability index + backlog updated** — the new tools routed in §2 of
   `RESEARCH-CAPABILITY-INDEX.md` with two new ⚠️ notes (the vol axis is a
   *population* fidelity question, and the label comes from the ML head not
   `vol_detector`); `BL-20260730-2D-VOL-CELLS-UNAUDITABLE` updated with an
   honest "tool built, nothing measured yet" entry and left **open**.

6. **Trainer inventory completed (#8059) — it validates the design end-to-end**,
   which matters because every one of these was an assumption until checked:
   - the head loads: `LightGBMMulticlassPredictor`, `class_labels ('range',
     'volatile')`, **13 features** exactly matching the manifest, `regime_spec`
     `BTCUSDT`/`15m`. So the replay's `predict_proba(row)["volatile"]` read is
     correct against the real artifact, not just against the manifest.
   - the **router itself** resolves `BTCUSDT → btc-regime-15m-lgbm-fc-pcv-v1`;
     `ETHUSDT`/`SOLUSDT`/`XRPUSDT` → `None` (consistent with SOL's 2026-07-26
     demotion and ETH still being shadow — so those symbols genuinely have no ML
     vol axis to replay, and live keeps the frozen label for them).
   - `ML_VOL_VERDICT_THRESHOLD` = **0.5** live.
   - `.venv`: python 3.11, lightgbm 4.6.0, pandas 3.0.3, numpy 2.4.4 — **no
     torch/Chronos needed**, since `v520` already carries the `fc_*` columns.
   - **Unlooked-for finding:** `MES` also has an advisory head
     (`mes-regime-5m-lgbm-v2`, 5m). The vol axis is therefore available for
     MES-symbol strategies too, not only BTC — worth knowing before anyone
     assumes this tool is BTC-only.
   - **Filed, not walked past:** the trainer root fs is at **95% (2.4 GB free)**
     against a workload writing ~480 MB per dataset build, with 16 sibling
     `market_features/BTCUSDT/15m/` versions present →
     `BL-20260730-TRAINER-DISK-95PCT`. A build that runs out of disk mid-write
     produces a truncated-but-present artifact, which is the same
     "green but vacuous" class the binding rule exists for.

## Validation Performed
- **Tests run:** 21 new (`tests/test_regime_vol_axis.py`) — all pass. The
  load-bearing one is a **parity test that drives the real live
  `ml_vol_regime_for_symbol`** through its per-bar publish cache and asserts the
  replay lands on the same label across `P(volatile) ∈ {0, …, 0.4999999, 0.5,
  0.5000001, …, 1.0}` — the `>=` boundary is tested against the gate, not
  restated. Plus: as-of join never reads a future bar; a pooled trend cell that
  hides an opposite-signed vol sub-cell splits correctly and **conserves the
  pooled total**; unlabelled trades are counted not absorbed.
- **Regression:** 71 existing regime tests
  (`test_regime_cell_walkforward`, `test_regime_debt_matrix_{fee,squeeze}`,
  `test_aggregate_intents_{ml_vol_shadow,regime_hard}`) still green.
- **CLI end-to-end** on synthetic fixtures: 1-D output byte-compatible with
  before; 2-D table renders; single-cell isolation (`--only-regime trending
  --only-vol calm`) emits the expected subset; the vacuity refusals return the
  intended exit codes.
- **Guards:** `check_research_index` (52/52 routed), `check_artifact_validity`,
  `check_backlog_refs --all`, `ruff` — all clean. CI green on the PR.
- **Gaps not yet verified — stated plainly:** the replay has **not been run**,
  so **no cell has been measured**. `verify` has not been run against live audit
  rows, so the offline-vs-serve feature-row caveat is **untested**. Both are the
  next sprint's first two steps.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none (no architecture change — research tooling).
- Trade pipeline doc updates: N/A (no pipeline stage touched).
- Roadmap updates: none this sprint; the work is tracked in the backlog row.
- Subsystem doc updates: `docs/research/RESEARCH-CAPABILITY-INDEX.md` §2.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **My own probes failed silently-ish three times, and that is worth recording**
  rather than quietly re-running: #8054 lost sections 3–7 to nested quotes,
  #8055 lost F/G to unquoted parens in an `echo`, #8057 lost G/H to an indented
  heredoc terminator **and** a wrong venv path (`venv` vs the real `.venv`). In
  each case the relay returned a *partial* result that still looked like output.
  Every missing fact was re-asked rather than assumed — which is the same
  discipline the "assert the inputs" obligation demands of data, applied to
  instruments.
- **A measurement I made and then withdrew:** an early probe reported "8,873 of
  the first 20,000 rows have `fc_ret_med` exactly 0.0" (≈44% neutral defaults).
  That was **my grep's false positive** — `"fc_ret_med": 0.0` is a prefix of
  `"fc_ret_med": 0.00019…`. It is not a finding and must not be cited as one.
  Whether live `fc_*` match the dataset's is exactly what `verify` mode settles.
- No doc-vs-code contradiction found in the regime/vol subsystem; the
  `intents.py` docstring's per-SYMBOL claim was confirmed against
  `ml_vol_regime_for_symbol` directly.

## Risks and Follow-Ups
- **Remaining technical risks:**
  - **In-sample.** The production artifact is fit on full history, so replayed
    labels inside the training window come from a model that saw those bars.
    Weaker than in-sample backtest bias (this is a market-state label, not a
    performance prediction) but real; stamped as `fidelity.in_sample: true`.
  - **Serve-path difference.** Live builds the feature row from live candles
    (`feature_row_for_predictor`); the replay reads the offline builder's row.
    Designed to agree (S-MLOPT-S17) but not the same code path — `verify` is the
    empirical test and has not been run.
  - **Label span.** `market_features/BTCUSDT/15m/v520` spans **2021-07-01 →
    2026-06-30 only**, so any emitted trade after 2026-06-30 labels `unknown`.
    The fresher forecasts side-stream `v002` already runs to 2026-07-29, so
    closing the gap is a `market_features` rebuild, not new tooling.
- **Remaining product decisions (Tier 3):** unchanged and still withheld — the
  `squeeze_breakout_4h` trending-short OFF-cell proposal stays withheld until
  the six 2-D cells are actually graded on this axis. Also still open with the
  operator: the M1 gate wording (`BL-20260730-M3-GATE-TESTS-TRACKING-NOT-USEFULNESS`).
- **Blockers:** none. The next step needs only the merged tool + a trainer run.

## Deferred Items
- **Running the replay + `verify` + grading the six cells** — deliberately not
  attempted in this sprint. The tool lands first so the trainer (which syncs
  `main`) can run it against the registry and `v520` without shipping a
  one-off script through the relay.
- **`regime_debt_matrix` / `regime_cell_walkforward` vol-split** — the labels
  file is now the shared primitive, but those two drivers still have no
  `--vol-labels`. Deferred deliberately: wiring them before the labels are
  *verified* would spread an unvalidated axis across three tools.
- **The authored-cell re-audit register** (`BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER`)
  — untouched this sprint, still open.

## Next Recommended Sprint
- **Suggested next sprint:** run and validate the axis, then grade.
  1. Trainer relay: `ml_vol_label_replay.py replay --symbol BTCUSDT --dataset
     datasets-out/market_features/BTCUSDT/15m/v520/data.jsonl`.
  2. Pull `regime_ml_vol_shadow` / `regime_hard_gate` rows via
     `/api/diag/audit_query` and run `verify`. **Treat a low agreement rate as a
     finding, not a rounding issue.**
  3. Only then: grade the six 2-D cells and record each verdict.
- **Why next:** six live cells drop real BTC intents on evidence nothing has
  re-checked, and one Tier-3 proposal is already blocked behind this.
- **Required verification before starting:** the labels manifest must show a
  non-trivial `calm`/`volatile` split (a 100/0 split is a broken replay, not a
  regime finding), and `verify` must report a real `comparable` count — not
  `no_overlap_nothing_verified`.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] N/A — no pipeline stage touched.
- [x] Roadmap status was checked (tracked in the backlog row, not a roadmap row).
- [x] Contradictions were recorded — including my own withdrawn measurement.
- [x] Remaining unknowns were stated clearly: **nothing has been measured yet.**
