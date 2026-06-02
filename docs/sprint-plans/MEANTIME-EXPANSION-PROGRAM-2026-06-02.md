# Meantime Expansion Program — Milestone Plan (2026-06-02)

> **Status:** Active program plan. Created this session (strategy + ML
> review).
> **Authority order:** `docs/CLAUDE-RULES-CANONICAL.md` →
> `docs/ARCHITECTURE-CANONICAL.md` → `ROADMAP.md` → this plan →
> the active sprint log under `docs/sprint-logs/`.
> **Maps onto existing roadmap:** M7/M8 (Strategy Improvement Program),
> M9/M10 (AI-traders workstreams). This plan does not fork the roadmap;
> it is the execution detail for the "what do we do while we wait for
> live data" question, run in parallel with live accumulation.
> **Companion plans:** `STRATEGY-IMPROVEMENT-PROGRAM-2026-05-23.md`,
> `DECIDER-SINGLE-ACCOUNT-2026-05-24.md`.

---

## Mission

The system is live but **data-starved**: live trades accrue ~2/week
(n≈78), so the trade-outcome ML families and the live-vs-backtest
verdicts that gate promotion are years away from significance at the
current rate. Rather than wait idle, this program attacks the three
bottlenecks we **can** move today, without a single new live trade:

1. **Concentration** — real-money PnL rides on ~1 net-positive strategy
   on 1 symbol (BTCUSDT). Expand the live roster and the symbol universe.
2. **The data wall** — break n≈78 with backtest-augmented training and
   with the no-risk demo/paper accounts as a live-simulation data engine.
3. **Backtest→live generalization failure** — fade/squeeze passed
   backtest (+64R) then bled live (−86R / 0% WR). Fix the *validation
   methodology* that let them through, so the next promotion is safer.

The North Star is unchanged: **hit the monthly PnL target.** More
*proven* strategies, on more *uncorrelated* symbols, promoted through a
*stricter* gate — that is how we grow PnL while the live evidence
matures.

---

## Operator directives (2026-06-02, this session)

1. **Graduate backtest-passing strategies to live Bybit (real money).**
   "We won't get anywhere running only one strategy." The two validated
   demo-only members (`fvg_range_15m`, `htf_pullback_trend_2h`) graduate
   to `bybit_2` real money — the same Tier-3 path `trend_donchian` took.
   (Excludes fade/squeeze: they *failed* live, so they are not
   backtest-passers in the sense that matters — see WS-C.)
2. **Use paper/demo accounts as the no-risk data engine.** Paper/demo
   accounts exist for full-roster, no-risk live simulation that generates
   performance data fast. This is now the standing promotion ladder: a
   new strategy executes on demo/paper *first* (real fills, no risk),
   then graduates to real money once demo + backtest agree.

---

## Current state (verified 2026-06-02)

### What is live (verified from config/strategies.yaml + accounts.yaml)

| Account | Money | Executing LIVE | Data-only (`execution: shadow`) |
|---|---|---|---|
| `bybit_2` | **real** | trend_donchian, turtle_soup, ict_scalp_5m | fade_breakout_4h, squeeze_breakout_4h, vwap |
| `bybit_1` | demo (paper) | trend, turtle, ict_scalp, **fvg_range_15m**, **htf_pullback_trend_2h** | fade, squeeze, vwap |
| `ib_paper` | paper | mes_trend_long_1d (+ roster) | — |
| `ib_live` | real | — (dry_run, inert) | — |
| `prop_velotrade_1` | — | — (scaffold, unwired) | — |

- **Net-positive on real money:** `trend_donchian` (the flagship,
  +52.5R/3yr 2h validated, re-tuned to 1h/trail-5.0 2026-06-01).
- **Net-negative / unproven on real money:** `turtle_soup` (net-negative
  standalone), `ict_scalp_5m` (≈breakeven), `fade`/`squeeze` (demoted to
  shadow 2026-06-01 after real-money loss), `vwap` (no net-of-fee edge,
  data-only).
- **Validated, demo-only (the graduation candidates):** `fvg_range_15m`
  (5.2y +24.4R, OOS *stronger* +21.8R — no overfit decay),
  `htf_pullback_trend_2h` (IS +32.7R / OOS +22.4R, 3-fold robust).

### The ML data picture

- Live trades n≈78, ~2/week. `setup-quality-lgbm-v2` demoted to
  `research_only` (needs ~1000 to beat baseline → ~9yr at this rate).
- Regime models (OHLCV-only, plentiful data) are healthy; v2-LightGBM
  regime models are in `shadow` soak. Coverage gap: 15m/1h/MES regime
  models emit zero shadow predictions (fire only on 5m signals).
- Open ML-backlog items this program advances: MB-20260530-001
  (backtest-augmented training), MB-20260529-001 (regime shadow
  coverage), MB-20260601-001 (regime window-recency sweep).

---

## Where the work runs (web-session reality)

This program is driven from Claude Code on the web. Egress to the VMs and
to exchange APIs is firewalled at the default **Trusted** network level,
so:

| Work | Where it runs | How Claude drives it |
|---|---|---|
| Data pulls (Bybit/IBKR history), sweeps, training, dataset builds | **Trainer VM** (autonomous territory) | `trainer-vm-diag` issue relay (`cmd:` block) |
| Live runtime reads (roster, journal, drift) | **Live VM** | `vm-diag-snapshot` relay (read-only) |
| Plan docs, harness/code, tests, analysis artifacts | **Repo** | branch + PR (this session) |
| Tier-3 config (roster, risk, promotion) | **Repo → live VM** | draft PR → operator approval → `pull-and-deploy` |

All sweep/training/analysis work below is **Tier-1 / autonomous** on the
trainer VM. Only the roster/risk/promotion *config* changes are Tier-3.

---

## Decision tiers (how every change is gated)

- **Tier 1 (autonomous):** backtests, sweeps, dataset builds, model
  training (up to `live_approved` in the registry, never past `shadow`
  influence), analysis, the new harness/monitors below, docs, tests.
  **Most of this program is Tier 1.**
- **Tier 2 (one operator OK):** deploy/restart, DB writebacks, new
  runtime timers/services.
- **Tier 3 (explicit operator approval):** ANY change to
  `config/strategies.yaml` params/execution, `config/accounts.yaml`
  routing/risk caps/mode, sizing, signal logic, or live promotion past
  shadow. Ships as a draft PR + approval request; never auto-merged.

---

## Workstreams

### WS-A — Wide symbol sweep (the data factory)

Run every roster strategy across a wide symbol universe to find where
each edge generalizes — answering "what can we add for more trades /
diversification" AND manufacturing the per-trade backtest outcomes WS-B
needs.

- **Universe (execution-path-constrained — only test what we can trade):**
  - **Bybit linear perps (cheapest expansion — same keys, same code):**
    ETHUSDT, SOLUSDT, plus a basket of liquid majors (e.g. BNB, XRP,
    DOGE, ADA, LINK, AVAX). These can be added live with a config-only
    change once validated.
  - **IBKR micro futures (best diversification — near-zero BTC corr):**
    MES (have), MNQ, MGC, MCL, M2K. Real-money IBKR is Tier-3-gated;
    validate now for when `ib_live` opens.
- **Method:** trainer-VM sweeps via `scripts/backtest_{trend,fade,
  squeeze,ict_scalp,fvg_range,pullback}.py` on fresh history
  (`scripts/ops/fetch_backtest_candles.py --symbol <X>` for Bybit; the
  IBKR/yfinance adapters for futures). Net-of-fee, long/short split,
  walk-forward windows — reuse the existing harness flags.
- **Deliverable:** a symbol×strategy generalization matrix (net-R, OOS
  hold, max-DD, trade frequency, fee robustness) under
  `docs/research/`, ranked by net-of-fee OOS edge.

### WS-B — Break the data wall (ML)

- **B1 — Backtest-augmented training (MB-20260530-001).** Tag WS-A
  per-trade backtest outcomes `source=backtest`, train trade-outcome /
  setup-quality models on backtest+live, **evaluate only on the live
  holdout.** Valid for outcome/setup-quality families; **NOT** for
  execution-quality (idealized fills) — keep those live-only.
- **B2 — OHLCV-only models (no data wall).** Train regime models on the
  WS-A symbols; add a regime-*transition* detector and a volatility
  forecaster (plentiful data, no n≈78 ceiling).
- **B3 — Fix regime shadow coverage (MB-20260529-001)** + the
  window-recency sweep (MB-20260601-001).

### WS-C — Honest validation framework (stop the bleed)

- **C1 — Diagnose fade/squeeze.** Why did +64R backtest become −86R
  live? (regime concentration, fee realism, fill assumptions,
  month-concentration). Write it up so the failure mode is named.
- **C2 — Stricter promotion gate.** Purged/embargoed walk-forward,
  combinatorial-CV robustness, realistic fee+slippage, regime-stratified
  OOS, both-leg-positive at n≥3 windows. Make this the *gate*, not just
  a one-off check.
- **C3 — Backtest-vs-live drift monitor (new tripwire).** Every live
  strategy continuously reports how far its live win-rate/expectancy has
  drifted from its backtest expectation, and alerts *before* it bleeds.
  Defensive; permanently de-risks every future promotion.
- **C4 — Synthetic-path stress (block bootstrap).** Resample returns
  into many synthetic-but-realistic paths to stress-test robustness
  beyond the single historical path (the exact gap that let fade/squeeze
  through). Also manufactures more training scenarios for WS-B.

### WS-D — Decider / regime-router research

Build & backtest the designed-but-unbuilt selection layer
(`DECIDER-SINGLE-ACCOUNT-2026-05-24.md`): one account, all strategies,
a smart aggregator that picks the highest-P(profit) signal each tick
instead of letting trend hog the book. Consumes the WS-A per-symbol
curves. Could rescue net-negative standalone members (turtle, ict_scalp)
via selection. Research-now, deploy-once-≥2-members-are-live.

### WS-E — Roster & promotion-ladder expansion (the directives)

The standing pipeline that operationalizes the two directives:

```
backtest-pass (WS-A/C gate)  →  demo/paper EXECUTE (no risk, real fills)
        →  demo+backtest agree  →  graduate to real-money bybit_2 (Tier-3)
```

- **Immediate (this session, Tier-3 — awaiting operator approval):**
  graduate `fvg_range_15m` + `htf_pullback_trend_2h` to `bybit_2` real
  money (add to `accounts.yaml::bybit_2.strategies`, keep risk_pct 0.3).
- **Standing:** every WS-A symbol/strategy winner enters demo-execute
  first, then graduates on agreement. New Bybit symbols are a
  config-only routing add once validated.

### Fresh idea (parked, to scope later)

- **Funding-rate harvest on perps** — a non-directional return stream
  (collect funding), uncorrelated to all trend/reversion strategies.
  Different trade type entirely; serves "more trades → monthly target"
  without adding directional risk. Scope after WS-A/B land.

---

## Sprint roadmap

Sprint ids: `S-MEANTIME-S0` … . Each produces a sprint log.

- **S0 — Plan + immediate roster graduation (this session).** This plan
  (Tier-1, committed). The `fvg_range`/`htf_pullback` → bybit_2
  graduation as a Tier-3 draft PR + approval request. Define the exact
  WS-A symbol list + sweep spec ready to dispatch.
- **S1 — WS-A Bybit sweep.** ETH/SOL + majors across all roster
  strategies on the trainer VM; generalization matrix.
- **S2 — WS-C1+C2.** fade/squeeze post-mortem + stricter gate spec, then
  apply the gate to the S1 winners.
- **S3 — WS-B1+B2.** Backtest-augmented training on S1 outputs +
  OHLCV-only models on the new symbols.
- **S4 — WS-C3 drift monitor** wired to the live roster.
- **S5 — WS-A IBKR micros + WS-D decider sim.**
- **S6 — Package validated winners** into Tier-3 promotion PRs (demo
  first, then real money).

---

## Safety constraints (non-negotiable)

- Never change strategy logic, params, execution gates, account routing,
  risk caps, sizing, or promote past shadow without explicit Tier-3
  operator approval. Draft PR + approval request always.
- Never write `accounts.yaml::mode:` outside `set-account-mode`.
- Backtest before proposing; require net-of-fee, both-leg-positive,
  OOS-holding evidence (the WS-C gate) before any real-money promotion.
- Demo/paper graduation is no-risk and lower-bar; real-money graduation
  requires demo + backtest agreement.
- One variable per Tier-3 PR so attribution stays clean.
- Trainer VM is autonomous; never SSH the live VM from a web session.
```
