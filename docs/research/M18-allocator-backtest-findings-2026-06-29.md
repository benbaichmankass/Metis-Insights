# M18 Capital Allocator — backtest findings + overnight research plan (2026-06-29)

> **Status:** research log. Tier-1 (analysis/backtests only). **No Tier-3 action taken** — every
> strategy/risk/sizing/config/promotion decision is parked for the operator. This doc is the durable
> anchor for the overnight autonomous research run (operator went to bed 2026-06-29, asked for
> results by morning; (b) + (c) below in parallel; Tier-3 decisions deferred).

## What shipped (all merged to `main`, observe-only)

P0b candidate-batch exposure (#5086) · P0c allocator soak + regret `/api/bot/allocator/soak` (#5092)
· P1 cost-aware EV_R scorer (#5098) · P0a per-trade cost capture (#5099) · BT `--allocator off|ev`
arm in `scripts/backtest_system.py` (#5107). Design `docs/research/capital-allocation-ai-DESIGN.md`;
plan `docs/sprint-plans/M18-CAPITAL-ALLOCATOR-EXECUTION-PLAN.md`; ROADMAP § M18.

## Backtest evidence to date — intra-symbol EV-ranking has NO edge

Harness: `scripts/backtest_system.py --allocator off|ev` on the trainer VM, BTC 5m
(`data/backtest_BTCUSDT_5m.csv`, 2020–2026), default full roster + default sizing (risk_pct 0.3,
flip_policy reverse) — **NOT the live config** (no regime gating / conviction / per-account routing).

| Window | Arm | Net PnL | Return% | maxDD% | Ret/DD | Trades | Win% | multi-cand bars | EV≠priority |
|---|---|---|---|---|---|---|---|---|---|
| 2026-01→05 (5mo) | off | −$986 | −9.86 | 11.82 | −0.82 | 187 | 40.1 | — | — |
| 2026-01→05 (5mo) | ev | −$1,069 | −10.69 | 12.63 | −0.83 | 189 | 40.2 | 195 | **6** |
| 2025-01→2026-05 (17mo) | off | −$4,202 | −42.02 | 42.71 | −0.97 | 688 | 37.5 | — | — |
| 2025-01→2026-05 (17mo) | ev | −$4,176 | −41.76 | 42.20 | −0.98 | 682 | 37.2 | 720 | **25** |

**Verdict:** priority routing and EV-ranking agree **~96–97%** of the time (6/195 and 25/720
divergences); on the disagreements the net effect is **noise** (EV −$83 worse over 5mo, +$26 better
over 17mo on $10k). **The intra-symbol selector (P2) is not worth building on this scorer** — the
existing priority order is already ~EV-aligned. This is the "test-before-we-commit" payoff.

Two caveats that define the overnight work:
- **(b) The real thesis is untested.** The harness is single-symbol → it only tests "which competing
  *BTC strategy*". The allocator's actual value is **cross-symbol / cross-account** (deploy capital to
  the best *market*: BTC vs ETH vs gold vs prop). Needs a multi-symbol harness.
- **(c) Both arms lost ~42%/17mo** — but that's the harness config (all strategies on BTC, default
  sizing, flip_policy reverse, no live regime gate), **not live P&L**. Worth isolating whether the
  loss is a harness-config artifact vs genuine strategy bleed.

## Overnight plan (Tier-1 research; Tier-3 parked)

### (c) — Why −42%? (existing harness, no new code) — HIGH CONFIDENCE
Run on the trainer (serialized; collect via `trainer-vm-diag` relay → `/tmp/alloc_*`):
1. **Live-like config** A/B: `--regime-router on --flip-policy hold` (the live defaults) vs the
   default `reverse`/no-gate — does the loss shrink materially? Isolates config artifact vs bleed.
2. **Per-strategy attribution**: the harness prints a per-cell `strategy|trend|vol|side` $ table
   (already in the run logs) — tabulate which strategies are the net losers over 17mo.
3. Record into this doc's "Results" section below.

### (b) — Cross-symbol / cross-market allocator backtest (new Tier-1 tooling) — AMBITIOUS
Build `scripts/research/allocator_multisymbol_backtest.py` (or extend the harness): load N symbols
(BTC/ETH/SOL/… 5m + the 1h ETF/gold cells available on the trainer: `data/<SYM>_<tf>.csv`), run each
symbol's strategies, and at each tick gather the **cross-symbol candidate set**, then compare:
- **baseline**: each symbol sized independently (today's behaviour), vs
- **allocator**: a shared capital/risk budget allocated to the top-EV candidates across symbols
  (greedy EV/risk under a max-concurrent / margin cap).
Metric: portfolio net-R / maxDD + how often the allocator skipped a lower-EV symbol's trade for a
higher-EV one. **Validate the harness on the local sample before the trainer run.** This is where an
allocator could actually add value (picking the market, not the BTC-strategy).

### Trainer data inventory (confirmed)
`backtest_BTCUSDT_5m.csv` (6yr, 2020–2026), `ETH/SOL/ADA/XRP/BNB/AVAX/LINK_{5m,15m}.csv`,
`SPX500_1m.parquet`, `GC_F_1h.csv`, `SPY/QQQ/IWM/GLD/TLT/USO_1h.csv`, `btc_1h_multiyear.csv`.
Trainer = 1 OCPU box → backtests are serial + slow (5mo BTC ≈ 16 min; 17mo ≈ 40 min). Full roster
includes non-BTC strategies that waste signal-gen on BTC data — restrict roster for speed where it
helps.

## Results (2026-06-30, overnight)

### (c) — the −42% was MOSTLY a harness-config artifact (but residual bleed remains)
Live-like 17mo BTC run (`--regime-router on --flip-policy hold`) vs the default-config baseline:

| 17mo BTC config | Net | Return% | maxDD% | Trades | Win% |
|---|---|---|---|---|---|
| default (no gate, flip=reverse) | −$4,202 | −42.0 | 42.7 | 688 | 37.5 |
| **live-like (regime gate + flip=hold)** | **−$1,196** | **−11.96** | **14.1** | **290** | 36.6 |

The live regime gate + hold-flip **cut the loss ~3.5×** (−42% → −12%) and maxDD ~3× — and gated out
~58% of trades (688→290, the money-losers). So the scary −42% was largely the naive harness config,
**not** the live system. **But −12%/17mo is still net-negative** — residual bleed is real. Per-strategy
losers (live-like run): **`ict_scalp_5m` −$505** (its *short* cell alone −$353), `trend_donchian` −$365,
`trend_donchian_eth` −$234, `turtle_soup` −$194; only `eth_pullback_2h` (+$91) / `squeeze_breakout_4h`
(+$37) net green. → A real strategy-performance lead (Tier-3, parked): `ict_scalp_5m` shorts +
`trend_donchian` are the bleeders.

### (b) — cross-symbol allocation DID show an edge (first positive signal; caveated)
Cross-symbol allocator (BTC+ETH+SOL, 5m, 2026-01→06, `--max-concurrent 2`):

| Arm | Net | Return% | maxDD% | Trades | Win% |
|---|---|---|---|---|---|
| baseline (independent per-symbol) | −$87 | −0.87 | 2.84 | 136 | 30.2 |
| **allocator (shared budget, greedy EV)** | **+$133** | **+1.33** | 6.52 | 113 | 33.6 |

`ev_beats_baseline_net = true` (+$221 delta). Crucially the allocator **actually made binding
cross-symbol choices**: `contested_ticks=19, lower_ev_skips=10, budget_binds=10` — it skipped a
lower-EV symbol's trade for a higher-EV one 10× (NOT a no-op, unlike the intra-symbol test). Win rate
rose (30→34%) on fewer trades — consistent with EV-ranking picking better trades.

**CAVEAT (don't over-claim):** the raw net gap **conflates sizing with selection** — the allocator
arm sizes off the full shared balance while baseline uses per-symbol slices, which also explains its
higher maxDD (6.5% vs 2.8%). A **sizing-normalized A/B** (same per-trade risk + same concurrency cap
in both arms; differ only in fill order: EV vs symbol-priority) is needed to isolate the *selection*
edge from the *sizing* effect. Also: one 5-month window, 10 binds (small sample), freshly-built
harness (a lookahead bug was found + fixed pre-run).

**Net read:** intra-symbol EV-ranking = no edge; **cross-symbol allocation = promising** (real
binding decisions, beat baseline) — exactly where the design predicted value. Worth the
sizing-normalized confirmation before any build decision (all Tier-3, parked).

