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

### Follow-up directions (2026-06-02, same session)

3. **Symbol universe: lead with futures diversification, but enumerate
   the FULL tradeable set.** Start WS-A on the futures/IBKR side (best
   BTC-uncorrelation) rather than Bybit alts. **Critical constraint:** the
   eventual live futures account will likely be **NinjaTrader**, not
   IBKR — so the universe must be scoped to what NinjaTrader can actually
   trade (verify the catalog), using IBKR only as the current paper-data
   source. Do not validate a symbol we cannot trade on the eventual venue.
4. **Augment as much as possible, replicating real conditions
   faithfully.** Maximize backtest-augmented training (WS-B), and make
   the sim/backtest model real trading conditions as faithfully as we
   can — fees, slippage, funding, partial fills, latency — so augmented
   data resembles live and backtests predict live.
5. **Do NOT add a stricter formal promotion gate.** Operator preference:
   don't slow promotions with new gating bureaucracy. WS-C invests in
   *evidence quality* (realism + diagnosis + drift observability), not a
   blocking gate. Better evidence is the substitute for more process.

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

- **A0 — Verify the tradeable universe (FIRST, per directive 3).**
  Enumerate the full set of symbols we can actually trade on the
  *eventual* venues, not just today's. The live futures account will
  likely be **NinjaTrader** (CME/CBOT/NYMEX/COMEX/ICE futures + FX via
  Rithmic/Continuum/Tradovate — *not* equities/crypto), so the futures
  universe is index futures (ES/MES, NQ/MNQ, YM/MYM, RTY/M2K),
  commodities (GC/MGC, CL/MCL, SI, HG), rates, and FX futures
  (6E/M6E, 6J). Cross-check NinjaTrader (likely live venue) against IBKR
  (current paper-data source) so we only validate symbols tradeable on
  the eventual venue. Bybit perps (ETH/SOL/majors) remain the
  same-keys frequency play.
- **Order (per directive 3): futures/diversification FIRST**, Bybit
  alts second.
- **Method:** trainer-VM sweeps via `scripts/backtest_{trend,fade,
  squeeze,ict_scalp,fvg_range,pullback}.py` on fresh history (the
  IBKR/yfinance adapters for futures; `fetch_backtest_candles.py` for
  Bybit). Net-of-fee, long/short split, walk-forward windows — reuse the
  existing harness flags. Re-tune per symbol (crypto params don't
  transfer — the decider doc found SPX needed its own tuning).
- **Deliverable:** a symbol×strategy generalization matrix (net-R, OOS
  hold, max-DD, trade frequency, fee robustness) under
  `docs/research/`, ranked by net-of-fee OOS edge, with each symbol
  tagged by which venue(s) can trade it.

### WS-B — Break the data wall (ML)

- **B1 — Backtest-augmented training, maximized (MB-20260530-001, per
  directive 4).** Tag WS-A per-trade backtest outcomes `source=backtest`,
  train trade-outcome / setup-quality models on backtest+live,
  **evaluate only on the live holdout.** Go as aggressive on volume as
  the live-holdout eval supports. **Realism is the constraint:** to make
  augmented data resemble live, the harness must model real conditions
  faithfully — net-of-fee (already in), plus slippage, funding (perps),
  partial fills, and entry/exit latency. The closer the sim is to live,
  the more the augmented labels transfer. Execution-quality (slippage)
  models stay live-only unless/until the sim models fills credibly.
- **B2 — OHLCV-only models (no data wall).** Train regime models on the
  WS-A symbols; add a regime-*transition* detector and a volatility
  forecaster (plentiful data, no n≈78 ceiling).
- **B3 — Fix regime shadow coverage (MB-20260529-001)** + the
  window-recency sweep (MB-20260601-001).

### WS-C — Evidence quality (NOT a gate, per directive 5)

Operator preference (directive 5): **no new blocking promotion gate.**
WS-C improves the *quality of evidence* so we promote confidently and
catch divergence early — it does not add process that slows promotions.

- **C1 — Diagnose fade/squeeze.** Why did +64R backtest become −86R
  live? (regime concentration, fee realism, fill assumptions,
  month-concentration). Write it up so the failure mode is named — input
  to the WS-B realism work, not a gate.
- **C2 — Sim realism (the substitute for a gate).** Make the
  backtest/sim model live conditions faithfully (slippage, funding,
  partial fills, latency on top of net-of-fee). Better-predicting
  backtests are how we avoid the next fade/squeeze without adding a gate.
  Shared work with WS-B1.
- **C3 — Backtest-vs-live drift monitor (observability, not a gate).**
  Every live strategy continuously reports how far its live
  win-rate/expectancy has drifted from its backtest expectation, and
  alerts *before* it bleeds. A tripwire the operator sees — it does not
  auto-block anything.
- **C4 — Synthetic-path stress (block bootstrap).** Resample returns
  into many synthetic-but-realistic paths to stress-test robustness
  beyond the single historical path. Also manufactures more training
  scenarios for WS-B.

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

- **Immediate (this session, Tier-3 — operator-approved 2026-06-02,
  committed, awaiting deploy):** real-money `bybit_2` roster set to
  **passed-backtest winners only** = `[trend_donchian, ict_scalp_5m,
  fvg_range_15m, htf_pullback_trend_2h]`. Graduated fvg_range +
  htf_pullback (risk_pct 0.3); dropped turtle_soup, vwap, fade, squeeze
  from real money (they stay on bybit_1 demo). Demo carries the full
  active roster. Deploys via `pull-and-deploy` on the operator's OK.
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

- **S0 — Plan + immediate roster change (this session).** This plan
  (Tier-1, committed). The real-money `bybit_2` winners-only roster as a
  Tier-3 commit (operator-approved, awaiting deploy). Define the WS-A
  universe + sweep spec.
- **S1 — WS-A0 + futures sweep (directive 3: futures FIRST).** Verify the
  NinjaTrader-tradeable futures catalog (cross-checked vs IBKR data);
  sweep all roster strategies across index futures + commodities + FX
  futures on the trainer VM; generalization matrix tagged by venue.
- **S2 — WS-B1 + WS-C2 sim realism (directive 4).** Add slippage /
  funding / partial-fill / latency modeling to the harness so augmented
  data resembles live; backtest-augmented training on S1 outputs,
  maximized, eval on live holdout.
- **S3 — WS-A Bybit alts + WS-B2.** ETH/SOL/majors sweep; OHLCV-only
  models (regime/vol) on the new symbols.
- **S4 — WS-C1 diagnosis + WS-C3 drift monitor** (observability) wired to
  the live roster.
- **S5 — WS-D decider sim + WS-C4 synthetic-path stress.**
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
