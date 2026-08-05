# C1 — Reductive conviction sizing on demo: evidence + flip proposal (2026-08-04)

> **W1.1 / C1** of the master-model convergence spine
> (`ROADMAP-REVIEW-WORKPLAN-2026-08-04.md` §6). C1 makes the **already-computed
> conviction number advise size**, reductive-first, on the **demo** account
> (`bybit_1`, no money at risk). `apply_conviction_sizing`
> (`src/runtime/conviction_sizing.py`) is fully built; `CONVICTION_SIZING_MODE=off`.
> This note records the **Tier-1 evidence** half (the A/B, run autonomously) and
> proposes the **Tier-3 flip** half (operator-gated).

## What this session did (Tier-1, landable)

Ran the `scripts/backtest_system.py` conviction-sizing A/B **now that the
execution-realism cost model is uniform across every harness** (merged 2026-08-04,
#8466/#8467/#8468/#8469) — so the comparison is **net-of-cost**, not gross.

## The measured result — and a normalization gap the workplan didn't anticipate

**ALWAYS STATE THE POPULATION:** the only feed available locally is a **7-day BTC
sample** (`data/btc_1m_sample.csv`) → **7 closed trades, an all-losing week**. This
is a **plumbing + directional** read, **NOT** deployable evidence. The real run
needs the full multi-year feed on the trainer VM (see "Next").

### Finding 1 — the harness on/off flag is NOT sizing-normalized
`--conviction-sizing` does not just re-shape size; it **swaps the risk budget**:
baseline sizes at the flat `--risk-pct` (0.3%), while the conviction arm sizes at
`conviction × PER_TRADE_RISK_BUDGET` (**2%**). A naive on/off A/B therefore
conflates the conviction *shape* with a **~6.7× budget increase** (confirmed: fees
scaled 3.6× and net went −$100 → −$452 on the *same 7 trades*). **A valid C1 A/B
must budget-match** — run the baseline at the same 2% ceiling so only the
conviction shape varies. (The workplan §6 called this the "sizing-normalized
harness"; the harness as-shipped is not normalized for this comparison, so the
normalization must be imposed by matching `--risk-pct` to the conviction budget.)

### Finding 2 — budget-matched, reductive lowers drawdown (as designed); net inconclusive on this sample
All three arms net-of-cost, same 7 trades:

| arm | net | maxDD | ret/DD |
|---|---|---|---|
| flat 0.3% (baseline) | −$99.88 | $155.99 | −0.64 |
| flat 2.0% (budget-matched baseline) | −$381.07 | $737.10 | −0.52 |
| **conviction × 2.0% (reductive)** | −$451.56 | **$635.59** | −0.71 |

Reductive conviction vs the **budget-matched** flat 2.0% baseline **cuts maxDD ~14%**
($737 → $636) — the reductive shape doing exactly what it's built to do (shrink
low-conviction size). Net is *worse* here, but on a 7-trade all-losing week that is
noise, not signal: when every trade loses, "size down the weak ones" only reshuffles
which loss is largest. **No net/expectancy claim is defensible at n=7.**

## Full-feed A/B — the feed bridge is BUILT (2026-08-04)

The blocker was a feed-format gap, not a soak: `backtest_system --data` reads CSV,
but the trainer's real crypto feed is `market_raw` jsonl (only a 7-day sample is
CSV). Rather than teach the harness to read jsonl, the bridge reuses the existing
`scripts/ops/fetch_backtest_candles.py` (Binance-vision, keyless) to produce the
full-history CSV the harness already reads — on a **free GH runner** (heavy compute
off the 1-OCPU trainer). Shipped as **`.github/workflows/c1-conviction-ab.yml`**
(trigger: label `c1-conviction-ab-request` or `workflow_dispatch`): per symbol it
fetches 5m candles → runs the **budget-matched** baseline (`--risk-pct 2.0`) vs
`--conviction-sizing`, both net-of-cost, and posts the net-$/maxDD/ret-DD comparison.
Re-runnable any time.

## Measured full-feed result (2026-08-05) — 365d per-symbol, budget-matched, net-of-cost

Run via the `c1-conviction-ab` workflow on the full **365d** Binance-vision 5m feed,
**one symbol per dispatch** (issues #8489/#8490/#8491). The combined 3-symbol run
overran the free-runner wall-clock cap twice: each `backtest_system` pass over a year
of 5m bars is ~25–30 min, so 6 passes (3 symbols × 2 arms) didn't fit even the raised
90-min cap — per-symbol is 2 passes ≈ 30 min with margin (workflow default lowered to
365d + 90-min cap in the same PR; 730d needs the per-symbol split). Baseline = flat
**2.0%** risk (matched to the conviction 2% budget so only the conviction *shape*
differs); conviction = **reductive ×2.0%**. Both net-of-cost.

| symbol | arm | trades | net $ | maxDD $ | Δ net | Δ maxDD | verdict |
|---|---|---|---|---|---|---|---|
| BTCUSDT | flat 2.0% | 231 | −11,759 | 11,823 | | | |
| BTCUSDT | conviction reductive | 477 | −9,966 | 10,013 | **+1,793** | **−1,810** | ✅ wins both |
| SOLUSDT | flat 2.0% | 300 | −10,136 | 10,136 | | | |
| SOLUSDT | conviction reductive | 617 | −9,728 | 11,922 | +408 | **+1,786** | ⚠️ mixed (better net, worse DD) |
| ETHUSDT | flat 2.0% | 11 | −23,462 | 23,473 | | | |
| ETHUSDT | conviction reductive | 12 | −12,108 | 12,118 | +11,354 | −11,356 | below-floor (n=11/12) |

**ALWAYS STATE THE POPULATION.** BTC (n=231/477) and SOL (n=300/617) clear the usable
floor; **ETH (n=11/12) does NOT** — its "wins both" is a small-n artifact, not evidence.
Two more caveats bound the read:
1. Both arms are heavily **net-negative** across all three symbols over this window — a
   **losing population**. So this is a read on the conviction *shape*, **not** a
   profitability claim: when the book loses, reductive sizing mostly reshuffles which
   losses are largest (the same caveat as the n=7 probe, now at real n).
2. The arms do **not** trade identical populations — reductive's smaller size clears a
   capital/risk gate that refuses some trades at flat 2%, so conviction trades ~2× the
   count. The comparison is shape-vs-shape on each arm's realized book, not a fixed set.

**Verdict — the C1 win condition (reductive cuts maxDD without worsening net over a
floor-clearing population) is PARTIAL.** It holds cleanly for **BTC** (lower net loss
AND lower drawdown at solid n) but **fails for SOL** (drawdown gets *worse*). ETH is
below-floor. So reductive conviction is **not** a clean cross-symbol drawdown win.

**Consequence for the flip:** it stays exactly where it already is —
`CONVICTION_SIZING_MODE=apply` / `DIRECTION=reductive` / `ACCOUNTS=bybit_1`, **demo
only** (`bybit_1`, no money at risk). The evidence supports *observing* reductive
conviction live on the demo book; it does **not** support widening scope to any
real-money account. Widening remains a **Tier-3** gate, pending a clean cross-symbol
maxDD win at solid n on a floor-clearing (ideally profitable) population.

## Next (to complete C1) — the remaining half

1. **~~Real evidence — DONE (2026-08-05)~~** — the per-symbol 365d budget-matched
   net-of-cost A/B is run and recorded above. Result: **PARTIAL** — reductive
   conviction cuts maxDD only on BTC (fails SOL, ETH below-floor), and over a
   net-negative population, so it is not a clean cross-symbol win. Not deployable
   evidence for widening past demo.
2. **The flip (Tier-3, operator-gated — the "advises size on demo" done-condition):**

   ```
   CONVICTION_SIZING_MODE=apply
   CONVICTION_SIZING_DIRECTION=reductive
   CONVICTION_SIZING_ACCOUNTS=bybit_1
   ```

   on the **live VM**, scoped to the **demo** account `bybit_1` only (no money at
   risk). Reductive-only is the live path — symmetric already failed the gate at
   4.5× maxDD (`CONVICTION_SIZING_MODE` docs). This is a live env-var change →
   **requires operator approval** before it goes on the VM. Once flipped, the
   orphaned conviction number advises size on demo for the first time, measured live.

**Status:** C1 is **COMPLETE** — the demo flip is live (`ACCOUNTS=bybit_1`) and the
deployable evidence run has landed (2026-08-05, above). The measured verdict is
**PARTIAL** (BTC wins, SOL mixed, ETH below-floor, over a net-negative population),
so the flip correctly stays **demo-only**; widening to any real-money account
remains a Tier-3 gate pending a clean cross-symbol maxDD win at solid n.
