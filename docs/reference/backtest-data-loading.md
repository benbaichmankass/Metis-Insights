# Backtest data loading — the one way to get candles

> **Doc status:** `unknown` · category `lookup` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Row **E4** (`docs/claude/work/MANAGER-CHECKLIST.json`). The problem this closes:
the default candle fixture (`data/backtest_candles.csv`) is 5,001 rows spanning
3.5 days of 2022. A harness that reaches it — by default, silently, because a
`--data` flag carried a hardcoded fallback — produces a confident, fully-green
result computed from three and a half days of one crypto pair. That is not a
backtest; it is a demo.

## The source of record

**One mapping, one policy, no second implementation.**

1. **`scripts/research/m20_fleet_exit_sweep.py::resolve_data(symbol, tf, data_dir)`**
   — the single `(symbol, timeframe) -> file` mapping. Convention:
   `data/<SYMBOL>_<grain>.csv`, finest-grain-≤-leg-timeframe, with the
   declared `PROXY_DATA` substitutions (`MGC→GC_F`, `MES→ES_F`, `MHG→HG_F`,
   `XAUUSD→GC_F`) applied by default (`BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA`
   — the proxy is the DEEPER series; do not flip this without reopening that
   decision). This is why it is the source of record and not merely *a*
   resolver: every research sweep in `scripts/research/` already reads
   candles through it, so a second mapping here would be
   `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` and the two would drift apart
   after the next proxy change.
2. **`scripts/ops/backtest_data_source.py::resolve_or_refuse(...)`** — the
   policy wrapper every harness's CLI calls. It does not re-derive the
   mapping above; it decides what to do with it. Four states:

   | state | when | what happens |
   |---|---|---|
   | `explicit` | caller passed `--data FILE` | used as given, never second-guessed |
   | `env` | `BACKTEST_DATA_PATH` is set (a fleet-wide knob, 23+ readers) | used as given |
   | `resolved` | a `--symbol` was named and a matching file exists | the resolver's answer, with a provenance line naming it |
   | `refused` | anything else — including a **bare invocation with no `--symbol` and no `--data`** | **exit non-zero**, naming the cause and the one-line remedy |

   `allow_implicit_default=True` is the one documented opt-in escape hatch
   back to the pre-E4 behaviour (silently defaulting to the fixture on a bare
   invocation); no shipped harness passes it. Every harness call site sets
   `--data` and `--symbol` argparse defaults to `None` — while `--data`
   carries a concrete default, a harness cannot tell "the caller named this
   file" from "argparse supplied it", and every run looks explicit
   (`BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC`).

Wired harnesses (verified via `python3 scripts/ops/symbol_data_binding_census.py`,
which reports **0** unbound `--symbol`/`--data` sites as of this row):
`scripts/backtest_trend.py`, `backtest_chop_scalp.py`, `backtest_fade.py`,
`backtest_funding_carry.py`, `backtest_fvg_range.py`, `backtest_ict_scalp.py`,
`backtest_orb.py`, `backtest_pullback.py`, `backtest_squeeze.py`,
`backtest_system.py`, `scripts/prop/account_compat_matrix.py`,
`scripts/ml/build_calibration_corpus.py`,
`scripts/research/build_backtest_panel.py`,
`scripts/research/build_intrabar_exit_panel.py`,
`scripts/research/nbook_portfolio.py`, `scripts/research/research_momentum.py`,
`scripts/research/m27/ict_scalp_exit_sweep.py`.

**The fixture stays.** `data/backtest_candles.csv` is still the fast smoke
path every self-test relies on — nothing deletes it. What row E4 removed is
reaching it *by default*: `python3 scripts/backtest_trend.py` with no flags
now refuses and names the explicit command (`--data data/backtest_candles.csv`)
instead of silently running against it.

## The committed corpus

Every `(symbol, timeframe)` pair a rostered leg trades
(`config/strategies.yaml` × `config/accounts.yaml`) needs its own real
history for `resolve_data` to find. **`docs/reference/corpus-manifest.json`** is the
committed, stated-coverage record: for each pair, its source, row count,
start/end timestamps, and (when absent) an honest `gap` reason.

**The CSVs themselves are not committed** — `.gitignore` already has a
deliberate, dated rule (`data/*.csv` ignored except the validate-manifest):
*"Regenerable in minutes on the free lane … data belongs in the archive, not
in git."* Committing 47 fetched candle files would repeat the exact
placeholder-data anti-pattern MI-157 already cost this repo once (real
numbers instead of flat ones does not change the failure mode — a
force-added data file goes stale the moment it is committed, and crypto
history in particular needs refreshing far more often than a git history
should carry). So the manifest is the committed, durable-storage-pointing
half of row E4's requirement; the CSVs are fetched on demand.

**To populate (or refresh) the corpus:**

```bash
python3 scripts/ops/fetch_backtest_corpus.py                          # every rostered pair
python3 scripts/ops/fetch_backtest_corpus.py --symbol BTCUSDT --timeframe 1h
python3 scripts/ops/fetch_backtest_corpus.py --roster-only            # verify the pair
                                                                        # table has not
                                                                        # drifted from
                                                                        # config/*.yaml
```

This is an orchestration layer over the **one existing fetcher**,
`scripts/ops/fetch_backtest_candles.py` (Bybit → Binance-vision fallback for
crypto; yfinance/Dukascopy for equities, ETFs, and the declared futures
proxies) — it does not re-derive fetch logic, only which pairs to fetch and
where to write them, then rebuilds the manifest from what actually landed on
disk (never from a fetch log's claims — RULE ONE, *read the field*).

### Coverage as of 2026-09-25

Built from `docs/reference/corpus-manifest.json`, itself rebuilt from the fetched
files on disk. 46 of 47 rostered pairs covered; one honest gap.

| symbols | timeframes | rows (typical) | span | source |
|---|---|---|---|---|
| BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT | 1h/2h/4h | ~1,100–4,400 | 6 months | Binance-vision (`futures/um` klines archive — Bybit is geoblocked from both this host and GH-hosted runners; same substrate `fetch_backtest_candles.py` already falls back to) |
| BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT | 15m | ~8,800 | 3 months | Binance-vision |
| BTCUSDT, SOLUSDT, XRPUSDT, AVAXUSDT | 5m | ~8,900 | 1 month | Binance-vision |
| GDX, GLD, IAUM, IEF, IWM, QLD, QQQ, SCHA, SLV, SPY, TLT, TQQQ | 1d | ~2,500 | ~10 years | Yahoo Finance chart API |
| **SPLG** | **1d** | **0** | — | **gap** — Yahoo's chart API returns `possibly delisted; no timezone found` for this ticker specifically (SPY/QQQ/etc. on the same call pattern work); needs a different source (IBKR/Alpaca native pull). Filed to the pipeline. |
| GLD, QQQ, SLV, SPY, TLT, USO | 1h | ~4,300 | ~2 years | Yahoo Finance chart API |
| MGC (proxy `GC_F`) | 15m/1h/1d | 1,300–2,500 | 60d–10y | Yahoo Finance chart API, `GC=F` continuous gold future (declared `PROXY_DATA` substitution) |
| MES (proxy `ES_F`) | 1d | ~2,500 | ~10 years | Yahoo Finance chart API, `ES=F` |
| MHG (proxy `HG_F`) | 1d | ~2,500 | ~10 years | Yahoo Finance chart API, `HG=F` |

Exact per-pair rows/start/end: `docs/reference/corpus-manifest.json` (rebuild with
`fetch_backtest_corpus.py`, not by hand — RULE ONE again).

## The guards

- **`scripts/ci/check_candle_fixture_variance.py`** — no committed candle
  file may be flat (MI-157). Price-variance axis.
- **`scripts/ci/check_corpus_row_floor.py`** — no `CORPUS-MANIFEST.json`
  entry may declare fewer than 500 rows (row E4). Row-count axis of the same
  defect. Cross-checks a committed manifest claim against a file that
  happens to be present on disk (field beats comment) and treats a
  disclosed `gap` as honest, not a lie.
- **`scripts/ops/symbol_data_binding_census.py`** — census of harnesses
  whose `--symbol` can bind their `--data`. `0` sites as of this row.

All three carry `--self-test` with planted positive AND negative controls,
run before the real check in `scripts/ci/run_guards.py`.
