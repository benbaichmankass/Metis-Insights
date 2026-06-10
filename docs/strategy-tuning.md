# Strategy Tuning (M8) — the canonical parameter-sweep harness

> **M8 makes the M7 gate's `tune` action executable.** The strategy review
> gate (`docs/strategy-review-gate.md`) emits a `tune_recipe` block on every
> `proposed_action == "tune"` packet. Until M8, that recipe was advisory text.
> M8 ships **one canonical harness** that ingests the recipe, runs the named
> net-of-fee parameter sweep, and reports the optimal value as an **advisory
> Tier-3 proposal** — it never writes `config/strategies.yaml`.

- Entry point: [`scripts/ml/strategy_tune_sweep.py`](../scripts/ml/strategy_tune_sweep.py)
- Tests: [`tests/test_strategy_tune_sweep.py`](../tests/test_strategy_tune_sweep.py)
- Sits on top of: the per-strategy research harnesses + the vwap workhorse
  documented in the [`backtesting`](../.claude/skills/backtesting/SKILL.md) skill.
- Kickoff sprint: [`docs/sprint-logs/S-M8-STRATEGY-TUNING-S0.md`](sprint-logs/S-M8-STRATEGY-TUNING-S0.md).

## Why one harness

The per-strategy backtesters (`scripts/backtest_{fade,squeeze,trend,ict_scalp}.py`,
`src/backtest/run_backtest_vwap.py`) are **heterogeneous** in both invocation
(per-value CLI flag vs. a built-in sweep mode that walks a module constant) and
output (R-denominated `net_total_r`/`net_expectancy_r` vs. the core
`total_pnl`/`expectancy`). A `tune_recipe` should not have to know which is
which. M8's harness is the single adapter: recipe in → normalized, net-of-fee
grid + a pick out.

## The recipe → sweep contract

The harness consumes the M7 `tune_recipe` schema verbatim:

```json
"tune_recipe": {
  "target": "config/strategies.yaml::vwap.threshold",
  "current_value": 1.0,
  "search_space": "log-uniform [0.5, 2.0]",
  "harness": "scripts/backtest_vwap.py",
  "evidence_window_days": 90,
  "note": "ties to the long-side overtrade pattern from S-STRAT-IMPROVE-S2."
}
```

| Field | Role in the sweep |
|---|---|
| `target` | `file::strategy.param`. Names the strategy + parameter and the exact YAML line the result advises against. |
| `current_value` | Folded into the grid so the live baseline is measured on identical footing; captured as `baseline_row`. |
| `search_space` | Expanded into a concrete grid — see grammar below. |
| `harness` | Selects the backtester via the dispatch registry. Loose names are aliased (`scripts/backtest_vwap.py` → the vwap module). |
| `evidence_window_days` | Passed through as the walk-forward window (recipe override of the harness default). |
| `fixed_args` | Extra backtester flags forwarded verbatim to **every** run so the sweep pins the strategy's **live** params (timeframe, donchian, trail, …). A token list (`["--timeframe","1h","--donchian","20"]`) or a shell-style string. Without it the harness runs at its CLI defaults and the optimum shifts off the live config — the backtesting skill's "match the live params exactly or the optimum shifts" rule. The gate should author this from `config/strategies.yaml`; `--fixed-args '<...>'` augments it at the CLI. |
| `note` | Carried into the result + Markdown for context; not interpreted. |

> **A sweep is only valid evidence at the live params.** Always set `fixed_args`
> (or `--fixed-args`) to the strategy's current `config/strategies.yaml` values
> for everything *except* the swept parameter. A harness flag the strategy uses
> but the backtester doesn't expose (e.g. trend's long-only gate) is a coverage
> gap — note it in the result and, if it materially moves the optimum, add the
> flag to the harness rather than running an unpinned sweep.

## Search-space grammar

`parse_search_space` accepts (case-insensitive; `current_value` always folded in
and the grid de-duplicated):

| Spec | Expansion |
|---|---|
| `log-uniform [lo, hi]` | `--samples` points, geometric-spaced (`lo>0` required). |
| `uniform [lo, hi]` | `--samples` points, linear-spaced. |
| `grid [a, b, c]` / `[a, b, c]` | exactly those values. |
| `lo:hi:step` | inclusive range (the existing confidence-sweep grammar). |

## Net-of-fee discipline

Every run charges `--fee-bps-roundtrip` (default `7.5`, in sync with the
research harnesses). **Gross-R sweeps mislead** — S-STRAT-IMPROVE-S2/S4-A
showed vwap gross-positive / net-negative once round-trip fees were charged.
The result reports `net_total` and `net_expectancy` only; the
`best_by_net_expectancy_minN` pick additionally requires ≥20 trades so a thin,
high-variance cell can't win.

## Dispatch styles + the registry

`_REGISTRY` maps `(harness basename, param)` → a `HarnessSpec` with one of two
dispatch styles:

- **per-value** — the param is a CLI flag (e.g. `--min-confidence`); the harness
  is invoked once per grid value. Used for the research harnesses' confidence
  floor across `fade` / `squeeze` / `trend` / `ict_scalp`.
- **native sweep** — the harness has a built-in sweep mode that reaches a
  parameter a CLI flag can't (e.g. vwap's `--threshold-sweep` walks the
  `ENTRY_STD_THRESHOLD` module constant). Invoked once; its own grid rows are
  re-keyed onto `value` via `native_rows_key` / `native_value_key`.

### Extending the registry

S0 seeds the registry with the verifiable cases (research-harness
`min_confidence`; vwap `threshold`). To cover a new `(harness, param)`, add a
row to `_REGISTRY` in `scripts/ml/strategy_tune_sweep.py` — set `flag` for a
per-value param, or `native_sweep_flag` + `native_rows_key` +
`native_value_key` for a built-in sweep. An unmapped pair raises with a pointer
back to this section, so the gate never silently emits an unrunnable recipe.

## Walk-forward / OOS validation (the go-live gate)

A full-history sweep finds the **in-sample** optimum — which overfits. A tuning
value only clears the go-live bar if it holds **out-of-sample**, the same
discipline the live `trend_donchian` floor was set under. Pass `--oos-start
DATE` (or `oos_start` in the recipe) to split chronologically:

```
train = [train-start, oos-start)      OOS = [oos-start, oos-end]
```

Each grid value is then run on **both** windows (via the harness's
`--start`/`--end`). The result flips to **OOS-gated**: top-level grid metrics
and all picks are OOS; the in-sample numbers are nested under each row's `train`
key (and shown as extra columns). The recommendation carries
`metric_basis: "oos"` and a `train_oos_consistent` flag (the OOS-optimal value
must also be net-positive in-sample, else the OOS lead is likely noise).

Without `--oos-start` the result is honest about its weakness: `metric_basis:
"full_sample"`, the grid header reads **IN-SAMPLE**, and the recommendation
detail says it has not cleared the go-live bar. **A merge-ready Tier-3 packet
requires the OOS split.**

### k-fold anchored walk-forward (robustness across regimes)

A single split can still be a lucky regime. `--wf-folds N --wf-start DATE
--wf-end DATE` runs **anchored (expanding-window) k-fold**: the first
`--wf-train-frac` (default 0.4) of the span is the initial train window; the
rest is divided into N contiguous OOS segments, and fold *k* trains on
everything before its segment. Each value's OOS metrics are **aggregated across
folds** (net Σ, expectancy μ, drawdown = worst fold), and the recommendation
adds a `robust` flag — true only when the pick is net-positive in **every**
fold. This is the discipline the live `trend_donchian` floor was set under
(3-fold). Per-fold detail is kept under each grid row's `folds`.

```bash
python scripts/ml/strategy_tune_sweep.py \
  --target 'config/strategies.yaml::trend_donchian.min_confidence' --current-value 0.30 \
  --search-space 'uniform [0.3, 0.9]' --harness scripts/backtest_trend.py \
  --fixed-args '--timeframe 1h --donchian 20 --trail-mult 5.0 --long-only' \
  --data data/btc_1h_multiyear.csv --oos-start 2025-01-01
```

## Output

```
runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.json
runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.md
```

The JSON (`schema: strategy_tune_result/v1`) carries the recipe echo, the full
net-of-fee `grid`, `best_by_net_total`, `best_by_net_expectancy_minN`,
`baseline_row`, and the `recommendation`. The Markdown is the PR-body twin.

## The Tier boundary

The harness is **Tier-1 tooling** — it reads config, drives backtesters, and
writes only to `runtime_logs/` + this doc tree. The `recommendation` block is
**advisory**: it names the optimal value and renders the exact
`config/strategies.yaml` line, but **applying that line is Tier-3** — the
operator approves it and it ships via a normal PR (the same boundary the M7
gate draws for `kill`). This harness never writes `config/strategies.yaml`.

```
strategy-review-gate packet (action: tune)  ← M7
        │  tune_recipe
        ▼
scripts/ml/strategy_tune_sweep.py            ← M8 (THIS) — runs the sweep, advises
        │  recommendation (Tier-3, advisory)
        ▼
operator-approved config/strategies.yaml PR  ← Tier-3, never this script
```

## CLI

```bash
# From a review packet (the gate writes one per 'tune' strategy):
python scripts/ml/strategy_tune_sweep.py \
  --recipe runtime_logs/strategy_reviews/<date>/vwap.json --data data/backtest_candles.csv

# Constructed inline:
python scripts/ml/strategy_tune_sweep.py \
  --target 'config/strategies.yaml::fade_breakout.min_confidence' \
  --current-value 0.0 --search-space 'uniform [0.0, 0.6]' \
  --harness scripts/backtest_fade.py --data data/backtest_candles.csv

# Plan only (prints grid + per-value invocations; no run — useful with no candle data):
python scripts/ml/strategy_tune_sweep.py --recipe <packet> --dry-run
```

Multi-year sweeps run on the trainer VM (longer-history candle cache), not in a
sandbox — see the `backtesting` skill § operator sweeps.

## What this doc is not

- **Not the gate.** M7 decides *whether* to tune; M8 runs the sweep that a
  `tune` verdict points at.
- **Not a kill switch / auto-tuner.** It proposes a value; the operator applies
  it (Tier-3).
- **Not a code-quality review.** That's `/review` / `/security-review`.

## Change log

- **2026-06-09 (S2)** — added walk-forward / OOS validation (`--oos-start`):
  each value run on train + OOS windows, picks gated on OOS, `train_oos_consistent`
  flag, and an explicit IN-SAMPLE warning when no split is given. The go-live gate.
- **2026-06-09** — created in sprint
  [`S-M8-STRATEGY-TUNING-S0`](sprint-logs/S-M8-STRATEGY-TUNING-S0.md); ships the
  canonical sweep harness, the search-space grammar, the dispatch registry
  (research `min_confidence` + vwap `threshold` seeded), and the
  `strategy_tune_result/v1` output. Makes the M7 `tune_recipe` executable.
