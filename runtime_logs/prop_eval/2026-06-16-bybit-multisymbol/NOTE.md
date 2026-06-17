# Bybit multi-symbol COST-AWARE EV sweep — trend_donchian is +EV on alts (2026-06-16)

Research run for the operator directive "everything Bybit-tradeable is fair game,"
using the cost-aware EV objective (expected $ netted per horizon, NET of fees,
re-buying a fresh account on each breach; BANK-ASAP withdrawal). Continues the
BTC-only conclusion (only the slow `fvg_range_15m`/`squeeze` pass survived on BTC;
HF BTC failed) by testing the EXISTING strategies on other liquid Bybit perps.

> **HEADLINE — a genuine +EV prop strategy found.** `trend_donchian` (a *loser*
> on BTC) is **strongly +EV on every high-vol alt tested**, out-of-sample (the
> BTC-tuned config, NO per-symbol re-tuning). It is the top cell for all 5 symbols
> at 1.5% risk, and the entire top-8 (12-mo EV) is `trend_donchian`. It works
> *because* of the bank-ASAP economics: it breaches + re-buys ~4-6 accounts/yr but
> banks far more than the fees first — exactly the "burns fast but worth it" thesis.

Tier-1 research only — NOTHING here touches `config/strategies.yaml`,
`config/accounts.yaml`, or the live order path. Wiring `trend_donchian` to an alt
for the prop account is a separate Tier-3, operator-gated decision.

## Result — 12-month cost-aware EV (clean run), best cell per symbol

All `trend_donchian @ risk 1.5`; $5k Breakout 1-Step (fee $45, 80/20, 6% static DD):

| symbol | mean net $ /12mo | P(net>0) | median $ | p5 | accts burned/yr | fees $ | ROI on fees |
|---|---|---|---|---|---|---|---|
| **SOLUSDT** | **+$1,707** | 95% | +$1,596 | +$77 | 4.0 | $180 | 9.5× |
| **ETHUSDT** | **+$1,183** | 93% | +$1,130 | −$180 | 5.1 | $230 | 5.1× |
| **BNBUSDT** | **+$1,101** | 92% | +$1,033 | −$225 | 5.6 | $251 | 4.4× |
| DOGEUSDT | +$587 | 80% | — | — | 5.6 | $252 | 2.3× |
| XRPUSDT | +$576 | 76% | +$495 | −$315 | 5.7 | $255 | 2.3× |
| _BTCUSDT (baseline)_ | _(computing — fold in)_ | | | | | | |

EV scales with realised volatility (SOL > ETH ≈ BNB > XRP ≈ DOGE). At 1.0% risk
the EV is lower but P(net>0) and ROI/fee hold up (e.g. SOL @1.0 = +$1,018, 87%,
11× ROI; fewer accounts burned — 2.0 vs 4.0). Other strategies (`squeeze`,
`fvg_range_15m`, `squeeze+fvg`) are flat-to-negative on the alts; `turtle_soup`
is a strong loser (ETH −$632, 14 accounts burned). `trend_donchian` is the signal.

## CRITICAL: a cache bug invalidated the first run (caught + fixed)

The FIRST cross-symbol run returned near-identical EV for all 5 coins (~$2,342
each) — impossible, and the tell. Root cause: `scripts/backtest_system.py::
generate_signal_stream` keyed its signal-stream cache on the hardcoded string
`"6yr"` for the data identity (the docstring claimed "keyed by …data…" but the
code never honored it), so every symbol after the first **silently reused the
first symbol's cached signals** — and the first cache hit was an earlier BTC run,
so all 5 "alts" were really BTC. Fixed in commit `5176b69`: the key now includes
a feed fingerprint (bar count + first/last timestamp + first/last close). The
numbers above are the CLEAN re-run — each symbol's own signals (confirmed: the
per-symbol EVs now genuinely differ, $576-$1,707, instead of being identical).
BTC-only prior runs were self-consistent (same data) and remain valid.

## Method / data

- Data: Binance Vision spot 5m monthly klines (2023-01 → 2026-02, ~332k bars/sym),
  fetched via `scripts/ops/fetch_binance_vision.py` (Bybit REST is 403-blocked from
  the sandbox; Binance Vision is reachable and is the same source as the BTC cache).
- Engine: `scripts/prop/montecarlo_prop.py --cost-aware` over the real per-strategy
  `order_package` ledger, block-bootstrap (3000 paths, block_len 8, seed 1234),
  risk grid {0.5, 1.0, 1.5}, clock_tf 1h, flip_policy hold. EV model in
  `src/prop/montecarlo.py::run_ev_montecarlo`; economics from
  `config/prop_rulesets/breakout.yaml`.

## Caveats (honest)

1. **Binance spot ≈ Bybit linear-perp within ~0-10 bps basis** (per the repo's own
   BTC-archive note). A *relative* ranking is sound; absolute EV should be
   re-checked on actual Bybit perp data + funding before sizing real capital.
2. **EV is realised-only optimistic** — the per-trade bootstrap has no intraday
   open-position swing, so daily-loss/DD breaches (hence fee churn) are
   UNDER-counted; true EV is somewhat lower than shown.
3. **Out-of-sample is a strength, but params are BTC-tuned, not alt-optimized** —
   the 5/5 consistency argues a real trend-following edge on high-vol alts (not
   luck), and alt-specific tuning could improve it further (or reveal fragility).
   A walk-forward per alt is the natural next validation.
4. `trend_donchian` inherently breaches + re-buys (~4-6 accts/yr); that is priced
   into the EV (fees subtracted, re-buys simulated). It is NOT a low-DD survivor —
   it is a high-EV churner, which is the correct objective for a disposable $45
   prop account under bank-ASAP.

## Recommendation / next steps

- **`trend_donchian` on a high-vol alt (SOL or ETH) at ~1.0-1.5% risk is the first
  genuine +EV prop candidate** this effort has produced — ~4-5× the BTC baseline.
  SOL @1.0 (+$1,018, 87%, 11× ROI, only 2 accts/yr) is the best risk-adjusted cell.
- Before any live wiring (Tier-3, operator-gated): (a) re-validate on real Bybit
  perp candles + funding, (b) a per-alt walk-forward, (c) decide execution path
  (the `prop_signal`/Comet manual bridge for the supervised soak, or MetaCopier
  for unattended).

## Reproduce

```bash
python3 scripts/ops/fetch_binance_vision.py --symbol SOLUSDT --start 2023-01 --end 2026-02
python3 scripts/prop/montecarlo_prop.py --data ~/ict-trader-data/solusdt_5m.parquet \
  --combos "fvg_range_15m;squeeze_breakout_4h;squeeze_breakout_4h,fvg_range_15m;trend_donchian" \
  --risk-pct-grid "0.5,1.0,1.5" --n-paths 3000 --clock-tf 1h --cost-aware \
  --out-dir runtime_logs/prop_eval/2026-06-16-bybit-multisymbol/solusdt
# -> ev.md / ev.json (ranked by EV-$), montecarlo.md (survival)
```
