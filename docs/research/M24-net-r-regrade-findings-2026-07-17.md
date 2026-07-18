# M24 P1/P2 — Net-R re-grade findings (2026-07-17)

> **Status:** M24 **P1 (net-R label pipeline) + P2 (net-R re-grade scorecard) DONE.**
> P3 (cost-aware EV scorer refresh) + P4 (within-tick contrastive net-R ranker)
> are **Tier-3** (order-routing/sizing-affecting) — proposed below, operator-gated.
> Design of record: [`M24-net-r-cost-aware-DESIGN.md`](./M24-net-r-cost-aware-DESIGN.md).
> Anchors: `MB-20260629-ALLOC-COSTCAP` (Slice B cost capture), `MB-20260717-M24-FUNDING-VISIBILITY` (the funding-coverage gap).

## What ran

The full P1→P2 chain against the **live** `trade_journal.db` (read-only, `mode=ro`):

1. **P1 net-R label pipeline** (`src/runtime/net_r_label.py`, #6806) — `net_R = (gross_pnl − fee_taker − fee_maker − funding) / risk_usd`, `risk_usd = |entry − stop| × qty × contract_value` (the `/performance` R denominator; null → row excluded from R, never a raw-pnl fallback). `cost_source ∈ {broker, estimate}` rides along as a label-quality flag.
2. **P2 net-R re-grade scorecard** (`scripts/research/net_r_regrade.py`, #6808) — per-strategy / per-`(strategy,symbol)` Σgross_R vs Σnet_R + cost-drag + the **sign-flip flag** (a cell gross-positive but net-negative after real costs).
3. Run wire: the new Tier-1 read-only **`net-r-regrade`** system-action (#6826), against `d1eeb12` on the live VM. Full output: issue #6829.

## Coverage — the honest denominator (P1)

**814 closed trades scanned.**

| bucket | count | meaning |
|---|--:|---|
| `broker_costed` | **3** | net_R computable AND `cost_source='broker'` (FIFO broker-truth fees) |
| `estimate_costed` | **679** | net_R computable AND `cost_source='estimate'` (Slice-A fixed-model) |
| `uncosted` | 1 | net_R computable but no cost attributed |
| `r_uncomputable` | 131 | no risk basis or no resolved gross pnl (orphan_adopt / unresolved rows) |

**This is an ESTIMATE-cost re-grade, not a broker-truth one.** Only **3 of 814**
(0.4%) trades carry FIFO broker-truth fees; the rest use the Slice-A fixed cost
model. **Every sign-flip and net-R number below is estimate-driven** and must be
confirmed against broker-truth before any Tier-3 action.

### Funding coverage = 0 (visibility gap, not a code bug)

`funding_paid_usd` is **0 for every trade** — perp funding is not in the net-R
anywhere. The per-symbol funding-puller fix (#6826) deployed and ran cleanly but
returned **0 funding records** for bybit_2 over 30 days across all four traded
perps (BTCUSDT/ETHUSDT/XRPUSDT/ADAUSDT), with no exception. The most likely cause
is the same one behind `BL-20260713-BYBIT2-PNL-UNDERRECORD`: bybit_2's funding
accrued on the **prior sub-account** the current API key cannot read. So the
crypto-perp cells' net-R is **fee-only** — it understates true cost on
funding-heavy holds. Tracked as `MB-20260717-M24-FUNDING-VISIBILITY`; the fix is
a data-visibility one (operator Bybit UM funding export, like the broker-truth
realized ledger), not a puller change.

## The headline — one sign-flip (P2)

**`spy_pullback_1h / SPY` — gross ΣR +1.456 → net ΣR −0.457** (n_R=9, cost-drag +1.913 R).

The only cell that is **gross-positive but net-negative after costs** — a
strategy that looks profitable on gross R but loses money once costs are charged.
**Caveat:** this is under the ESTIMATE cost model (SPY = alpaca equity, no
broker-truth row), and +0.21 R/trade of drag is high for an equity — the estimate
fee model may over-charge here. **Tier-3 review candidate, pending broker-truth
confirmation** — do not demote on the estimate alone.

Every other net-negative strategy was **already gross-negative** — costs only
deepen the loss, never flip the sign:

- `vwap / BTCUSDT` — gross −190.7 → net −454.8, **drag +264.1 R over 348 trades**. The estimate fee model on a high-frequency scalper; already a known loser. The extreme drag is itself evidence the estimate model likely over-charges HFT — another reason broker-truth matters.
- `mgc_trend_1h` — gross −227.3 → net −280.1 (drag +52.7).
- `htf_pullback_trend_2h` — gross −125.4 → net −127.2 (drag +1.8).
- `qqq_pullback_1h`, `ict_scalp_5m`, `gld_pullback_1h`, `sol/ada/avax_pullback_2h`, `tlt_*` — all already gross-negative.

**Net-positive-after-cost survivors** (gross-positive AND still net-positive):
`trend_donchian` (+19.5), `uso_trend_1h` (+7.3), `eth_pullback_2h` (+7.1),
`slv_trend_1h` (+5.8), `trend_donchian_ada_4h` (+5.7), `trend_donchian_eth_4h`
(+4.7), `trend_donchian_xrp_4h` (+3.3), `xrp_pullback_2h` (+2.4),
`trend_donchian_avax_4h` (+0.2), `pairs_sol_eth_b` (+0.03). The Donchian trend
family + eth/xrp pullbacks survive costs cleanly; the pullback-2h alt-coins
(sol/ada/avax) do not.

## Full scorecard

Reproduce any time: dispatch the `net-r-regrade` system-action (Tier-1, read-only).
The committed run (issue #6829, `generated_at 2026-07-17T23:46:57Z`):

- Per-strategy + per-`(strategy,symbol)` Σgross_R vs Σnet_R + drag_R tables are in
  the issue-#6829 comment (33 strategies / 36 cells). The one 🚩 is
  `spy_pullback_1h`.

## P3 / P4 — the Tier-3 next steps (operator-gated)

These are **order-routing/sizing-affecting** — proposed here, not enacted:

- **P3 — Cost-aware EV scorer refresh.** Feed the measured per-cell net-R into
  `src/runtime/allocator_ev.py::candidate_ev_score` (replace the fixed cost model
  with the measured one) + add the decision-time correlation/covariance feature
  (`MB-20260629-ALLOC-CORR`). Re-run the sizing-normalized allocator A/B — does
  EV *selection* beat dumb priority once costs are real? (The exact test T1.3
  failed.) **Blocked on better cost coverage first:** with 3/814 broker-truth +
  0 funding, the measured net-R is ~99% estimate — feeding an estimate-cost
  distribution into the EV scorer just re-derives the fixed model. Recommend
  P3 waits on (a) broker-truth fee coverage widening (more of the fills window)
  and (b) the funding-visibility fix.
- **P4 — Within-tick contrastive net-R ranker.** Rank the candidates present on
  the same tick by realized net-R (not a global base rate); LightGBM ranker on
  the M18 candidate→shadow→advisory ladder. Same coverage prerequisite as P3.

## Honest bottom line

The M24 P1/P2 **machinery is done and validated** — it computes true net-of-cost
R, produces a committed scorecard, and surfaced one real sign-flip candidate
(`spy_pullback_1h`). But the **cost coverage is thin** (3/814 broker-truth,
funding entirely absent), so the scorecard is an **estimate-cost** view. The
binding next step for M24 to *matter* (P3/P4) is not more modeling — it is
**broker-truth cost coverage**: widen the fills-store history and close the
bybit_2 funding-visibility gap. Both are data steps, teed up for the operator.
