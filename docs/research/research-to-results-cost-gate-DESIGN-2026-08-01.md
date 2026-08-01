# R4 — the research→results cost-gate: design + build plan (scope-and-start)

**Owns:** the highest-leverage frontier fix from `S-ROADMAP-STATUS-REVIEW-2026-08-01`
§R4 — *"no strategy graduates unless its portfolio-mirror is net-positive after full
costs."* Diagnosis of record: [`research-to-results-gap-2026-07-30.md`](./research-to-results-gap-2026-07-30.md).

**Status:** scoping doc (Tier-1, docs-only). The gate itself is a **Tier-3**
promotion-methodology change — operator-gated, built observe-first. This session
scopes it and flags the one load-bearing contradiction that must be settled before
any code enforces.

---

## 1. The objective, restated

The validation layer green-lights strategies the honest live-execution layer then
loses money on (every live crypto leg is red at every scale; the diagnosis doc has
the four compounding root causes: **incomplete cost model**, execution divergence,
offline over-selection, regime). R4 is process-change **#1** of that doc: promote
the **paper-portfolio mirror** — same signals, same execution path, realistic size —
to a **hard graduation/retention gate**, so a leg keeps real-money routing only while
its mirror is net-positive net-of-cost. Process-change #2 (complete the crypto cost
model) is the parallel build, tracked in §5.

## 2. THE CONTRADICTION THAT MUST BE SETTLED FIRST (say-something)

The 07-30 diagnosis says *"we already have the honest predictor and don't gate on
it"* — pointing at `bybit_portfolio` reading **−$12,597/30d** as the net-of-everything
answer. **But the 2026-08-01 roadmap review's binding data-trust matrix says the
opposite about that number's trustworthiness:**

> All demo/paper per-row PnL — **fabricated at scale**. The `demo` block reads 7d
> −$17,343 / lifetime −$60,176 — a poisoned book. bybit_1 47% / **bybit_portfolio
> 92% fabricated**. **Binding directive: do not tune any strategy/exit/promotion
> gate on paper PnL until the measured/fabricated split is surfaced.**

So the naive R4 gate — "block promotion when `paperPortfolio.totalPnl < 0`" — would
be **tuning a promotion gate on a 92%-fabricated number**, the exact move the review
forbids. The −$12,597 is dominated by mark-price fabrication, not measured
execution loss. **A gate built on it inherits the poison.**

**This is not a reason to abandon R4 — it is the design constraint.** The provenance
overhaul (the week's headline: `src/runtime/provenance.py` + `pnlProvenance` on
`/trades/closed` + `pnlCoverage`/`pnlMeasuredCount` on `/performance`, incl. the
`paperPortfolio` sub-block — verified in `performance.py:439-471`) is *exactly* the
"surface the measured/fabricated split" the directive names as the precondition. The
gate must read the **measured** subset, not the raw sum.

## 3. Corrected gate design — measured-provenance-aware

The gate reads `GET /api/bot/performance?window=…` (the mirror already surfaces
everything needed; no new endpoint):

```
paperPortfolio: { totalPnl, expectancy, pnlCoverage, pnlMeasuredCount,
                  pnlFabricatedCount, perStrategy:[{name, trades, totalPnl, …}], … }
```

Rule (per strategy, rolling window):

1. **Coverage floor first.** If `pnlCoverage < COVERAGE_FLOOR` (start ~0.6, the live
   real-money 7d figure) the mirror verdict is **`unverified` → the gate ABSTAINS**
   (neither pass nor fail). An unmeasured mirror is not evidence of anything — it is
   the "poisoned book" state, and abstaining is the honest disposition. This is the
   direct application of the `rCoverage`/`pnlCoverage` discipline: *transparency,
   never a raw-pnl fallback.*
2. **Net-positive on the measured subset.** When coverage clears the floor, the pass
   condition is the **measured** net-of-cost expectancy ≥ 0 over the window — i.e.
   recompute the mirror PnL over `pnlProvenance ∈ {measured, estimated}` rows only
   (fabricated/unverified excluded), not `totalPnl`. (Producer note: `/performance`
   today returns the *counts* per bucket but sums `totalPnl` over all rows; the gate
   needs a **measured-only PnL sum** — see §4, the one small producer add.)
3. **Real-money override / fallback.** `bybit_2` exchange-fills (the control account,
   ~2% fabricated, 90% measured coverage) is the trustworthy real-money read; where a
   leg has enough real-money closed volume, that is authoritative over the mirror. The
   mirror is the *early-warning breadth* instrument (realistic size, more rows); the
   exchange-fills store is the *ground truth* (few rows). The gate uses real-money
   when n is adequate, the measured mirror when it is not, and abstains when neither is.

**Never gate on `totalPnl`. Never gate below the coverage floor.** Both are the
same error the diagnosis and the review each warn about from opposite directions.

## 4. Wiring points (where the gate attaches)

- **Real-money graduation gate:** `scripts/prop/account_compat_matrix.py` — today
  gates standard accounts on *"net-of-fee performance"* (`run_montecarlo` end-return +
  survival + P(breach)). Add the mirror precondition as a **new required pass** in the
  standard-account branch: a leg cannot be reported `compatible` for a real-money
  account unless its `*_portfolio` mirror clears §3 (or abstains with an explicit
  `mirror: unverified` flag the operator sees).
- **M7 strategy-review gate:** `scripts/ml/strategy_review_packet.py` — surface the
  mirror verdict in the packet (`KILL`/`DEMOTE_SHADOW`/`TUNE`/`HOLD`/`PROMOTE` matrix)
  so a review session sees `mirror_measured_net` alongside the live closed-trade
  stats. Read-only in the packet; the Tier-3 action is proposed, never enacted here.
- **Producer add (Tier-1, small):** `/performance` (`performance.py`) — add
  `totalPnlMeasured` (sum over `measured`+`estimated` rows) beside `totalPnl`, to
  every block incl. `paperPortfolio`, so the gate reads a measured sum instead of
  re-deriving it consumer-side. This is the *only* code the gate strictly needs before
  it can compute an honest verdict; it is safe, additive, observe-only.

## 5. Parallel track — complete the cost model (process-change #2)

The mirror gate answers *"is the realistic-size, real-execution result net-positive?"*
The cost-model completion answers *"why not, and would a fix survive?"* — they compose.

- Port funding from the market-neutral harnesses (`backtest_pairs` /
  `backtest_funding_carry`) into `backtest_trend` / `backtest_pullback` /
  `backtest_ict_scalp`; add a slippage term (tight-stop scalps are slippage-dominated).
- Re-run `account_compat_matrix` **net of fee + funding + slippage** for every live
  crypto leg.
- **Prereq (blocking calibration):** close the M24 funding-visibility gap — `bybit_2`
  has 0 funding records, so the funding model would be calibrated to a guess. The
  `pull-exchange-funding` system-action exists; it must run and populate before the
  funding term is trusted. Until then the cost model is *structure without calibration*.

## 6. Build plan (phased, observe-first)

- **P0 (Tier-1, now-ish):** the `totalPnlMeasured` producer add (§4) + a standalone
  **shadow gate reporter** — a script that computes the §3 verdict for every live leg
  and writes an observe-only report (`would-block` / `pass` / `abstain-unverified`),
  wired into the review packet. Nothing is enforced; it accrues the evidence that the
  gate would have made the right call, the same discipline every other soak follows.
- **P1 (Tier-1 build, gated calibration):** the funding+slippage cost terms + the
  M24 funding pull, then the full-cost `account_compat_matrix` re-run.
- **P2 (Tier-3, operator-gated):** flip the mirror precondition + full-cost gate to
  **enforcing** in `account_compat_matrix`. This is the promotion-methodology change;
  it demotes legs and must not self-merge.

## 7. Binding constraints carried from the review

- **Do not tune on paper PnL** — §2/§3 is the whole point; the gate reads measured
  provenance, abstains below the coverage floor.
- **Do not cite** `exit-capture-deepdive-2026-07-30.md`'s root cause (its
  `BYBIT_TPSL_MODE` premise is wrong). Its MFE/giveback metrics are fine; unused here.
- **A red number is a trigger to diagnose, not demote** (drift-remediation): the gate
  *proposes* demotion evidence; the per-leg 2-yr walk-forward diagnosis (the
  `eth_pullback_2h` lens) decides keep-and-regime-tune vs cost-fix vs demote. The gate
  is the **filter that flags**, not the executioner.

## 8. Operator decision needed

P2 (enforce) is Tier-3 and rests on §2 being accepted: **the mirror gate reads the
measured-provenance subset, not raw paper PnL, and abstains below a coverage floor.**
If the measured subset of `bybit_portfolio` turns out too thin to gate on (92%
fabricated ⇒ ~8% measured rows), the mirror becomes an *abstain-mostly* instrument and
the real-money `bybit_2` exchange-fills path (§3.3) carries the gate — which is fine,
just slower to accrue. Either way the number the gate trusts is a **measurement**, and
that is the fix.
