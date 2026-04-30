# S-015 Session A — summary

**Status:** Session A complete. Session B (the actual baseline + parameter sweeps) still pending.
**Scope:** infrastructure + harness validation. **No P&L claims.**
**Date:** 2026-04-30.

---

## TL;DR for the operator

Five drafts opened, none merged:

| PR | Title | Stack base | Why it's a draft |
|---:|---|---|---|
| #200 | S-015 sprint prompt | `main` | Sprint-wide rule: every PR is a draft. |
| #201 | T1 backtest harness + multi-source keyless fetcher + sampler | `main` | 941 LOC over the 400-LOC budget; cohesive deliverable. |
| #202 | T3 harness validation on existing repo fixtures | `claude/s015-t1-harness` | Stacks on #201; rebases automatically once #201 merges. |
| #203 | mid-session checkpoint CP-2026-04-30-10 | `main` | Docs-only checkpoint. |
| #204 | T9 Session A summary (this doc) | `claude/s015-t3-fixture-analysis` | Stacks on #202. |

**Recommended PM review order:** #200 → #201 → #202 → #204 → #203 (#203 is independent and can be merged any time).

**No experiment / parameter PRs were opened.** S-015 T4 / T6 / T7 are gated by the locked-baseline threshold (Sharpe Δ > 0 AND max-DD not worse > 10 % AND p < 0.10), and the baseline itself can't be locked from this sandbox. That work is **Session B**.

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

_Generated by Claude Code (S-015 Session A, T9). PM review order at the top._
