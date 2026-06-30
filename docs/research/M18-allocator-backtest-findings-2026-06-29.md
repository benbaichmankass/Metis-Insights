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

### (b-norm) — the sizing-normalized A/B: the cross-symbol "edge" is SIZING, not SELECTION
The confirmation run (`shared_priority` control arm — same shared-budget engine + same per-trade
sizing + same `max_concurrent` cap as the EV allocator, differing ONLY in the contested-slot ranking:
EV_R vs an EV-blind symbol-priority order). BTC+ETH+SOL 5m, 2026-01→06-18, `--max-concurrent 2`:

| Arm | Net | Return% | maxDD% | Trades | Win% |
|---|---|---|---|---|---|
| baseline (independent per-symbol budgets) | −$58 | −0.58 | 2.93 | 137 | 29.9 |
| allocator (shared budget, **greedy EV**) | +$156 | +1.56 | 7.20 | 113 | 31.9 |
| **shared_priority (shared budget, EV-BLIND symbol-priority)** | **+$163** | **+1.63** | 7.14 | 112 | 31.3 |

- `ev − baseline = +$213` (the earlier (b) "edge") **conflates sizing with selection**.
- **`ev − shared_priority = −$7` · `ev_beats_priority = FALSE`** — holding sizing + concurrency
  identical and changing ONLY the ranking rule, EV is *marginally worse* than a trivial
  symbol-priority order. There were **19 genuinely contested ticks** (11 binds) where the two rules
  made different choices — so this is a real test, not a no-op, and the EV scorer did **not** win it.

**Verdict: the cross-symbol gain was a SIZING artifact, not an EV-selection edge.** The whole +$213
came from the shared budget concentrating capital into ETH's winners (ETH carries the book in every
arm); the EV *scorer* adds no cross-symbol selection skill over dumb priority — exactly mirroring the
intra-symbol result (EV ≈ priority ~96–97%). And the sizing lever is not free: it costs **2.5× the
maxDD** (7.2% vs 2.9%) — i.e. it's leverage/concentration, a risk decision, not something an "AI
allocator" is needed to pull.

**CAVEATS (bounds on the claim — it rules out the SCORER, not the allocator concept):** one
5.5-month window (data ended 2026-06-18), 19 contested ticks (small), one `max_concurrent`, a single
shared-account model with no correlation budgeting. Critically, the scorer here is the **P1
confidence-proxy `EV_R`** (`P_win = c_strat`), NOT the design's full conviction/ML/per-cell-expectancy
blend (§5.2). A better-calibrated `P_win` could still surface a selection edge — so the open question
is **scorer quality**, not allocator plumbing.

**Net read (updated):** intra-symbol EV-ranking = no edge; cross-symbol EV-ranking = **no selection
edge either once sizing is normalized** — the apparent (b) edge was capital concentration. On the
*current* (confidence-proxy) scorer the allocator's *selection* adds nothing over priority routing.
→ **Do NOT build the Tier-3 cross-symbol selector on this scorer.** The worthwhile next probe (Tier-1,
parked for operator steer) is whether a better-calibrated `P_win` (conviction blend / per-cell
historical expectancy) yields a *real* selection edge in this same sizing-normalized harness — a
scorer-quality investigation. The sizing lever (shared-budget concentration) is real but is just
leverage with ~2.5× the drawdown; that's a risk call, separate from the allocator thesis. All Tier-3
decisions remain parked.

### (scorer probe) — is there ANY learnable ranking signal? (first pass, n=136)
Before fitting a learned ranker we asked the prior question with
`scripts/research/allocator_candidate_dataset.py`: simulate every actionable candidate standalone
(single-position-per-symbol, no lookahead, fixed notional → balance-free net-R) and measure whether
any decision-time feature separates winners from losers. First pass (BTC+ETH+SOL 5m, 2026-01→06-18,
**136 candidates**, 30.1% win, mean net-R −0.035):

| feature | AUC(win) | corr(net-R) | feature | AUC(win) | corr(net-R) |
|---|---|---|---|---|---|
| confidence (c_strat) | 0.534 | +0.054 | ret_1h | 0.459 | **−0.104** |
| ev_r (current scorer) | 0.539 | +0.057 | ret_4h | 0.451 | −0.096 |
| rr | 0.559 | +0.015 | ret_12h | 0.484 | −0.061 |
| tp_dist_pct | 0.571 | +0.052 | hour_utc | 0.443 | **−0.133** |
| stop_dist_pct | 0.435 | −0.028 | vol_1h | 0.490 | +0.048 |
| mom_align_1h | 0.501 | +0.033 | dow | 0.500 | −0.008 |

Reads:
- **The current scorer is barely informative** — `ev_r` AUC 0.539 (≈ coin flip). That is *mechanically
  why* it ties dumb priority: a near-0.5 ranker can't out-select.
- **The strongest single features are NOT in the scorer:** short-horizon **mean-reversion**
  (`ret_1h`/`ret_4h` corr ≈ −0.10 — candidates entering *after* a recent up-move do worse) and
  **time-of-day** (`hour_utc` corr −0.13). A ranker that faded recent momentum + avoided bad hours
  could plausibly beat priority — the first concrete lead.
- **The dominant effect is owner/symbol IDENTITY, not a cross-candidate feature:** `trend_donchian`
  (BTC) n=21 / 19% win / **net-R −9.7** carries almost the entire loss; `trend_donchian_eth` n=55 /
  +4.5 carries the gain. That's a strategy-performance call (Tier-3, already flagged — and the live
  regime gate already filters trend_donchian's worst cells per finding (c)), not a ranker insight.

**CAVEAT:** n=136 is too small — a corr of 0.10 at n=136 has a 95% CI ≈ ±0.17, so only `ret_1h`/
`hour_utc` are near significance and none is strong. **Fitting a model on 136 noisy rows would
overfit.** → Grew the dataset to multi-year to test stability before fitting (below).

### (scorer probe, n=1559) — the leads were NOISE; no stable ranking signal exists
Multi-year re-run (BTC+ETH+SOL 5m, **2022-01→2026-05**, ~4.4yr, **1559 candidates**, 34.8% win,
mean net-R +0.045). **Every n=136 "lead" washed out — most flipped sign**, the signature of a
small-sample artifact:

| feature | AUC @ n=136 | AUC @ n=1559 | feature | AUC @ n=136 | AUC @ n=1559 |
|---|---|---|---|---|---|
| ev_r (scorer) | 0.539 | **0.484** | ret_1h | 0.459 | 0.487 (corr −0.104 → **+0.031**) |
| confidence | 0.534 | 0.524 | hour_utc | 0.443 | 0.508 |
| rr | 0.559 | 0.469 | ret_4h | 0.451 | 0.475 |

Every feature now sits in **0.47–0.53** — none meaningfully off 0.5; `ev_r` is *below* chance. The
per-owner table also de-confounded: the n=136 "trend_donchian-BTC bleeds −9.7" was a 2026-window
artifact — over 4.4yr `trend_donchian` is **+16.3**, and the best owners are the **4h variants**
(`trend_donchian_sol_4h` 51.5% win / +25.7), i.e. even strategy identity is regime/period-dependent,
not a stable cross-the-board rank.

**Walk-forward logistic ranker** (`allocator_ranker_eval.py`, expanding-window train-on-past/
test-on-future, train-fold-only standardization, pooled OOS over 1299 candidates):

| model | OOS AUC | vs baseline |
|---|---|---|
| `ev_r` single feature | 0.484 | (current scorer — below chance) |
| `confidence` single feature | 0.524 | the best single feature, still trivial |
| **market-features-only logreg** | **0.513** | ≈ noise (SE≈0.024 → ~0.5σ above 0.5); < confidence |
| **+owner one-hot logreg** | **0.517** | identity adds ~0.004 — nothing |

**VERDICT — definitive negative.** A multi-feature, leakage-controlled, out-of-sample ranker reaches
only **AUC ≈ 0.51–0.52** — statistically indistinguishable from a coin flip and *no better than the
confidence feature alone*. On the candidate set these strategies produce, **the per-trade outcome is
essentially unpredictable from the available decision-time features**, so **no scorer beats dumb
symbol-priority** — which is exactly why both the intra-symbol test and the sizing-normalized A/B
showed EV ≈ priority. The lever for better results is **NOT** a smarter cross-candidate allocator/
scorer; it is (i) strategy-level quality — which strategies/cells to run (Tier-3, e.g. the 4h variants
clearly outperform), and (ii) the sizing/risk decision (shared-budget concentration = leverage, ~2.5×
maxDD).

**One untested input remains (honest scoping):** this rules out a ranker built from price-momentum /
vol / time / R:R / `c_strat` / cost-aware `ev_r`. It does **not** test the design's full conviction
stack — the **ML regime/model heads (`c_ml`) and per-cell historical expectancy** (§5.2), which aren't
in this offline feature set and ride the separately-gated ML-promotion track. If the allocator-scorer
idea is ever revisited, that `P_win` is the *only* remaining candidate input, and it should be proven
in **this same** sizing-normalized harness (`learned` arm vs `shared_priority`) **before** any routing
plumbing is built. Until such evidence exists, the M18 selector stays observe-only and **all Tier-3
decisions remain parked.**

