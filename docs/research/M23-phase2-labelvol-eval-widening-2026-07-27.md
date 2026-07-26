# M23 P2 — WS-3b eval-book widening: measured payoff (2026-07-27)

**Anchor:** `MB-20260717-M23-META-LABEL` (label-volume) / workplan 1.1 steps 3a+3c.
**Tier:** Tier-1, offline, trainer-side (read + a `--no-register` harness re-run).
**Prior artifacts:** [`M23-phase2-labelvol-findings-2026-07-19.md`](M23-phase2-labelvol-findings-2026-07-19.md)
(the P2 pool run), [`WS-B-candle-shard-labelvol-scoping-2026-07-26.md`](WS-B-candle-shard-labelvol-scoping-2026-07-26.md)
(the scope), WS-3b = PR #7677 (the harness widening).

## What was measured

WS-3b widened `scripts/ml/m23_phase2_labelvol.sh`'s `MR_PATHS` from the 3 hardcoded
BTC/ETH/SOL 1h shards to an existence-guarded roster over every WS-B-covered shard
at its actual timeframe (alt-USDT 15m + equities/metals 1d). The mechanism: each
shard in `market_raw_paths` lets `setup_candidates._iter_one_symbol` path-resolve
*that symbol's* real closed trades into the eval book. This measures whether the
eval book actually grew past the ~376 BTC-only wall.

**3a — shards present + fresh** ✅ (trainer VM, git head `78e3ca7` = WS-3b merged):
- alt-USDT 15m v002: ADAUSDT / AVAXUSDT / XRPUSDT — built 2026-07-25.
- equities/metals 1d v002: SPY / QQQ / GLD / MGC / MHG (+ the full fleet) — built 2026-07-26.

**3c — eval book, re-run 2026-07-26 22:19 UTC with the merged code:**

| | LIVE eval rows by symbol | LIVE total |
|---|---|---|
| **Before** (Jul-19, pre-WS-3b, 3-crypto pool) | BTC 376 · ETH 7 | **383** |
| **After** (Jul-26, widened `MR_PATHS`) | BTC 376 · ETH 9 · **XRP 8 · ADA 6 · IEF 1** | **400** |

Net **+17** live eval rows (+4.4%). New symbols that now path-resolve: XRP (8),
ADA (6), IEF (1); ETH picked up 2 more.

## The honest finding — necessary, but the wall is not shard-coverage

**WS-3b is mechanically correct and did exactly what it was built to do** — the
XRP/ADA/IEF rows were shard-blocked before (their closed trades couldn't
path-resolve) and now they resolve. That artificial cap is removed.

**But the payoff is small, and the reason is the important result: the eval book
is real-money-closed-trade-COUNT-bound, not shard-coverage-bound.**

- **AVAX 0, SOL 0**, and the **entire equities/metals fleet added 0** (only a single
  IEF row) — despite their shards being present and fresh. They have ~no
  *reconstructable real closed trades* to resolve.
- The eval book counts **real-money, non-backtest, non-demo** closed trades only
  (`setup_candidates._load_live_trades`: `is_demo=0`, `pnl IS NOT NULL`). BTC
  dominates because that is where the real-money trading volume is; the alt +
  equities/metals activity is overwhelmingly on **paper/demo** accounts
  (`bybit_1`, `alpaca_paper`, the soak books), which are **excluded** from the eval
  book by construction.
- So the WS-B premise ("216 of 491 closed trades in the 90d window had no candle
  shard") counted trades that are largely **paper** — real for candle-coverage
  purposes, but not part of the real-money eval book the M23 wall is about.

**EV-gate verdict: still FAIL / no usable edge.** Even at 400 rows, the meta-label's
best net-positive threshold selects only **2–7 trades (0–2% of the book)** — far
below the usable-volume floor (≥40 trades / ≥10% coverage). `p2pool-v1` accuracy
0.735 = the majority baseline 0.735 (VERDICT=FAIL); the R-aware `c1-v1` EV sweep is
"SELECTION EV POSITIVE but below the usable-volume floor" at both τ=0.5 and τ=0.75.
The +17 rows did not move the wall.

## Conclusion + the real next lever (for the morning)

WS-3b was **necessary** (removed a real, artificial cap and is the correct behavior
going forward — as more real-money symbols accrue trades, they now resolve
automatically) but **not sufficient** to break the label-volume wall. The wall is
the **real-money closed-trade count**, dominated by BTC.

The evidence redirects the label-volume workstream (workplan 1.1) away from *more
candle-shard coverage* and toward the levers that actually add labels:

1. **L3 — paper-book labels (the clear next lever).** The paper-portfolio mirror
   (`bybit_portfolio`/`alpaca_portfolio`, `paper_role: portfolio`) + the soak books
   trade the *full* instrument roster continuously. Admitting paper closed trades
   as an **additional, tagged** eval population (an `include_paper` path into
   `_load_live_trades`, kept a distinct split so real-money stays the ground truth)
   is where the volume actually is. **This is a design choice with a leakage/
   domain-shift tradeoff → flag for operator review before building.**
2. **L5 — net-R relabel** as broker-truth fee coverage widens (separate lever).
3. **Lean on powered-offline discrimination** (workplan 1.2 / N2): the live eval
   book is too thin to *prove* a meta-label edge, so the standing offline
   replay + purged-WF-CV `oos_edge` path is the honest way to show discrimination
   without waiting on live volume.

**Bottom line:** the label-volume constraint is not a plumbing gap anymore (WS-3b
closed that) — it is a real-money-volume constraint, addressed by L3 paper labels
(operator-gated design call) + offline discrimination, not by more shards.
