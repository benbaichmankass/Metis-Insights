# Prem-tier run kit

Detached, **Tier-1, read-only** evidence runs that were gated on trainer-VM
compute. They are prepped so they can fire the moment dedicated ("prem-tier")
cores are available, without competing with the operator's in-flight sweep.

Nothing here mutates live config, the order path, or any DB. Each script writes
to a timestamped dir under `runtime_logs/prem_runs/` and is idempotent. All
heavy work is wrapped in `nice -n 19 ionice -c3` (override with `NICE=0`) so it
yields to the foreground sweep on a shared box.

## Prerequisites

Point these env vars at the data on the VM before running:

| Var        | Needed by      | What                                                  |
|------------|----------------|-------------------------------------------------------|
| `DATA_5M`  | 01, 02         | Full-history 5m BTCUSDT CSV/parquet (the trainer file)|
| `DATA_2H`  | 01 (optional)  | Pre-resampled 2h BTC CSV; derived from `DATA_5M` if absent |
| `DATA_SPX` | 03             | SPX/MES 2h OHLCV; 03 no-ops cleanly if unset          |

Optional: `REPO_ROOT`, `VENV_DIR`, `OUT_ROOT`, `NICE=0` (disable throttle).

## Scripts (run in order)

1. **`01_reproduce_check.sh`** — confirms the consolidated `sim` Phase-5 account
   layer reproduces the retiring `scripts/backtest_system.py` on full 5m
   history (single-strategy `trend_donchian`, apples-to-apples). Writes
   `reproduce_verdict.json`; exit 0 = within the 5% band → `backtest_system.py`
   can be retired with evidence. Exit 2 = divergence to investigate.

2. **`02_demotion_evidence.sh`** — runs the full live roster, feeds the
   in-system attribution to `scripts/strategy_gate.py`, and emits
   `strategy_scorecard.json` with promote/demote recommendations. This is the
   attachment for the **Tier-3 demotion PR** (operator approves; the script
   never flips a gate). Expected: fade/turtle/ict_scalp → demote,
   trend_donchian → keep.

3. **`03_spx_retune.sh`** — sweeps `trend_donchian` params on SPX/MES to test
   the proven winner on an uncorrelated market. Ranks variants by return/DD.
   No-ops cleanly until `DATA_SPX` exists (SPX data acquisition is upstream).

## Example

```bash
export DATA_5M=/data/btc_5m.parquet
scripts/ops/prem_runs/01_reproduce_check.sh      # sign off the consolidation
scripts/ops/prem_runs/02_demotion_evidence.sh    # produce the demotion scorecard
DATA_SPX=/data/spx_2h.csv \
  scripts/ops/prem_runs/03_spx_retune.sh         # SPX diversification evidence
```

## After the runs

- **01 passes** → open the small PR deleting `scripts/backtest_system.py` and
  pointing its callers at `sim` (the consolidation's final step).
- **02 scorecard** → attach to a `config/strategies.yaml` demotion PR for
  operator review (Tier-3).
- **03 winner** → if return/DD clears the bar, propose a Tier-3 PR routing a
  second `trend_donchian` instance to an SPX/MES account.

Each run pings the operator via `notify_run.sh` when present (no-op off-VM).
