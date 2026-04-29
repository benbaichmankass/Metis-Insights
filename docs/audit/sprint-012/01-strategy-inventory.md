# § 1 — Strategy inventory

Every Python file that defines a strategy class, signal builder, or
order-package adapter, with import path, config reference, test coverage,
and last-touching commit.

## 1.1 Legacy `strategies/` (repo root)

| File | LOC | Exports | Imported by | Config ref | Tests | Last commit |
|---|---|---|---|---|---|---|
| `strategies/turtle_soup_mtf_v1.py` | 364 | `TurtleSoupMTFv1` (class, extends `BaseStrategy`) | `tests/test_turtle_soup_mtf.py:14` only | **none** (not in `strategies.yaml`, `units.yaml`, or `accounts.yaml`) | `tests/test_turtle_soup_mtf.py` | `60adc4d feat: full TurtleSoupMTFv1 strategy implementation` |
| `strategies/vwap_signal_builder.py` | 149 | `compute_vwap()`, `build_vwap_signal()`, `ENTRY_STD_THRESHOLD` | `src/units/strategies/vwap.py:5` (canonical), `src/runtime/pipeline.py:119` (legacy direct import) | `config/strategies.yaml:23` (vwap → enabled: true) | `tests/test_vwap_strategy.py` | (S-008-era) |
| `strategies/breakout_confirmation.py` | 144 | `_load_model()`, `_local_model_path()` (model-loader stubs only) | `src/strategies_manager.py:11` (legacy registry); `src/units/strategies/breakout_confirmation.py:82-94` (model loader) | `config/strategies.yaml:13` (enabled: false), `config/units.yaml:24` (enabled: false) | `tests/test_strategies_manager.py` | `4392980 Add breakout confirmation strategy integration` |

**Verdict:** Turtle Soup is **orphaned from production wiring** — it is the
only Turtle Soup implementation in the repo (no copy under
`src/units/strategies/`), but no config or runtime path consumes it. C1 in
§ 9 ports it to `src/units/strategies/turtle_soup.py`.

The two other files in this directory are scheduled for deletion in § 9
(PR C5) — `breakout_confirmation.py` because PM intent excludes it; the
`vwap_signal_builder.py` decision is a keep-or-fold (see § 8 PM-decision
note: VWAP's pure helpers may stay in `strategies/` or move into
`src/units/strategies/vwap.py`).

## 1.2 Modern `src/units/strategies/`

All files here implement the S-008 contract: `order_package(cfg, candles_df)
-> dict` returning `{symbol, direction, entry, sl, tp, confidence, meta}`,
per the docstring at `src/units/strategies/_base.py:4-17`.

| File | LOC | Exports | Inherits/uses | Config ref | Tests |
|---|---|---|---|---|---|
| `__init__.py` | 77 | `load_strategy_config()`, `save_strategy_config()` | YAML loader/saver for `config/strategies.yaml` | n/a (loader) | `tests/test_unit_config.py`, `tests/test_s011_strategy_purity.py` |
| `_base.py` | 77 | `side_to_direction`, `last_close`, `derive_sl_tp`, `require_candles` | shared helpers | n/a | `tests/test_s008_strategies.py` |
| `breakout_confirmation.py` | 124 | `order_package()` | imports `src.strategies_manager.StrategyManager:82`; `_base:31-36` | `config/units.yaml:24-28` (enabled: false), `config/strategies.yaml:13` (enabled: false) | `tests/test_s008_strategies.py:159-200` |
| `vwap.py` | 102 | `order_package()` | imports `strategies.vwap_signal_builder:55`; `_base:23-28` | `config/strategies.yaml:23`, `config/units.yaml:19` (both enabled: true) | `tests/test_vwap_strategy.py`, `tests/test_s008_strategies.py:201-250` |
| `ict.py` | 125 | `order_package()` | imports `src.runtime.strategies.ict.build_ict_signal:96`; `_base:26-31` | `config/strategies.yaml:45`, `config/units.yaml:14` (both enabled: true) | `tests/test_s008_strategies.py:87-158` |
| `killzone.py` | 119 | `order_package()` | `_base:26-31` only (consumes pre-built signal via `cfg["_signal"]`) | `config/strategies.yaml:34`, `config/units.yaml:29` (both enabled: true) | `tests/test_s008_strategies.py:251-320` |

**Verdict:** This is the canonical strategy directory. After S-012:
- **Keep:** `_base.py`, `vwap.py`, **new `turtle_soup.py`** (added in PR C1).
- **Delete:** `breakout_confirmation.py`, `ict.py`, `killzone.py` (per PM
  intent — flagged in § 8 because two of them are `enabled: true` today,
  not pure scaffolding).

## 1.3 Runtime `src/runtime/strategies/`

| File | LOC | Exports | Imported by |
|---|---|---|---|
| `__init__.py` | 16 | (empty package marker) | n/a |
| `ict.py` | 301 | `build_ict_signal()` (pure signal builder) | `src/units/strategies/ict.py:96` only |

**Verdict:** Falls with `src/units/strategies/ict.py`. Both deleted in PR
C5 once the `ict` strategy is removed from config.

## 1.4 Other strategy-shaped code

| File | Status |
|---|---|
| `src/strategies_manager.py` (29 LOC) | Legacy in-memory registry. Only consumer: `src/units/strategies/breakout_confirmation.py:82-94`. Becomes orphan once breakout is deleted. **Delete in PR C5.** |
| `src/core/automated_trading_loop.py` (≈112 LOC) | Orphan entrypoint module. Contains a turtle_soup-shaped pure signal function but is never invoked by `ict-trader-live.service` (live runs `python -m src.main`). Last touched by `4fe893f DEPLOY CANDIDATE: Turtle Soup Iteration #5 — Replaced src/core/automated_trading_loop.py`. **Delete in PR C6** (paired with `run_trader.sh`/`check_bots.sh`). |

## 1.5 Final post-sprint shape

```
strategies/                              # DELETED (PR C5)
src/runtime/strategies/                  # DELETED (PR C5)
src/strategies_manager.py                # DELETED (PR C5)
src/core/automated_trading_loop.py       # DELETED (PR C6)

src/units/strategies/                    # only strategy directory
├── __init__.py                          # YAML loader/saver
├── _base.py                             # shared helpers
├── turtle_soup.py                       # NEW (PR C1, ported from strategies/turtle_soup_mtf_v1.py)
└── vwap.py                              # KEPT, may absorb compute_vwap helpers
```
