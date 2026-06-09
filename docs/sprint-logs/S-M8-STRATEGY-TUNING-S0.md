# Sprint Log: S-M8-STRATEGY-TUNING-S0

## Date Range
- Start: 2026-06-09
- End:   2026-06-09

## Objective
- Primary goal: open the M8 milestone — ship the **canonical parameter-sweep
  harness** that makes the M7 gate's `tune` action executable. The gate emits a
  `tune_recipe` block; M8 ingests it, runs the named net-of-fee sweep against the
  existing backtesters, and reports the optimal value as an **advisory Tier-3
  proposal**. The harness never writes `config/strategies.yaml`.
- Secondary goals:
  - Establish the recipe→sweep contract + search-space grammar as the canonical
    M8 doc (`docs/strategy-tuning.md`).
  - Back-link the M7 gate doc's "until M8 lands, tune_recipe is advisory" line to
    the now-shipped harness.

## Tier
- Tier 1. The harness is research tooling — it reads config, drives the existing
  backtesters as subprocesses, and writes only to `runtime_logs/strategy_tunes/`
  + the docs tree. It never writes `config/strategies.yaml`; the
  `recommendation` block is advisory and names the Tier-3 line for the operator.
- Justification: same boundary M7 drew — the gate/harness generates evidence; the
  YAML change is the operator-gated Tier-3 step downstream.

## Starting Context
- Active roadmap items: M8 NOT STARTED (seam reserved by M7); M7 IN PROGRESS
  (kickoff sprint `S-M7-STRATEGY-REVIEW-GATE-2026-06-09` shipped the gate, the
  packet generator, the read route, and the `tune_recipe` schema).
- Prior sprint reference: `S-M7-STRATEGY-REVIEW-GATE-2026-06-09` § "Next
  Recommended Sprint" names this sprint and its rationale — the gate without M8
  can only `kill` / `demote_shadow` / `hold`; `tune` is the most common mid-`n`
  output and stalls without a recipe runner.
- Known risks at start: (a) the existing backtesters are heterogeneous in
  invocation + output shape, so the harness must normalize rather than assume one
  schema; (b) net-of-fee discipline is mandatory (S-STRAT-IMPROVE-S2/S4-A:
  gross-positive / net-negative vwap); (c) the sandbox has no candle data, so the
  suite must run without a real backtester.

## Repo State Checked
- Branch: `claude/m8-milestone-setup-ggr7d6`.
- Canonical docs reviewed:
  - `docs/CLAUDE-RULES-CANONICAL.md` (Generation Discipline § skill-first lookup
    — invoked the `backtesting` skill before generating; Permission Tiers).
  - `ROADMAP.md` — M8 row confirmed NOT STARTED before edit.
  - `docs/strategy-review-gate.md` § "M8 hook" — the `tune_recipe` schema this
    harness consumes.
  - `.claude/skills/backtesting/SKILL.md` — the harness inventory + net-of-fee
    rule the dispatch registry sits on top of.

## Files and Systems Inspected
- Code files inspected:
  - `src/backtest/run_backtest_vwap.py` (argparse surface; `--threshold-sweep`
    walks the `ENTRY_STD_THRESHOLD` module constant; prints its result dict to
    stdout and defines **no** `--json` flag — drove the native-sweep dispatch).
  - `scripts/backtest_fade.py` (the `--confidence-sweep` / `--min-confidence`
    grammar + `_confidence_sweep` return shape with `net_total_r` /
    `net_expectancy_r` / `max_drawdown_r` — drove the metric normalizer and the
    min-20-trades expectancy floor).
  - `scripts/backtest_{squeeze,trend,ict_scalp}.py` (confirmed all route
    `--json -` to stdout — the per-value dispatch relies on this).
  - `src/backtest/run_backtest.py` / `backtester.py` (the core
    `total_pnl`/`expectancy`/`win_rate` keys — folded into the normalizer's
    candidate-key priority lists so both R- and PnL-denominated output read).
- Config files inspected: none mutated.
- Deployment files inspected: none.

## Work Completed
- **`scripts/ml/strategy_tune_sweep.py`** — the canonical M8 sweep harness:
  - `TuneRecipe` + `load_recipe` ingest the M7 schema from a review packet
    (`{"tune_recipe": {...}}`) or a bare recipe object; `parse_target` splits
    `file::strategy.param`.
  - `parse_search_space` — the grammar: `log-uniform [lo,hi]`, `uniform [lo,hi]`,
    `grid [a,b,c]` / `[a,b,c]`, and `lo:hi:step`. Always folds `current_value`
    into the grid and de-dups so the baseline is measured on identical footing.
  - `_REGISTRY` — `(harness, param)` dispatch with two styles: **per-value** (CLI
    flag, e.g. research-harness `--min-confidence`) and **native sweep** (the
    harness's own built-in mode, e.g. vwap `--threshold-sweep` re-keyed onto
    `value`). Seeded with the verifiable cases; unmapped pairs raise with a
    pointer to the doc's extension section.
  - `normalize_row` folds heterogeneous harness output onto one canonical row via
    candidate-key priority lists; missing metrics are `None` (honest), not `0`.
  - `run_sweep` expands the grid, drives the harness through an injectable
    `runner` (the seam the tests exploit), picks `best_by_net_total` +
    `best_by_net_expectancy_minN` (≥20 trades), captures the `baseline_row`, and
    builds the advisory `recommendation` (prefers the expectancy optimum; names
    the exact YAML line; `tier: 3`).
  - Emits `strategy_tune_result/v1` JSON + a Markdown twin to
    `runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.{json,md}`.
  - CLI: `--recipe` or inline `--target/--current-value/--search-space/--harness`;
    `--data`, `--fee-bps-roundtrip` (default 7.5), `--samples`, `--out-dir`,
    `--dry-run`.
- **`docs/strategy-tuning.md`** — the canonical M8 doc: recipe→sweep contract,
  search-space grammar, net-of-fee discipline, the two dispatch styles + the
  registry extension seam, output schema, and the Tier-1-tooling /
  Tier-3-application boundary diagram.
- **`tests/test_strategy_tune_sweep.py`** — 32 tests: target/recipe parsing (incl.
  packet vs bare object + rejection of a non-tune packet), all four search-space
  grammars + `current_value` folding + the malformed-input rejections, registry
  dispatch (per-value, vwap alias→native, unknown-pair pointer), normalization
  across R- and PnL-denominated output, end-to-end `run_sweep` against a fake
  runner for both dispatch styles (best-pick + advisory recommendation +
  insufficient-evidence), and the JSON/MD emission + stdout-with-leading-table
  JSON extraction.
- **`docs/strategy-review-gate.md`** — the "until M8 lands, advisory" note now
  links to the shipped harness + doc.
- **`ROADMAP.md`** — M8 flipped NOT STARTED → IN PROGRESS, pointing at this log.

## Validation Performed
- Tests run:
  - `python -m pytest tests/test_strategy_tune_sweep.py -q` — **32 passed**
    (Python 3.11).
- Dry-runs / staging checks:
  - `--dry-run` for the vwap native-sweep recipe → renders the log-spaced 9-point
    grid and the single `... --threshold-sweep` invocation with **no** `--json`
    flag (vwap doesn't define one).
  - `--dry-run` for a `fade_breakout.min_confidence` uniform recipe → renders the
    4-point grid and one `--min-confidence <v> --json -` invocation per value.
- Manual code verification:
  - Confirmed each research harness routes `--json -` to stdout (so the per-value
    runner's stdout-JSON parse is valid) and that the vwap harness has no `--json`
    flag (so the native-sweep path correctly omits it).

## Documentation Updated
- Rules doc updates: none — M8 sits within existing skill-first + Tier-1 protocols.
- Architecture doc updates: none — no runtime / order-path / mode-mutation change.
- Roadmap updates: M8 → IN PROGRESS.
- Subsystem doc updates: **NEW** `docs/strategy-tuning.md`; back-link added to
  `docs/strategy-review-gate.md`.

## Contradictions or Drift Found
- None. The gate doc's advisory note was the only stale-by-design line (it said
  the recipe was advisory "until M8 lands") — updated now that M8 has landed.

## Risks and Follow-Ups
- Remaining technical risks:
  - **Registry coverage is intentionally narrow.** S0 seeds only the two
    verifiable `(harness, param)` pairs. A gate packet that names an unmapped pair
    raises (with a doc pointer) rather than running a wrong sweep — safe, but it
    means new params need a one-line registry add. Follow-up: extend as packets
    demand.
  - **Not yet run against real candle data.** The sandbox has none; the suite
    proves the orchestration via a fake runner. First real run rides the trainer
    VM's candle cache (per the `backtesting` skill) — a natural next step.
- Remaining product decisions (Tier 3): none this sprint — applying any tune
  result is the operator-gated downstream step, by design.
- Blockers: none.

## Deferred Items
- Extend the `(harness, param)` registry to more parameters (vwap SL mult via
  `--param-sweep`, research-harness structural params) as gate `tune` packets
  surface them.
- First on-trainer run against multi-year candle history to produce a real tune
  result.
- Dashboard surfacing of `runtime_logs/strategy_tunes/` (a read route + Streamlit
  panel) — pairs with the M7 dashboard-wiring follow-up.
- A dedicated `/tune` skill (vs. the pointer now living in the `backtesting`
  skill) if the harness grows enough surface to warrant one.

## Next Recommended Sprint
- Suggested next sprint: **S-M8-STRATEGY-TUNING-S1** — run the harness on the
  trainer VM against the real candle cache for the first live `tune` packet
  (whatever the next `/performance-review` rotation flags), and extend the
  registry to cover that packet's parameter if it isn't already seeded.
- Why next: S0 proved the machinery; S1 produces the first real evidence packet a
  Tier-3 tuning proposal can cite.
- Required verification before starting: a gate `tune` packet to consume (from
  the deployed `strategy_review_packet.py`), and trainer-VM candle data confirmed
  present.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage — n/a, no pipeline change.
- [x] Roadmap status was checked and updated.
- [x] Contradictions were recorded (gate-doc advisory note updated).
- [x] Remaining unknowns were stated clearly (registry coverage; no real-data run
      yet).
