# Why the research isn't translating into live results — diagnosis + process fix

**Operator question (2026-07-30):** "We do so much testing and research. We need
to understand why that's not translating into results and what we need to be
doing differently." Focus: **crypto first**, then equities/ETFs.

## The evidence in one line

Every live crypto strategy is red at every scale — real `bybit_2` −$30/14d, the
$87k paper mirror `bybit_portfolio` **−$12,597/30d (all 5 strategies negative)**,
soak `bybit_1` −$25k/30d — yet each of those strategies passed a backtest/gate to
go live. **The validation layer keeps green-lighting strategies the honest
live-execution layer then loses money on.** The answer is *why the gate says yes
when reality says no*, and it's four compounding causes.

## Root cause #1 — the cost model is incomplete exactly where the book lives (the big one)

- `scripts/backtest_trend.py` / `backtest_pullback.py`: model **fees only** (a flat
  `FEE_BPS_ROUNDTRIP = 7.5`) — **no funding, no slippage.**
- `scripts/backtest_ict_scalp.py`: explicitly **"assumes intra-bar SL/TP fills at
  the level (no slippage)."**
- The real-money graduation gate, `scripts/prop/account_compat_matrix.py`, gates
  standard accounts on **"net-of-fee performance"** — fees only.
- Funding is modeled **only** in the market-neutral harnesses (`backtest_pairs`,
  `backtest_funding_carry`, `backtest_xsec_momentum`) and `src/prop/montecarlo.py`.

The live directional crypto strategies run on a **perpetual-futures** account
(`bybit_2`), where **funding accrues continuously on every held position** and
**tight-stop scalps are slippage-dominated** (a 5m scalp risking ~0.3×ATR gives
back a large fraction of its edge to a few bps of slippage per side). The
backtests that green-lit them model **neither** cost. So a backtest that reads
"+edge net of fee" is routinely ≤0 net of the **full** live cost stack. This is
already documented as a known caveat in the ml-review backlog — it was just never
promoted into a gate.

## Root cause #2 — execution divergence (proven, one already fixed)

Live execution differed from the idealized backtest. The `BYBIT_TPSL_MODE=full`
shared-bracket bug (`BL-20260720`) made scalps ride **6–14 hours** instead of
their clean TP/SL — the backtest assumes a per-trade bracket that live didn't
deliver. Fixed 2026-07-30 (partial mode). Other divergences to expect: partial
fills, SL slippage on market exits, funding on overnight holds — none in the
harness.

## Root cause #3 — offline over-selection (the lesson you already learned on ML)

The ML review's own finding: the build funnel "must gate on **RG4 (live-row
parity + net-of-cost edge), not RG3 AUC**" — an offline-0.89-AUC head went
**0.32 anti-predictive** live. Running ~36–52 strategies + selecting on
backtest/offline metrics is the strategy-level version of the same disease:
with enough candidates, the ones that *pass* are disproportionately the ones that
were *lucky/overfit*. The discipline exists for models; it isn't applied to
strategy graduation.

## Root cause #4 — regime

The crypto legs were validated over multi-year windows that include strong trends;
the recent live tape is chop (ADX mostly 15–25 in the audit sample). Trend and
pullback strategies structurally bleed in chop. Backtests that don't
regime-condition their reported edge overstate what the current regime delivers.

## The punchline: we already have the honest predictor, and we don't gate on it

`bybit_portfolio` is the paper mirror of the real book — **same signals, same
execution path, realistic ($87k) size**. It is **−$12,597**. That is the
net-of-everything, real-execution answer, and it is screaming. **If real-money
graduation required the paper mirror to be net-positive net-of-cost, none of the
current crypto strategies would qualify.** The signal isn't missing — the gate
just doesn't read it.

## What to do differently (process changes)

1. **Promote the paper-portfolio mirror to a hard graduation gate.** A strategy
   earns (or keeps) real-money routing only while its `*_portfolio` mirror is
   net-positive net-of-cost over a rolling window. This is the strategy-level RG4.
   It uses infrastructure we already have.
2. **Complete the crypto cost model + re-gate.** Port the funding model from the
   pairs/carry harnesses into `backtest_trend`/`backtest_pullback`/`backtest_ict_scalp`,
   add a slippage model, and re-run the account-compat gate **net of fees +
   funding + slippage** for every live crypto leg. Demote the ones that don't
   survive. (Prereq: close the M24 funding-visibility gap — `bybit_2` has 0
   funding records — so the model is calibrated to real paid funding, not a
   guess.)
3. **Cut the roster to what survives (2) and (1).** Fewer, higher-conviction,
   regime-gated legs beat a wide book selected on cost-blind backtests.
4. **Regime-condition the reported edge** — a strategy's gate result should be
   reported per-regime, and the live router should keep it off in the regimes
   where it has no net-of-cost edge (the regime-policy machinery exists).

## Concrete first steps (crypto)

- **Now (Tier-3, operator):** demote `eth_pullback_2h` — the top real-money
  bleeder (−$24.65 real, −$5,601 mirror) — to shadow while the re-gate runs.
- **Build:** the funding + slippage cost terms in the directional crypto
  harnesses; re-run `account_compat_matrix` net-of-full-cost for the crypto legs.
- **Adopt:** the paper-mirror-net-positive gate as the standing promotion/retention
  rule (this is the durable methodology fix).

Then repeat the same lens for the equities/ETF book (where the mirror is
+$3,953 but carried entirely by one unverified `uso_trend_1h` run — the *inverse*
risk: a single lucky strategy masking a losing roster).
