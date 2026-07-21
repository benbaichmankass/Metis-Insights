# M27 P0 — Batch-3 equities/ETF findings (SPY/QQQ/IWM/TLT/GLD/SLV/GDX/USO/IEF, 2026-07-21)

**Question:** does the ict_scalp_5m setup transfer to the equities/ETF sleeve
(SPY, QQQ, IWM, TLT, GLD, SLV, GDX, USO, IEF) under a venue-plausible cost
model?

**Answer: no verdict is possible for any of the 9 symbols — the batch is
severely underpowered, and the root cause is a data-availability constraint,
not (yet) evidence about the setup itself.** Every symbol fired between
**1 and 15 trades total** over the full 60-day window; the anchored 4-fold
k-fold therefore carries 0–5 trades per fold — noise, not evidence, exactly
the same failure mode as Batch-2's futures result. IWM (15 trades) and SLV
(10 trades) are the least-starved and show an encouraging-but-meaningless
3/4-folds-positive baseline; nothing here clears (or fails) a statistical
gate.

## Root cause — the yfinance data cap, not the venue

Unlike Batch-1 (Bybit klines, multi-year) and Batch-2 (IBKR history pulls,
~1 year), Batch-3's data source is **yfinance** (keyless, deliberately —
see the rig PR #7259: no live equities scalp leg exists yet to be
config-exact against, and copying a production Alpaca key onto the trainer
would cross the "never copy production secrets to the trainer" line even
read-only). Yahoo's own API caps 5m/15m intraday history at **~60 calendar
days**, which at 5m resolution over regular trading hours is **4680 bars**
per symbol — roughly **1/6th** of Batch-1's crypto history and **1/12th**
of Batch-2's futures history. A setup that fires ~285 trades/yr on 24/7
crypto (Batch-1) firing only single digits over 60 RTH-only days on
equities is broadly consistent with **both** genuine lower setup frequency
(equities close 16.5h/day, no overnight liquidity structure) **and** simply
having 6-12x less data to find setups in — this batch cannot distinguish
the two, and does not try to.

## Rig

- Data: yfinance 5m + 15m bars, ~60 days (`2026-04-23 13:30 → 2026-07-20
  19:55` UTC, RTH-only — Yahoo's intraday bars are already exchange-session
  bars, no synthetic overnight fill), pulled trainer-side via
  `scripts/research/m27/fetch_yfinance_5m.py` (PR #7259) — 4680 5m bars /
  1560 15m bars per symbol, all 9 symbols clean (trainer relay #7263).
- Vol-spec derivation: `run_symbol_p0.py --derive-window prefix:0.25`
  (the max allowed fraction) — only 1170 bars, far short of the
  crypto/futures-calibrated 10k-bar floor. Added an explicit
  `--min-derivation-bars` override (PR #7269, default unchanged at 10,000)
  so this batch could relax it deliberately (`--min-derivation-bars 500`)
  without silently loosening the floor for a future deep-history batch.
  **This means Batch-3's frozen vol-spec edges are derived from a much
  thinner sample than Batch-1/2** — a second, compounding reason to treat
  any per-cell (`calm_only_*`, `off_cells_*`) breakdown as illustrative only,
  on top of the overall trade-count scarcity.
- Cost model: **3.0 bps round-trip**, uniform across all 9 symbols
  (`kfold_oos.py --fee-bps-roundtrip 3.0`) — a ballpark estimate for
  commission-free Alpaca-style execution against typical liquid-ETF
  spreads, **not measured per-symbol**. Given the trade counts below, the
  fee assumption is not the binding constraint either way — it would not
  change the "no verdict" conclusion for any symbol.
- Artifacts: trainer `/home/ubuntu/m27_out_eq/<SYM>/`; full run output
  mirrored in relay issue #7271.

## Results

| Symbol | Trades (60d) | Win % | Gross ExpR | Gross TotR | Net baseline k-fold |
|---|---|---|---|---|---|
| SPY | 2 | 50.0 | +0.250 | +0.50 | 1/1, n=1, +1.39 |
| QQQ | 2 | 100.0 | +1.083 | +2.17 | 1/1, n=1, +1.13 |
| IWM | 15 | 53.3 | +0.057 | +0.86 | 3/4, n=12, totR +1.13, expR +0.094 |
| TLT | 2 | 50.0 | +0.593 | +1.19 | 0/1, n=1, −0.40 |
| GLD | 3 | 66.7 | −0.153 | −0.46 | 1/1, n=1, +0.12 |
| SLV | 10 | 60.0 | +0.376 | +3.76 | 3/4, n=8, totR +3.05, expR +0.381 |
| GDX | 12 | 50.0 | +0.305 | +3.66 | 3/4, n=10, totR +0.99, expR +0.099 |
| USO | 9 | 44.4 | −0.009 | −0.08 | 1/3, n=6, totR +0.43, expR +0.072 |
| IEF | 1 | 0.0 | −0.155 | −0.15 | 0/1, n=1, −0.33 |

**Every symbol is n ≤ 15 total, n ≤ 5 per fold.** SPY/QQQ/TLT/GLD/IEF each
fired 1–3 trades over the ENTIRE 60-day window — a single fold's worth of
data by Batch-1's standard, split four ways. IWM and SLV are the two
"healthiest" by count and both show a positive-leaning baseline (3/4 folds),
but 8–12 total observations across 4 folds is not a statistical result by
any of this milestone's own standards (Batch-1 required ≥3/4 folds
**and** a meaningfully powered per-fold n; Batch-2 explicitly rejected
2–4-trades-per-fold cells as "noise, not evidence" — the same bar applies
here).

## Recommendation (no Tier-3 — proposals only)

- **No equities/ETF 5m scalp leg from this batch.** None of the 9 symbols
  reaches a gate; the trade counts are too thin to accept OR reject the
  setup on this data.
- **Do not re-run this exact batch expecting a different answer** — the
  binding constraint is the 60-day yfinance window, not anything about the
  symbols or the setup. Two paths forward, either logged to the
  performance-review backlog (`PB-20260721-M27-EQUITIES-DATACAP`) for a
  future session to pick up:
  1. **Deeper history via a data-only, non-production source** — a paid or
     free-tier historical-bars provider with real years of 5m/15m equities
     data (not Alpaca's live trading key; a genuinely separate,
     trainer-safe data credential, if the operator wants to provision one).
  2. **Coarser timeframe** — this milestone's own P1 phase already plans a
     15m timeframe sweep; equities/ETF 15m bars may have a longer yfinance
     lookback in practice (not verified this session) and, per the Batch-2
     precedent, wider bars naturally accrue more setups per unit of
     wall-clock history.
- **IWM and SLV are the two symbols worth prioritizing if/when deeper
  history becomes available** — not because they passed anything, but
  because they're the only two that even approached a fold-level trade
  count worth reading.

## Coverage impact

SPY/QQQ/IWM/TLT/GLD/SLV/GDX/USO/IEF 5m cells resolve **❌ rejected
(underpowered — 1–15 trades over 60d, data-capped not setup-capped, no
gate)** — see `docs/research/artifacts/m27/coverage.md`.
