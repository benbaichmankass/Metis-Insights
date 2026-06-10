# Sprint Log: S-M8-STRATEGY-TUNING-S2

## Date Range
- Start: 2026-06-09
- End:   2026-06-09

## Objective
- Primary goal: add **walk-forward / out-of-sample validation** to the M8 sweep
  harness — the validity gate that turns S1's in-sample directional signal into
  merge-ready Tier-3 evidence. Then re-run the `trend_donchian.min_confidence`
  sweep with a widened grid + an OOS split.
- Secondary goal: make the harness honest about in-sample-only runs (flag them
  as NOT OOS-validated) so a full-history optimum can't masquerade as go-live
  evidence.

## Tier
- Tier 1. Harness-only change (additive `--oos-start`); writes nothing to
  `config/`. The result proposes a Tier-3 value; the operator applies it.

## Starting Context
- M8 S0 (harness) + S1 (first real run + fixed_args/extractor/`--long-only`)
  merged to `main` in PR #3140 (squash, `5cea1bd`). This branch restarted from
  that merged base.
- S1 left two gaps for S2: the optimum sat at the grid boundary (0.60), and the
  run was full-history **in-sample** — the live 0.30 was set under walk-forward
  CV, so an in-sample sweep isn't comparable evidence.

## Work Completed
- **`scripts/ml/strategy_tune_sweep.py` — walk-forward / OOS split.**
  - `WalkForward(oos_start, train_start?, oos_end?)` + `--oos-start` /
    `--train-start` / `--oos-end` CLI (and recipe passthrough). `HarnessSpec`
    gains `window_flags=("--start","--end")` (all research harnesses + vwap).
  - With a split, each grid value runs on **both** windows; grid rows + all
    picks become **OOS-gated** (top-level metrics = OOS; in-sample nested under
    each row's `train`). Native sweeps run twice (train, oos) and join by value.
  - Result envelope carries `metric_basis` (`oos` | `full_sample`) +
    `walk_forward`; the recommendation adds `train_oos_consistent` (the OOS
    optimum must also be net-positive in-sample).
  - Without a split the result is explicitly flagged **IN-SAMPLE / not
    OOS-validated** (grid header + recommendation detail) so it can't pass as
    go-live evidence.
  - +6 tests (44 total); `docs/strategy-tuning.md` § Walk-forward + change log.

## Validation Performed — OOS-validated tune result
Trainer VM, `data/btc_1h_multiyear.csv` (2021-01-01 → 2026-06-01), live params
(`--timeframe 1h --donchian 20 --atr-stop-mult 2.5 --trail-mult 5.0 --long-only`),
grid `uniform [0.3, 0.9]` (13 pts), fee 7.5 bps, **OOS split at 2025-01-01**
(train ≈4 yr, OOS ≈17 mo). Relay #3168.

| min_conf | OOS trades | OOS net R | OOS exp | OOS maxDD | train net R | train exp |
|---|---|---|---|---|---|---|
| **0.30 (live)** | 126 | **−14.00** | **−0.111** | 22.27 | 79.31 | 0.230 |
| 0.40 | 119 | −11.06 | −0.093 | 20.35 | 76.14 | 0.235 |
| 0.45 | 112 | −6.28 | −0.056 | 17.36 | 75.22 | 0.238 |
| 0.50 | 105 | −8.02 | −0.076 | 13.39 | 67.13 | 0.216 |
| **0.55** | 93 | **+5.01** | +0.054 | 8.13 | 60.94 | 0.202 |
| 0.60 | 92 | +3.66 | +0.040 | 8.13 | 75.96 | 0.264 |
| 0.70 | 84 | +4.57 | +0.054 | 9.53 | 71.62 | 0.262 |
| 0.90 | 74 | +4.22 | +0.057 | 8.94 | 80.55 | 0.349 |

- **Headline finding: the live `min_confidence: 0.30` is net-NEGATIVE
  out-of-sample** (−14.0 R, expectancy −0.111 over 2025-06→2026-06). The S1
  in-sample sweep showed 0.30 → +65.9 R — that signal was dominated by 2021-24
  and **masked an OOS regime where the low floor bleeds.** This is exactly the
  failure the OOS gate exists to catch.
- OOS only turns net-positive at **min_confidence ≥ 0.55**, which also **roughly
  thirds the OOS max drawdown** (22.3 → ~8 R). The 0.55–0.90 plateau is broadly
  positive (+1.8 to +5.0 R).
- Harness recommendation: `propose_value 0.90` (best OOS expectancy ≥20 trades),
  `beats_baseline=True`, `train_oos_consistent=True`. But the actionable read is
  **"raise the floor into the 0.55–0.90 OOS-positive plateau"**, not a fixation
  on 0.90 (which is near the grid edge again, though the plateau is flat).

## Documentation Updated
- `docs/strategy-tuning.md` — new § "Walk-forward / OOS validation (the go-live
  gate)" + change log.
- ROADMAP M8 row updated with the S2 OOS finding.

## Contradictions or Drift Found
- **The S1 directional read (0.30 → ~0.60) is confirmed and sharpened, but its
  framing was in-sample.** S2 supersedes it: the merge-ready basis is OOS, and
  on OOS the live 0.30 is a *loser*, not merely sub-optimal. Recorded here so a
  future reader doesn't cite S1's +66 R in-sample number as go-live evidence.

## Risks and Follow-Ups
- **Single fold.** S2 ships a single chronological train/OOS holdout. A fully
  robust packet would use **k-fold walk-forward** (the live 0.30 was set under
  3-fold). S3 candidate: add k-fold WF to the harness and re-confirm the plateau
  holds across folds before any Tier-3 proposal firms.
- **Thin OOS edge.** Even at the optimum the OOS net is a few R over 17 months;
  the clearer win is risk reduction (drawdown + sign flip). The operator should
  weigh whether the plateau's edge justifies a change vs. other levers.
- **Tier-3 decision (operator):** whether to raise
  `trend_donchian.min_confidence` from 0.30 into the 0.55–0.90 band. This is a
  live, real-money strategy — proposed, not applied.

## Deferred Items
- k-fold walk-forward (multi-fold robustness) in the harness.
- Dashboard surfacing of `runtime_logs/strategy_tunes/` (read route + Streamlit).
- Registry expansion to more `(harness, param)` pairs as gate packets demand.

## Next Recommended Sprint
- **S-M8-STRATEGY-TUNING-S3** — k-fold walk-forward in the harness; re-run the
  trend floor across folds to confirm the 0.55–0.90 OOS plateau is robust (not a
  single-split artifact), producing the packet the operator can act on. Then
  surface tune results on the dashboard.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage — n/a, research tooling only.
- [x] Roadmap status was checked and updated.
- [x] Contradictions were recorded (S1 in-sample framing superseded by OOS).
- [x] Remaining unknowns stated (single fold; thin OOS edge — both → S3).
