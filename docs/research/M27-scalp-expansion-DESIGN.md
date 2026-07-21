# M27 — Scalp Expansion (design + phased plan)

**Operator-directed 2026-07-20** (same session as the ict_scalp_5m Phase-0→4
arc, PR #7115): *"take the basis of what we worked on today and see if we can
expand that to lots of other strategies, pairs, symbols"* — test the existing
scalp strategy on every other instrument we already trade, expand across
timeframes, and hunt for additional scalp setups/tweaks — all under the
measurement discipline Phase 0 established.

**Anchor backlog id:** `PB-20260720-M27-SCALP-EXPANSION`
(performance-review backlog).

## Why this milestone exists

The 2026-07-20 arc proved two things worth generalizing:

1. **The measurement discipline finds real answers.** The −467R "structural"
   demotion baseline was unreproducible; the live record was netting
   misattribution; the true constraint was fee load (~0.20R/trade at scalp
   stop widths); and a regime OFF-cell gate validated k-fold OOS (+20–29R,
   3/4 folds) turned a marginal strategy back into a deployable one.
   That same pipeline — config-exact backtest → gross→net fee accounting →
   decision-time regime stamping → per-(trend,vol) cell table → k-fold OOS →
   Tier-3 packet — is strategy- and symbol-agnostic.
2. **The infrastructure now supports scalps under netting.** The cascade-close
   fix, the repair pathway, and venue-validated `BYBIT_TPSL_MODE=partial`
   (qty-scoped brackets) remove the incident class that poisoned the last
   scalp record, so new scalp legs accrue *trustworthy* live evidence.

## Reusable assets (built 2026-07-20, all on main)

- `scripts/backtest_ict_scalp.py` — live-exit-faithful harness:
  `--stamp-regime` (decision-time ADX trend + frozen-edge vol over the same
  200-bar window as `_stamp_regime_on_meta`), `--vol-spec-json`,
  `--sim-breakeven` (BE@1R + `be_offset_bps`); emits `mfe_r/mae_r/bars_held/
  exit_time/exit_price` per trade.
- `scripts/research/ict_scalp_phase0/` — `build_percell.py` (per-cell tables,
  price-based R), `kfold_oos.py` (anchored 4-fold walk-forward, per-fold rule
  selection on train / evaluation on test), `stamp_vol_post.py`.
- Fee-load accounting: 7.5 bps round-trip taker ≈ 0.20R/trade at ict_scalp's
  ~0.4%-of-price stops — the binding 5m constraint. Net-of-fee is the ONLY
  number that counts.
- The Phase-4 packet template
  (`ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md`) — the shape every
  Tier-3 proposal in this milestone reuses.
- Findings doc as the method record:
  `ict_scalp_5m-phase0-findings-2026-07-20.md`.

## Binding discipline (every phase, no exceptions)

- **Config-exact or it doesn't count** — the harness must match the live
  block's exits/gates before any number is trusted (the SOL-tuning and
  Phase-0 lessons).
- **Decision-time regime stamps only** — never resolution-time backfills;
  vol edges are frozen registry/artifact constants, committed with the run.
- **Fees always netted; R always from price geometry**, never journal pnl
  (the misattribution lesson).
- **k-fold OOS before any proposal** — a rule/param that wins in-sample only
  is excluded (the fitted-min_confidence rejection precedent).
- **Every live change is a Tier-3 packet** — per leg, operator-gated; new
  legs run the prop account-compat matrix per the standard flow.

## Phases

### The universe (coverage-first mandate, operator directive 2026-07-20)

**Every traded symbol gets a coverage row — not just crypto.** The roster is
enumerated **data-driven from config** (union of `accounts.yaml` per-account
`symbols` + `strategies.yaml` per-strategy symbols — never a hardcoded list),
which today yields **24 symbols across five venue families**:

| Family | Symbols | Data source | Cost model (the "net" in net-of-fee) |
|---|---|---|---|
| Crypto (Bybit linear) | BTCUSDT ✅(done), ETHUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT | Bybit klines, trainer-side pull (deep history) | taker bps round-trip (maker variant in P2) |
| Futures (IBKR) | MES, MGC, MHG | `pull-ibkr-history` operator actions (5m pulls exist for MES) | per-contract commission + tick value; RTH/ETH session handling; whole-contract sizing floors the risk granularity |
| Equities/ETFs (Alpaca + IBKR) | SPY, QQQ, IWM, TLT, GLD, SLV, GDX, USO, IEF, IAUM, SPLG, SCHA, TQQQ, QLD | Alpaca data API intraday bars (trainer-side) | ~zero commission; spread + market-hours-only sessions dominate |
| FX/metals (OANDA) | XAUUSD | Dukascopy candles, keyless (research proxy — actually used for the Batch-4 15m study, `docs/research/M27-P0-batch4-xauusd-findings-2026-07-21.md`; OANDA remains the intended **live** venue per P1 below, currently blocked, `BL-20260611-007`) | spread; 24/5 |
| Options underlyings | SLV, GDX (options expression on `alpaca_options_paper`) | tested as their underlying ETFs above | scalping the OPTIONS expression itself is **blocked-with-reason** (DTE-banded debit verticals don't map to 5m scalps) |

Standing blocked-with-reason rows (recorded, not silently dropped): the
**prop bridge** (breakout_1 ETH/SOL) at 5m — manual ticket latency is
incompatible with scalp fills; revisit at 15m+ only. Thin/levered ETF
duplicates (TQQQ/QLD/SCHA/IAUM/SPLG) test AFTER their base indices — a
levered wrapper only earns a cell if the base symbol passes.

**No silent caps:** a symbol×timeframe cell missing from the coverage table
is a bug in the milestone, not an allowed omission. The committed table lives
at `docs/research/artifacts/m27/coverage.md` and is updated every session.

### P0 — Full-universe coverage enumeration + cross-symbol transfer

Step 1 (session 1): commit the initialized coverage table for all 24 symbols.
Step 2: run the existing 5m logic, config-exact, in **data-availability
batches**: **Batch 1 crypto** (ETHUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT —
deepest history, same venue as the proven BTC run), **Batch 2 futures**
(MES/MGC/MHG via the IBKR history pulls, session-aware), **Batch 3
equities/ETFs** (base symbols first: SPY, QQQ, IWM, TLT, GLD, SLV, GDX, USO,
IEF), **Batch 4 XAUUSD** (folds into P1's 15m thread). Per symbol:
gross→net with the **venue-correct cost model** → per-(trend,vol) cell
table → k-fold OOS of the OFF-cell rule. **Gate per symbol:** net gated
expectancy > 0 with ≥3/4 folds positive.

Mechanics: intraday history is pulled **trainer-side** (the sandbox proxy
blocks exchange endpoints; the trainer's dataset builds already fetch Bybit
klines) — `scripts/research/m27/fetch_bybit_5m.py` is the Batch-1 puller.
Per-symbol 5m vol edges: reuse the symbol's registry regime-head spec where
one exists; otherwise derive frozen edges from the training window ONLY and
commit them in the run artifact.

### P1 — Timeframe sweep

Same scalp logic at **15m** (and 1m where data quality supports it) on BTC +
every P0 passer. Fold in the M15 Phase-0 finding that **ict_scalp 15m on
XAUUSD survives fees** (+39R train / +10R OOS): re-validate config-exact and
carry it toward an OANDA leg. Equities intraday (SPY) via Alpaca is the
second non-crypto candidate. Fee math changes with timeframe (wider stops →
smaller fee load in R) — recompute per cell, never assume.

### P2 — Geometry & cost tweaks (carries the existing fee-load thread)

The 5m fee load is the binding constraint, so sweep the levers that change
the math: stop width / R:R grids, BE trigger/offset, M20 exit levers applied
to scalp legs, and a **maker-entry (limit) variant** study (halving the
round-trip cost roughly halves the 0.20R load). All k-fold-gated; tuning
lands as alt-variant configs, never silent edits to the live block.

### P3 — New scalp archetypes

Candidate generation on the same harness: opening-range breakout,
LTF VWAP-reversion, sweep-reversal variants, killzone-scoped variants of the
existing FVG+sweep logic. Each candidate gets the identical pipeline; most
should die cheaply in P3 backtests — that is the point of the discipline.

### P4 — Promotion path (per passing leg)

Each passing (symbol × timeframe × variant) leg ships as a **new strategy
entry** (alt-variant naming, e.g. `ict_scalp_eth_5m`) via the `new-strategy`
skill: shadow soak first, regime cells authored from its own k-fold evidence,
prop account-compat matrix, M20 exit-refinement processing (every new leg),
then the Tier-3 operator gate. No blanket promotions — one packet per leg.

## Non-goals

- No changes to the live ict_scalp_5m BTC leg in this milestone (it has its
  own gate + first-fire watch from the 2026-07-20 arc).
- No new order-path code — P4 uses the standard strategy wiring; the partial
  tpsl / cascade-close infrastructure is already live.
- Not an entry-refinement program for the non-scalp families (that's M21).

## Done-condition

A committed coverage table (symbol × timeframe × variant) where every cell is
either **promoted** (live/shadow with its packet linked), **rejected** (with
the net k-fold numbers that killed it), or **blocked** (with the reason,
e.g. no data) — plus at least the P0 crypto batch, the futures batch, the base-ETF batch,
and the P1 BTC-15m/XAU-15m cells resolved. The table covers ALL 24 traded
symbols — a missing row is a milestone bug.
