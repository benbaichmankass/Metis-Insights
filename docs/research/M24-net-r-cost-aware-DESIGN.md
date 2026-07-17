# M24 — Net-R / Cost-Aware Modeling

> **Status:** 📋 PROPOSED 2026-07-17 (design of record). **PROPOSE-ONLY** — no
> live-path change from this doc. The offline label pipeline + re-grader (P1/P2)
> are Tier-1; anything that lets a cost-aware score *route or size* a live order is
> Tier-3, backtest-A/B-gated + operator-approved.
> Anchors: `MB-20260629-ALLOC-COSTCAP` (cost capture — **delivered** by Slice B,
> PR #6780, 2026-07-17), `MB-20260629-ALLOC-CORR` (correlation feature),
> `MB-20260530-001` (per-trade label rows). Reopens the tracks the T1.3 ranker
> (`docs/research/T1.3-ranker-findings-2026-07-16.md`) parked *pending clean cost labels*.

## Why now

Every performance/allocation model to date has evaluated on **raw pnl or
risk-model R**, never true **net-of-cost R**. Two backtests were explicit that this
was the binding gap:

- **M18 allocator** (`M18-allocator-backtest-findings-2026-06-29.md`): "fees/funding
  are backtest-only, **not logged per live trade** … must land in P0 before the P3
  ranker is trained."
- **T1.3 ranker** (`T1.3-ranker-findings-2026-07-16.md`): closed honest-negative,
  "Reopen only with a within-tick contrastive target **+ clean cost labels
  (`MB-20260629-ALLOC-COSTCAP`)** + a net-positive opportunity set."

**Slice B (PR #6780, merged 2026-07-17) just delivered the clean cost labels.** The
close path now carries broker-truth per-trade **fees** (`fee_taker_usd` /
`fee_maker_usd`, FIFO-attributed from the exchange-fills store via the indexed
`broker_order_id` join) and **funding** (`funding_paid_usd`, from the
`exchange_funding` store), with `cost_source='broker'` for cleanly-attributable
trades and `'estimate'` (fixed-model, Slice A) elsewhere. So a **true net-R label**
is now constructible for the first time.

## The label

For each resolved closed trade:

```
net_pnl_usd = gross_pnl_usd − fee_taker_usd − fee_maker_usd − funding_paid_usd     (signs per Slice B: cost > 0)
net_R       = net_pnl_usd / risk_usd_at_entry                                       (risk from SL distance × qty × contract_value)
cost_source ∈ {broker, estimate}   → carry as a label-quality flag, weight broker rows higher
```

`risk_usd_at_entry` is the same denominator the `/performance` R-metrics already
use (null when SL/qty/contract_value unknown → row excluded from R, never a
raw-pnl fallback — the existing honest-coverage rule).

## Phased plan

| Phase | Scope | Tier | Gate |
|---|---|---|---|
| **P1 — Net-R label pipeline** | A pure module that joins the Slice-B cost columns to each closed trade and emits `net_R` + `cost_source` + `risk_usd`. Coverage report: how many trades are `broker`-costed vs `estimate` vs uncosted, by cell. Unit-tested on fixtures. | T1 | Label computes; coverage report matches the on-VM dry-run numbers; broker/estimate split sane. |
| **P2 — Net-R re-grade of strategies/exits** | Recompute the M7 review-gate aggregates + the exit-refinement matrix on `net_R` instead of estimate-R. Answer: which strategies/legs are net-positive *after real costs*, and does any flip sign vs the estimate? (Especially the thin-edge crypto perps where funding is non-trivial.) | T1 | A committed net-R scorecard; any sign-flip vs estimate flagged for a Tier-3 review (does not itself change config). |
| **P3 — Cost-aware EV scorer refresh** | Feed the real per-cell net-R distribution into `allocator_ev.py::candidate_ev_score` (replace the fixed cost model with the measured one) + add the decision-time **correlation/covariance** feature (`MB-20260629-ALLOC-CORR`). Re-run the sizing-normalized allocator A/B — does the EV scorer's *selection* beat dumb priority now that costs are real? | T3 (soak/backtest) | The sizing-normalized cross-symbol A/B shows `ev − priority > 0` net-of-real-cost (the exact test T1.3 failed). Still observe-only. |
| **P4 — Within-tick contrastive ranker (the other T1.3 unlock)** | The second thing T1.3 needed: a **within-tick contrastive target** (rank the candidates *present on the same tick* by realized net-R, not a global base rate) trained on the clean net-R label. LightGBM ranker on the M18 candidate→shadow→advisory ladder. | T3 | Ranker beats rules-EV in the sizing-normalized harness on net-R; per-fold robustness; then M18 P2 (allocator selects) can un-park. |

## What this unblocks

- **M18 P2/P3** (the allocator actually *selecting* a capital-constrained subset)
  were parked on exactly (clean cost labels + a proven within-tick P_win). P3/P4
  here are the two missing inputs. M24 is the M18 unblocker, not a replacement.
- **M23 P3** trains take/skip heads against a target — `net_R` is that target.
- **Prop EV / survival** (`run_ev_montecarlo`) currently uses a modeled cost; P2's
  measured per-cell net-R tightens the prop compatibility matrix.

## Honest risks / non-goals

- **Broker-truth coverage is partial.** Slice B only cleanly attributes
  non-netted, USD-fee Bybit trades; IBKR/Alpaca and netted crypto keep the
  estimate. P1's coverage report is the honest denominator — do **not** train a head
  on a cell that's mostly `estimate` and call it "broker-truth net-R."
- **This is modeling, not a live change.** Nothing in P1/P2 touches routing/sizing.
  P3/P4 stay observe-only until a sizing-normalized A/B passes and the operator
  approves — same gate that (correctly) parked M18.
- **Don't re-litigate T1.3's negative on stale evidence.** The ranker failed for a
  reason (no within-tick separation). M24 only reopens it *with the two specific
  new inputs*; if P3/P4 also fail net-of-real-cost, that's a real second negative —
  record it and re-park.
