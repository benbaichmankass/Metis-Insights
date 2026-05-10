# S-AI-WS8-PART-3 — Shadow-prediction drift detector

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS8-PART-1.md`](S-AI-WS8-PART-1.md), [`docs/sprint-logs/S-AI-WS8-PART-2.md`](S-AI-WS8-PART-2.md)
**Status:** ✅ COMPLETE — window-over-window drift; external-reference variant filed.

## Goal

Close the WS8 monitoring loop with **drift detection**. Shadow
predictions are now observable (PART-1 CLI, PART-2 dashboard);
PART-3 surfaces the question "is the model's score distribution
changing over time?" without needing labelled outcomes (which are
still operator-blocked on the deploy unlock + the WS5 baseline
training pass).

## Decisions

- **Window-over-window self-comparison, not reference vs production.**
  The cleanest version of drift detection needs an external
  "reference distribution" baked at training time. We don't have
  that yet — the registry stores metrics but not a histogram of
  the trained-on score distribution. So PART-3 ships
  **self-comparison**: compare scores from the last
  `reference_days` (default 30) against scores from the last
  `current_days` (default 7). Non-overlapping windows anchored at
  "now". Meaningful immediately; no infrastructure changes; trivial
  to extend later when a reference distribution exists.
- **Two metrics — KS + PSI.** They catch different drift shapes:
  - **Kolmogorov–Smirnov** is sensitive to ANY shape change;
    returns max |F_ref(x) − F_cur(x)|.
  - **Population Stability Index** is the industry-standard
    monitoring metric for score-based models; thresholds
    (<0.1 / 0.1–0.25 / >0.25) are battle-tested.
  Surfacing both lets the operator triangulate.
- **Plain-English verdict buckets.** `interpret_ks` and
  `interpret_psi` map raw stats to `{no_change, minor, moderate,
  significant}`. The compound `overall_verdict` is the WORSE of
  the two — "significant" if EITHER triggers it. Conservative by
  design: alerts on the union, not the intersection, so a single
  false-positive metric still surfaces the question.
- **Pure stdlib.** No numpy / scipy dependency. Sample sizes are
  bounded (one row per shadow tick over weeks → ~10⁴ to 10⁵), so
  the O(n log n) KS and O(bins) PSI are fast enough.
- **`smoothing=1e-4` on PSI** to avoid log(0) when a bin is empty
  on one side. Standard practice in production PSI implementations.
- **Histogram clamping at `[score_min, score_max]`.** Defaults to
  [0, 1] for probability-like outputs. Values outside the range
  are clamped to the nearest end-bucket — better than dropping or
  raising on a model occasionally spilling a sliver outside its
  nominal range.
- **`insufficient_data` is a terminal verdict, not an error.**
  When either window has zero records, the CLI / endpoint
  returns the verdict alongside the (zero) counts and the window
  boundaries. The operator sees "drift isn't computable yet"
  without an exception or HTTP 500.
- **Dashboard route is unauthenticated Tier 1**, matching the
  rest of `/api/bot/shadow/*`. Drift summaries reveal model
  behavior in aggregate — not secret-equivalent.

## Deliverables

- `ml/shadow/drift.py` (new) — `Summary`, `summarize`,
  `ks_statistic`, `interpret_ks`, `histogram`, `psi`,
  `interpret_psi`, `DriftReport`, `compute_drift`. ~210 LOC,
  stdlib-only.
- `ml/shadow/__init__.py` — drift symbols re-exported.
- `ml/cli.py` — new subcommand `python -m ml shadow-drift
  --model-id X [--stage X] [--log PATH] [--reference-days N]
  [--current-days N] [--bins N] [--score-min F] [--score-max F]`.
  Returns indented JSON to stdout for shell-friendly grep.
- `src/web/api/routers/shadow.py` — new
  `GET /api/bot/shadow/drift` route. Same filter surface as the
  CLI. FastAPI `Query` validation: `bins` ge=2 le=100,
  `*_days` gt=0 le=365.
- `CLAUDE.md` — new row in the Dashboard REST API table; new
  endpoint line in the architecture diagram.
- `tests/ml/test_shadow_drift.py` (new) — 23 unit tests across
  `summarize`, `ks_statistic`, `interpret_ks`, `histogram`,
  `psi`, `interpret_psi`, `compute_drift`, `_worst`.
  Identical-distribution → near-zero metrics; disjoint → ~1.0
  KS; shifted Gaussians → `significant`.
- `tests/ml/test_shadow_drift_cli.py` (new) — 4 end-to-end CLI
  tests: `insufficient_data` when log empty, full report when
  both windows populated (with significant drift), model_id
  filter, subcommand registered in parser.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/` — 350 / 350 pass (24
      drift unit + 4 drift CLI added; no regressions).
- [x] `ruff check` clean on all changed files.
- [x] Identical samples → `verdict=no_change`, ks<0.05, psi<0.05.
- [x] Disjoint samples → `verdict=significant`, ks≈1.0.
- [x] Realistic shifted Gaussians (μ=0.2 vs μ=0.8) → significant.
- [x] Empty windows → `verdict=insufficient_data` without raising.
- [x] PSI smoothing handles empty bins without log(0).
- [x] Histogram clamps out-of-range values to end buckets.
- [x] CLI returns indented JSON; endpoint returns envelope.

## Out of scope (filed for follow-ups)

- **External reference distribution.** Store a histogram of the
  model's training-set scores in the registry entry (new
  `metrics_distribution` field). The drift endpoint accepts
  `reference=training` to compare current scores against that
  fixed reference rather than a rolling time window. This is the
  "real" drift detection use case; the rolling-window variant is
  a useful early-warning indicator without it.
- **Alert thresholds + notifier.** Today the operator polls the
  CLI / dashboard. A WS8-PART-4 could fire a Telegram alert when
  `overall_verdict` flips to `significant`, anchored to a
  per-model deadband to avoid flapping.
- **Per-feature drift.** Today drift is computed on the score
  alone. If `row_keys` evolves (e.g. a new feature column lands
  in the model's input), the drift detector should flag the
  shape change as well. Filed.
- **Wasserstein / earth-mover distance.** Lower-floor for
  small-sample drift sensitivity than KS. Filed.
- **Multi-stage drift comparison.** When a model is promoted
  from `shadow` → `advisory`, compare distributions across
  stages to catch deployment-related drift. Filed.

## Live runtime impact

None until the live VM serves the new endpoint
(`pull-and-deploy` brings it up). Until then the CLI is
operator-runnable on any host with the repo + the audit log.
Zero impact on the live trader process.

## Operator usage

```
# Quick drift check on a specific model:
python -m ml shadow-drift --model-id vwap-shadow-v0

# Custom windows:
python -m ml shadow-drift --model-id vwap-shadow-v0 \
    --reference-days 60 --current-days 14

# Dashboard:
GET /api/bot/shadow/drift?model_id=vwap-shadow-v0&reference_days=30&current_days=7
```
