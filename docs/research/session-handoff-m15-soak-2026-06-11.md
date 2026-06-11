# Session Handoff — M15 Soak + Next Evidence Sweeps (2026-06-11)

> Written at the close of the M15 build-out session (memo PR #3273 →
> go-live PRs #3336–#3340). The practice fleet is LIVE; this doc is the
> kickoff brief for the follow-up session. Read
> [`m15-phase0-results-2026-06-10.md`](m15-phase0-results-2026-06-10.md)
> and sprint log `S-M15-PHASE4-SOAK-2026-06-11.md` first.

## State you inherit

- **Live-on-practice since 2026-06-11**: `xauusd_trend_1h` (gold 1h
  trend) on `oanda_practice` (fxpractice host, paper $100k);
  `spy_trend_long_1d` / `qqq_trend_long_1d` / `gld_pullback_1d` on
  `alpaca_paper` (paper-api host, paper $100k). Roster 16. All flips
  operator-approved + mode-mirrored in repo YAML.
- Pre-existing trading untouched (Bybit demo/real BTC roster, IBKR
  paper futures).
- Backlog: `BL-20260611-M15-1` (runtime_status live-map missing the two
  new accounts), `BL-20260610-M15-1` (backtesting-skill net-of-fee
  claim vs `backtest_ict_scalp.py`).

## Workstream A — soak verification (do first, mostly reads)

1. **First-fill verification per broker** (whenever each first order
   lands): diag relay → journal `trades`/`order_packages` rows present,
   `is_demo` stamped, SL/TP recorded; broker-side protection visible in
   `positions()`; dashboard renders the account/strategy.
2. **FX weekend gate, first live window** (Fri 21:00 UTC → Sun 21:00
   UTC): expect `fx_market_closed` eval rows for `xauusd_trend_1h`, no
   fetch errors, clean resume Sunday.
3. **Balance probes**: confirm `oanda_practice` / `alpaca_paper`
   balances appear in `runtime_logs/balance_snapshots.json` (hourly
   reporter) — if the reporter doesn't know the new exchanges, that's a
   small wiring PR (relates to BL-20260611-M15-1).
4. **Candle fidelity cross-check** (Tier-1 trainer job): OANDA XAU_USD
   1h vs the Phase-0 Dukascopy series; Alpaca SPY/QQQ/GLD 1d vs the
   Dukascopy ETF dailies. Quantify OHLC deltas; material divergence
   would qualify the Phase-0 evidence.

## Workstream B — SPY/QQQ intraday validation (operator item 1)

Goal: decide whether `spy_*` intraday deserves a shadow slot.
- Trainer worktree `/home/ubuntu/m15-phase0` still holds the corrected
  RTH datasets (`data/{SPY,QQQ}_5m_rth.csv`) + harnesses.
- Run **k-fold anchored walk-forward** (mirror the M8 S3 method —
  `scripts/ml/strategy_tune_sweep.py` has the pattern; the simple
  alternative is multiple `--start/--end` folds on
  `backtest_ict_scalp.py` pre-split CSVs + `backtest_fvg_range.py`)
  for: SPY 5m ict_scalp (net via
  `scripts/ops/m15_net_ict_scalp.py`), SPY 15m fvg_range, and the QQQ
  equivalents. Gate: positive in EVERY fold net-of-fee (the `robust`
  bar) + fee headroom to ~2× assumed costs.
- If SPY passes → propose a Tier-3 PR: `spy_ict_scalp_5m` (or
  fvg_range variant) at `execution: shadow` on `alpaca_paper`,
  us_equity-gated. QQQ stays research unless it newly passes the same
  bar.

## Workstream C — Bybit alt generalization sweep (operator item 3)

Goal: which existing BTC strategies transfer to liquid Bybit alt perps.
- Universe (same keys/connector, zero new integration): ETHUSDT,
  SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, LINKUSDT, AVAXUSDT.
- Method: fetch multi-year candles per symbol on the trainer (CCXT,
  like `data/backtest_BTCUSDT_5m.csv` was built), then the Phase-0
  pattern — `m15_phase0_sweep.sh` is the template: trend 1h/4h,
  pullback 2h, ict_scalp 5m (net post-processor), fvg_range 15m,
  train/OOS split, 7.5 bps roundtrip (Bybit perp costs, NOT the 2 bps
  FX/ETF figure). Per-symbol re-tune only for cells that screen
  positive (crypto params don't transfer — WS-A finding).
- Caveat to carry into the report: alts correlate ~0.7–0.9 with BTC —
  this buys frequency, not diversification.
- Output: generalization matrix + Tier-3 proposals for robust cells.

## Ground rules (unchanged)

Trainer VM is autonomous (relay label `trainer-vm-diag-request`); live
VM reads via `vm-diag-request`, mutations via `system-action` (Tier-2
ack in chat). Any new strategy/account change = Tier-3 draft PR until
explicit operator approval. Real-money promotion needs new per-broker
keys + `OANDA_ENV`/`ALPACA_ENV` flips + `set-account-mode` — never
implied by paper performance alone.
