# S-015 Session A — summary (updated 2026-04-30 21:25 UTC)

**Status:** Session A complete. **Live-behaviour DRAFT (#209)** awaits PM review. Session B (intraday baseline + parameter sweeps) still pending — needs a host with keyless intraday-API egress.
**Scope:** infrastructure + harness validation + a daily-resolution smoke test against real coinmetrics data. **No 5m / 15m P&L claims.**
**Date:** 2026-04-30.

---

## TL;DR for the operator (post-clarification update)

Mid-session you clarified: the no-self-merge rule applies only to **strategy / model promotions**, not to test infrastructure. With that scope:

### Merged this session (8 PRs — infrastructure)

| PR | Title |
|---:|---|
| #200 | S-015 sprint prompt |
| #201 | T1 backtest harness + multi-source keyless fetcher + sampler |
| #202 | T3 harness validation on existing repo fixtures |
| #203 | mid-session checkpoint CP-2026-04-30-10 |
| #204 | T9 Session A summary report (the original of this doc) |
| #205 / #206 | Final session checkpoint CP-2026-04-30-11 (#205 had a CHECKPOINT_LOG conflict; #206 superseded it cleanly) |
| #207 | github-raw fetcher adapter + coinmetrics/data wrapper |
| #208 | daily-resolution smoke test against coinmetrics |

### Held as DRAFT for your hand on the merge button (1 PR)

| PR | Title | Why |
|---:|---|---|
| **#209** | **VWAP timeframe 15m → 5m** | Changes which bars the live trader consumes for `bybit_2`. Per S-015 rule, live-behaviour changes wait for PM. |

### Still gated on Session B (no PRs open)

T2 baseline lock + T4 / T6 / T7 parameter sweeps all need real 5m intraday data. The locked-baseline threshold (Sharpe Δ > 0 AND max-DD not worse > 10 % AND p < 0.10) means no parameter changes can be proposed until that baseline exists.

---

## What blocked the originally-planned T2 / T4 / T6 / T7

T0 audit probed every keyless market-data host called out in the sprint prompt:

| Host | HTTPS status from this sandbox |
|---|---|
| `api.exchange.coinbase.com` | 403 |
| `api.kraken.com` | 403 |
| `query1.finance.yahoo.com` | 403 |
| `min-api.cryptocompare.com` | 403 (DNS resolves but proxy rejects) |
| `huggingface.co` | 403 |
| `pypi.org`, `github.com` | 200 ✓ |

The egress gateway is allowlisted to pypi + GitHub only — confirmed both with raw `curl` (`-k` insecure mode still 403) and via the ccxt SDK's TLS handshake. No keyless market-data adapter can return a frame from inside this box.

Per the operator's option-2 directive ("ship infrastructure now, defer the runs"), Session A built the harness and analysis tooling; **Session B** runs T2 / T4 / T6 / T7 against real OHLCV.

---

## What Session A did ship

### #201 — T1 harness (3 modules)

| Module | LOC | Surface |
|---|---:|---|
| `scripts/sprint015/data_sources.py` | 260 | 5 adapters (Coinbase, Kraken, yfinance, CryptoCompare, HuggingFace placeholder), `fetch_ohlcv()` orchestrator, `DataUnavailableError` on all-fail. **No Bybit, no Binance** — pinned by a contract test. |
| `scripts/sprint015/sample_data.py` | 106 | Month-bucket sampler, recency weights `[0–12mo: 1.00, 13–36mo: 0.50, 37–60mo: 0.25]`, stratified disjoint folds, deterministic seed. |
| `scripts/sprint015/run_backtest.py` | 195 | Pure-function harness, single-position model, 2 bps round-trip slippage, fold-wise metrics (PnL / n_trades / win_rate / Sharpe / max_dd). Does **not** import `src.runtime.orders` / `notify` / `bot.*` / live-trading code. |

24 contract / unit tests pass locally in 0.43 s.

### #202 — T3 fixture analysis

`scripts/sprint015/analyze_fixtures.py` wraps the production VWAP signal builder behind the harness contract and runs slippage sensitivity / signal density / hour-of-day attribution against the two committed repo fixtures. Output: `docs/backtests/sprint-015/harness-validation.md`.

**This is harness validation, not strategy quality.** The fixtures are 7 days + 3.5 days of BTC 1m bars — orders of magnitude too small for real backtest claims. What it *does* prove:

- The CSV → DataFrame → resample → strategy adapter → metrics pipeline is wired end-to-end.
- Slippage sensitivity moves PnL monotonically downward (arithmetic invariant — pinned by a test).
- Signal density falls monotonically as the entry-σ threshold rises (sanity check on the strategy adapter — pinned by a test).
- The harness produces hour-of-day attribution buckets for the eventual killzone overlay.

4 smoke tests pass locally; total Session A test count: **28 passing in 12.66 s**.

---

## Sprint-prompt corrections applied during T0

The original prompt listed Bybit as a fallthrough source. Operator corrected late in T0:

> the testing package should also be able to pull data from open sources on the web that don't require Api keys. don't take data from bybit for training sessions.

The amended prompt locks the no-Bybit / no-Binance rule, sources the fallthrough chain in this order:

```
Coinbase Exchange REST → Kraken REST → yfinance → CryptoCompare keyless tier → HuggingFace
```

The default registry in `data_sources.py` enforces this; a contract test fails the build if Bybit or Binance ever sneaks in.

---

## Concrete first action for Session B

Recommended host: any networked machine OR the Oracle VM (S-014.5 unlocked the `/vm` Telegram dispatcher; Tier 2 needs operator confirmation per `docs/claude/vm-operator-mode.md` though).

```bash
git pull
PYTHONPATH=. python -m pytest tests/sprint015/ -q   # all 28 must pass
PYTHONPATH=. python -c "
import datetime as dt
from scripts.sprint015 import data_sources as ds
df, src, attempts = ds.fetch_ohlcv(
    'BTCUSDT', '1h',
    dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
    dt.datetime(2025, 2, 1, tzinfo=dt.timezone.utc),
)
print(f'source={src} rows={len(df)} attempts={[(a.source, a.ok) for a in attempts]}')
"
```

If that prints a source name and a positive row count, proceed:

1. **T2 — lock baseline.** Run `run_backtest` with current `config/strategies.yaml` params for `vwap` + `turtle_soup`, both on at least 5 stratified folds across 2021-04-30 → 2026-04-30. Write `docs/backtests/sprint-015/baseline.md` with per-fold seeds + commit SHA.

2. **T4 — VWAP sweep** (DRAFT only if the candidate clears Sharpe Δ > 0 AND max-DD not worse > 10 % AND fold-wise paired t-test p < 0.10). Grid over `ENTRY_STD_THRESHOLD` ∈ {0.7, 1.0, 1.3, 1.6, 2.0}, exit threshold, max hold time, partial-TP. Pareto frontier on (PnL, max_dd).

3. **T6 — turtle_soup sweep.** Same threshold, same draft rule. Grid over `atr_stop_mult`, `tp1_at_r`, `tp2_at_r`, `partial_close_pct`, `trail_atr_mult`, `min_sweep_buffer_bps`.

4. **T7 — regime filter probe.** Cheap rolling-ATR + 20/50 EMA-cross classifier as an *additive veto*; only block trades the baseline would have lost. Same draft rule.

5. **T9' — full summary update.** Replace this Session A summary with the merged Session A + Session B view. Negative results documented; no-PR-on-fail.

If the smoke test 403s on every source, **stop** and tell the operator the egress is still blocked — don't synthesize.

---

## Threshold reminder (applies to every experiment branch)

A T4 / T6 / T7 candidate qualifies for a draft PR iff **all three** hold against the locked baseline:

1. `Sharpe delta > 0`.
2. `max-DD not worse by more than 10 %` of baseline max-DD.
3. `fold-wise paired t-test p < 0.10` on per-fold realised P&L.

Failed candidates **do not get a PR** — they're documented in the summary and that's it.

---

## Open issues / improvements carried forward

1. **Centralise telegram stubs in `tests/conftest.py`** — flagged from S-014 CP-09. Module-level `_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[…]])` (PR #184) breaks the `MagicMock` stub used by ~10 existing test files.
2. **Document the recursive `web/templates/**/*.html` whitelist pattern in git-workflow.md** — flagged from S-014 CP-09.
3. **Add a "this sandbox has no market-data egress" note to `docs/claude/testing-policy.md`** — so the next training/backtest sprint doesn't repeat T0's discovery.
4. **HuggingFace community OHLCV adapter is a placeholder** — wire to a specific dataset when one is identified for crypto OHLCV. Until then it always returns `None`.
5. **CryptoCompare adapter only supports `histohour` + `histoday`** — the keyless tier doesn't expose sub-hourly. If Session B needs 5m / 15m / 1m, that source will fall through silently.

---

## Why no PR was opened for "experiments that didn't beat baseline"

Per the operator's negative-results rule (verbatim, sprint planning chat):

> if the only reason to get it to merge is if there's positive results. So if there's negative results, then, like, we should only be attempting to push, but is gonna be an improvement.

So Session A did not open speculative parameter-sweep PRs. Session B will follow the same rule.

---

## Post-clarification work (added 2026-04-30 21:25 UTC)

After the operator's mid-sprint clarification, Session A also re-probed the network and shipped:

### #207 — github-raw adapter (tier-3 keyless source)

`raw.githubusercontent.com` and `git clone` against `github.com` both work from this sandbox. New `fetch_github_raw` adapter in `data_sources.py` curates a tiny registry of keyless github datasets and dispatches per parser:

| Symbol | Timeframe | Source | Provenance |
|---|---|---|---|
| `BTCUSDT` | `1d` | `coinmetrics/data btc.csv` | `PriceUSD` column, ~5,765 daily bars from 2010-07-18 |
| `ETHUSDT` | `1d` | `coinmetrics/data eth.csv` | same shape, daily |

Hard rule pinned by tests: the registry **only** serves daily timeframes. Sub-daily requests return `None` so daily reference rates can't masquerade as 5m / 15m bars. The adapter runs **last** in the source registry — public-exchange APIs always win when reachable.

### #208 — daily-resolution smoke test

`scripts/sprint015/run_smoke_test.py` pulls 5 years of BTC + ETH daily bars from coinmetrics, slices into 5 stratified recency-weighted folds, runs a toy VWAP-deviation adapter through the harness, emits per-fold metrics + a 0/2/10 bps slippage sweep into `docs/backtests/sprint-015/smoke-test-daily.md`.

**This is not a baseline.** The data is daily reference rates with synthesised OHLC. The live trader runs at 5m / 15m intraday — daily results say nothing about live strategy quality. Operator hard rule (verbatim): "we definitely don't want the models learning from incorrect datasets." The runner is harness-validation only, full stop.

Live results from inside this sandbox:

| Asset | Bars | Folds | Trades @ 2 bps | Aggregate PnL @ 2 bps | Slippage monotone? |
|---|---:|---:|---:|---:|:-:|
| BTCUSDT | 1826 | 5 | 11 | -216,752.12 | ✓ (0 → 10 bps degrades PnL) |
| ETHUSDT | 1826 | 5 | 24 | +7,957.28 | ✓ |

The harness arithmetic is correct. End-to-end pipeline runs with real data.

### #209 — VWAP timeframe 15m → 5m (DRAFT — your call)

Operator directive (verbatim): "vwap should be wired to 5 minutes not 15 minutes so we should do that fix as well."

Three coordinated changes so the YAML actually wins on the live VM:

1. `config/strategies.yaml` — `vwap.timeframe`: `"15m"` → `"5m"`.
2. `src/runtime/pipeline.py::vwap_signal_builder` — new resolution order: strategies.yaml → env → default. Previously env hit first; if any account's `.env.bybit_2` had `TIMEFRAME=15m` the YAML change would silently no-op.
3. `.env.example` — default `TIMEFRAME=15m` → `5m` + comment that strategies.yaml wins.

4 regression tests pinning the new behaviour. **Held as DRAFT** because it changes which bars the live trader consumes.

---

## Real intraday data — paths the operator can take outside this sandbox

The sandbox blocks every keyless intraday API I tried. The harness is ready; data isn't reachable from here. Options for getting real 5m / 15m bars *outside* this sandbox, in rough order of effort:

### A. Run Session B on the Oracle VM (lowest effort)

The VM has unrestricted egress and already-vetted Bybit credentials. The `/vm` Telegram dispatcher (S-014.5) can launch a Claude session there. Caveat: per `docs/claude/vm-operator-mode.md`, Tier 2 actions (anything that mutates state, including `pip install pandas scipy ...`) need real-time operator confirmation in Telegram. So Session B on the VM needs the operator online for ~5 minutes at the start to confirm the package install, then the rest can run autonomously.

**Note:** the no-Bybit-for-training rule still applies. The VM should pull data from Coinbase/Kraken/yfinance via the existing fetcher, not from Bybit.

### B. Pre-stage data via a manual download (next-lowest)

Pick one of:

- **Kaggle** datasets: e.g. *Bitcoin Historical Data* (5m bars, multi-year, ~1.5 GB) — operator downloads via browser, `git lfs add` to the repo, harness adapter loads it. One-off, not a recurring pipeline.
- **Polygon.io free tier**: 5 calls/min, 30-day window — operator generates an API key, sets it as a local secret, runs a snapshot script on a personal box, commits the resulting CSV. Recurring updates need human-in-the-loop.
- **Alpaca free tier**: similar shape, also keyed.

These all involve a credential at some point, so they don't fit the "open keyless web" rule for the *repo* — but they're fine when run by the operator on their own box and the data is committed as a fixture.

### C. Add a github-mirrored intraday repo (medium)

Several public github repos host 1m / 5m crypto OHLCV CSVs. With `api.github.com` blocked I couldn't search this sandbox; on a host that can reach it, `gh search repos crypto ohlcv 1m` would surface candidates. Once a maintained one is identified, register it in `_GITHUB_DATASETS` and the harness picks it up automatically.

### D. Self-host an intraday mirror (highest)

Operator runs a tiny scheduled job on the VM that pulls Coinbase / Kraken / Bybit 5m bars (Bybit is fine for *production live data*, just not for *training data*) and writes parquet to S3 / a public github repo. Harness adapter targets that mirror. This is the long-term right answer if S-015-style sprints become recurring; overkill for one sprint.

**My recommendation: A** — Session B on the VM with one Tier-2 confirmation upfront. Lowest operator workload, fastest path to a real baseline, no new infrastructure to maintain.

---

_Generated by Claude Code (S-015 Session A, T9, post-clarification update). Recommended PM review order at top._
