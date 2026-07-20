---
name: backtesting
description: Run and interpret strategy backtests for the ICT bot — the standalone research harnesses (scripts/backtest_squeeze.py, backtest_fade.py, backtest_trend.py, backtest_ict_scalp.py, src/backtest/run_backtest_vwap.py), the on-demand M5 `/test <strategy>` consumer that writes to trade_journal.db::backtest_results, and the trainer-VM sweep mirror surfaced at /api/bot/backtests/sweeps. Use when the operator says "backtest <strategy>", "run a sweep", "validate this config on history", or asks where backtest code/data/outputs live. NOT for live tuning of config/strategies.yaml params (Tier-3) — this is the evidence-gathering step that precedes that.
---

# /backtesting — run and read ICT strategy backtests

Backtesting is the evidence step before any Tier-3 strategy change. This
skill maps every real backtest entry point in the repo (verified against
the scripts on `main`), the data each needs, and where results land.

**Per-strategy research harnesses are net-of-fee — with one exception.**
Gross-R sweeps mislead — S-STRAT-IMPROVE-S2/S4-A showed vwap was
gross-positive / net-negative once round-trip fees were charged. Most
harnesses below take `--fee-bps-roundtrip`; quote net metrics, not gross.
**Exception (BL-20260610-M15-1):** `scripts/backtest_ict_scalp.py` has
**no** fee model — no `--fee-bps-roundtrip`, so its R/PnL are **gross
only**. Don't read ict_scalp harness output as net; apply a fee haircut
manually, and prefer the account-compat matrix / trainer sweeps (which
stamp `net_of_fee_bps`) for the live-routing gate. Adding a fee model to
the ict_scalp harness is the open follow-up.

## MANDATORY: the per-account compatibility matrix (every strategy)

Before a strategy is proposed for live routing, run it against **every account's
ruleset** — the top-down "which strategy on which account" gate (design:
`docs/integrations/prop-accounts-architecture-DESIGN.md`):

```bash
python scripts/prop/account_compat_matrix.py --strategy <name> --data <feed>
```

For each account (`src.prop.account_rulesets.all_account_units`): **prop**
accounts are scored on the cost-aware EV + survival gate under their prop ruleset
(breach rules + economics); **standard** accounts on net-of-fee performance at
their own risk. Output: `runtime_logs/prop_eval/<date>/compat_<strategy>.{md,json}`
with a **ROUTE / skip verdict per account**. A strategy is never routed to an
account it wasn't evaluated against under that account's rules. (Prop verdicts are
research on the configured feed — revalidate on the account's real venue data
before any Tier-3 live wire.)

**Daily/ETF cells + the Alpaca real-money gate.** For an Alpaca ETF cell (the
daily/intraday SPY/QQQ/IWM/GLD/SLV/USO/TLT/IEF legs, outside the BTC engine
`ROSTER`), score it via the harness-emit path: pass the cell's
`scripts/backtest_{trend,pullback}.py --emit-trades` JSONL to
`account_compat_matrix.py --ledger <jsonl> --symbol <SYM> --base-risk-pct <pct>`.
The orchestrator `scripts/ops/etf_account_compat.sh` runs this for every Alpaca
cell at its exact `config/strategies.yaml` params (trainer-VM-resident
`data/<SYM>_<tf>.csv`). For these `standard` accounts the ROUTE gate is **tighter
than positive end-return** — it also requires `survival ≥ --min-survival` (0.90)
AND `P(breach) ≤ --max-p-breach` (0.10) under the account's own soft limits, and
stamps `symbol`/`asset_class`/`net_of_fee_bps` onto each row. **This daily/ETF
compat run is the MANDATORY gate before promoting any cell onto the real-money
`alpaca_live` account** (Tier-3); the ROUTE verdict must still be revalidated on
Alpaca's own real venue fills + fees before the live wire.

## Where backtest code lives (on `main`)

| Entry point | Strategy | Invocation |
|---|---|---|
| `scripts/backtest_squeeze.py` | squeeze_breakout (BB/KC squeeze) | `python scripts/backtest_squeeze.py --data <csv> [...]` |
| `scripts/backtest_fade.py` | fade_breakout (failed-breakout fade) | `python scripts/backtest_fade.py --data <csv> [...]` |
| `scripts/backtest_trend.py` | trend_donchian (confirmed-breakout follower) | `python scripts/backtest_trend.py --data <csv> [...]` |
| `scripts/backtest_ict_scalp.py` | ict_scalp_5m | `python scripts/backtest_ict_scalp.py --data <csv> [...]` |
| `src/backtest/run_backtest_vwap.py` | vwap (HTF-filter sweep) | `python -m src.backtest.run_backtest_vwap [...]` |
| `src/backtest/run_backtest_m5.py` | on-demand `/test` consumer | `python -m src.backtest.run_backtest_m5 <strategy>` |
| `src/backtest/run_backtest.py` | core `ICTBacktester` harness (`load_data`, `summarize`) | imported by the M5 runner; not a CLI you call directly |

> **Research-only harnesses live on the program branch, not `main`.**
> `scripts/research_decider.py` and `scripts/ops/fetch_dukascopy_index.py`
> are referenced by the strategy-improvement program but ship on the
> persistent branch `claude/strategy-improvement-program-EZi1X` (see
> CLAUDE-RULES-CANONICAL.md § "Strategy-improvement program — branching
> convention"). Don't document them as if they're on `main` — if you
> need them, continue on the program branch.

## Backtest data

All standalone harnesses read a candle CSV resolved as:

```
--data <path>   →   $BACKTEST_DATA_PATH   →   data/backtest_candles.csv
```

Never a bare filename relative to CWD — pass `--data` explicitly or set
`BACKTEST_DATA_PATH`. The trainer VM keeps a longer-history parquet cache
(qashdev/btc) provisioned by `scripts/ops/trainer_bootstrap.sh`; sweeps
that need multi-year history run there, not in the sandbox.

## Running a research backtest (squeeze / fade / trend / ict_scalp)

Common flags: `--data`, `--timeframe`, `--symbol` (default BTCUSDT),
`--resample`, `--start`/`--end` (ISO walk-forward window),
`--fee-bps-roundtrip` (default ~7.5 bps), `--json <out>` (write the
summary), `--emit-trades <jsonl>` (per-trade rows).

**Confidence-threshold sweeps** (squeeze / fade / trend / ict_scalp): each
emits a live-parity per-trade `confidence` (the same formula the strategy's
`order_package()` uses) and accepts `--min-confidence <f>` (skip entries
below the floor) and `--confidence-sweep '<lo>:<hi>:<step>'` (tabulate net
metrics per threshold to read off the PnL-optimal floor). This is the
evidence path for a `config/strategies.yaml::<strategy>.min_confidence`
proposal (Tier-3). Match the live params exactly (timeframe, trail, and any
regime gate like fade's `--adx-max` / squeeze's `--kc-mult`) or the optimum
shifts. Multi-year 5m sweeps (ict_scalp) are slow — run detached on the
trainer and collect from a file.

```bash
# Squeeze breakout (Bollinger/Keltner squeeze → breakout)
python scripts/backtest_squeeze.py --data data/backtest_candles.csv \
  --timeframe 4h --bb-period 20 --bb-std 2.0 --kc-mult 1.5 \
  --atr-stop-mult 2.5 --trail-mult 3.5 --timeout-bars 48 \
  --fee-bps-roundtrip 7.5 --json /tmp/squeeze.json

# Failed-breakout fade (Donchian pierce → reversion)
python scripts/backtest_fade.py --data data/backtest_candles.csv \
  --timeframe 4h --donchian 20 --atr-stop-buffer 0.5 \
  --exit-style far --adx-max 25 --json /tmp/fade.json

# ICT scalp 5m (sweep + displacement + FVG, HTF-gated)
python scripts/backtest_ict_scalp.py --data data/backtest_candles.csv \
  --timeframe 5m --htf-rule 1h --htf-ema-period 20 \
  --warmup-bars 50 --timeout-bars 24 --json /tmp/ict_scalp.json
```

**ict_scalp exit-model caveat (Phase-0, 2026-07-20):** the harness default
(static SL/TP + 24-bar timeout) does NOT match live exits — live runs a
break-even trail at +1R (`monitor_breakeven_sl` + `be_offset_bps`) and has
no timeout. Pass `--sim-breakeven` (and a wide `--timeout-bars`, e.g. 288)
for a live-faithful run. `--stamp-regime` + `--vol-spec-json <frozen spec>`
stamp decision-time regime/vol onto `--emit-trades` rows (same pure
functions as the live builder) for per-(trend,vol) cell attribution; emit
rows also carry `mfe_r`/`mae_r`/`bars_held`/exit fields. See
`docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md`.

`backtest_ict_scalp.py` reads `config/strategies.yaml` by default; pass
`--ignore-yaml` to backtest pure CLI params instead of the live config.

The vwap harness (`python -m src.backtest.run_backtest_vwap --help`) has
its own rich flag surface (HTF filter, band-pct, regime split, net-of-fee
aggregates via `--fee-bps-roundtrip`, `--label`). It is the sweep workhorse
behind the `vwap-backtest-sweep` system-action.

## On-demand `/test <strategy>` (the M5 consumer → trade_journal.db)

The operator's `/test <strategy>` Telegram command (or a comms request)
runs inside the trader's poll loop, NOT in this session:

1. `cmd_test_strategy` validates the name against `config/strategies.yaml`.
2. `CommsPoller.poll_once` runs `BacktestConsumer.scan_and_run` (gated by
   `M5_CONSUMER_ENABLED`).
3. It spawns `python -m src.backtest.run_backtest_m5 <strategy>` under an
   `M5_BACKTEST_TIMEOUT_S` wall clock (default 120s).
4. The subprocess writes **one row** to `trade_journal.db::backtest_results`
   and prints `{"db_row_id": N, "summary": {...}}` as its last stdout line.
5. The consumer appends one NDJSON row to `runtime_logs/validation.jsonl`
   and answers the comms request.

Read those results from the sandbox via `GET /api/bot/backtests?limit=N&strategy=X`
(diag-reachable, Tier-1). Runbook: `docs/runbooks/strategy-testing.md`.

## Operator sweeps (trainer VM → mirror → /api/bot/backtests/sweeps)

The operator's real multi-config sweeps run on the trainer VM (kicked via
the `vwap-backtest-sweep` system-action or a `trainer-vm-diag` relay) and
publish `SUMMARY.md` + `all_metrics.json` into
`runtime_logs/trainer_mirror/backtests/<UTC-date>/` via
`scripts/ops/publish_trainer_mirror.sh`. Surfaced at
`GET /api/bot/backtests/sweeps?limit=N`. **This is the route that holds
the operator's real sweeps** — `backtest_results` (the table above) only
ever holds on-demand `/test` runs.

## Output locations — at a glance

| Output | Where |
|---|---|
| Standalone harness summary | `--json` path you pass (e.g. `/tmp/*.json`) |
| Standalone per-trade rows | `--emit-trades` JSONL path you pass |
| `/test` headline metrics | `trade_journal.db::backtest_results` (one row/run) |
| `/test` audit trail | `runtime_logs/validation.jsonl` (one NDJSON row/run) |
| Operator sweep summaries | `runtime_logs/trainer_mirror/backtests/<date>/` |

## Tuning a parameter from a review-gate `tune` packet (M8)

When the M7 strategy review gate emits `proposed_action: "tune"`, its
`tune_recipe` block is executed by the **canonical M8 sweep harness**,
`scripts/ml/strategy_tune_sweep.py` — don't hand-roll a sweep over the
harnesses above. It ingests the recipe (a review packet or a bare recipe
JSON), expands the `search_space`, dispatches to the right backtester here
(research-harness `min_confidence` per-value; vwap `threshold` via
`--threshold-sweep`), normalizes net-of-fee metrics, and writes a
`strategy_tune_result/v1` packet to `runtime_logs/strategy_tunes/<date>/`
with an **advisory** Tier-3 value proposal. It never writes
`config/strategies.yaml`. Full reference: `docs/strategy-tuning.md`.

```bash
python scripts/ml/strategy_tune_sweep.py --recipe <packet.json> --data <csv>
python scripts/ml/strategy_tune_sweep.py --recipe <packet.json> --dry-run   # plan only
```

## What to report

A backtest result that justifies a Tier-3 change must state: net (not
gross) total R, net win rate, trade count (sample size), expectancy,
max drawdown, the fee bps assumed, and the date window / regime mix. A
gross-positive / net-negative or low-N result does **not** clear the
go-live bar — say so plainly. Promoting a strategy on the strength of a
backtest is still Tier-3 (operator-approved); this skill produces the
evidence, the `new-strategy` skill wires it, the operator approves it.
