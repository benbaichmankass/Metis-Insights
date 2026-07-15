# M22 — Reliable small-TF trading tool: research directions

**Date:** 2026-07-15 · **Status:** PROPOSED / IN PROGRESS · **Tier:** 1 (research;
Tier-3 items are operator-gated proposals) · **Predecessor:** the chop-scalp
study (`docs/research/chop-scalp-capital-efficiency-2026-07-15.md`, PR #6479/#6485).

## Why (the diagnosis)

The chop-scalp study closed the "just go faster" idea: at **5m and 1m**, on BTC +
ETH, every hard-rules small-TF cell is **net-negative**, and in the 1m/roster data
**`fee_R` ≈ the entire loss** — the *gross* edge is ~breakeven and round-trip fees
sink it. A small-TF scan of the whole roster (`squeeze/fade/trend/pullback/fvg_range`)
at 5m/15m was uniformly negative; only `fvg_range` at its **native 15m** is positive.

Small-TF fails for **two compounding reasons**:

1. **Fee-drag.** The backtests charge **7.5 bps taker on BOTH legs**
   (`scripts/backtest_system.py`, `allocator_ev.py::fee_R`), and the **live order
   path is 100% market/taker** (`src/units/accounts/execute.py` hardcodes
   `orderType:"Market"`; IBKR/Alpaca likewise). Bybit **maker** fees on linear
   perps are ~0/rebate. The maker fix (`maker_band_post_only`) is *recognized but
   deferred* (`config/research/recombination_pool.yaml` `_deferred`;
   `config/research/studies/pullback_exit_feebleed.yaml`).
2. **OHLCV-blindness.** The small-TF edge lives in **order-flow** (OFI / VPIN /
   micro-price). That feature pipeline is fully built
   (`ml/datasets/orderflow_features.py` + `scripts/ml/orderflow_capture.py` +
   `deploy/trainer/ict-orderflow-capture.service`) but **data-starved** — L2 can
   only be captured FORWARD, never backfilled (`MB-20260604-002`, `MB-20260613-002`).

The direct ML lever — a **P(win) entry-selectivity filter** ("trade only the ~10%
of signals likely to win" → cut frequency → cut fee drag) — has been **attempted
twice and failed** on OHLCV decision-bar features (M18 ranker OOS-AUC ~0.51,
`M18-allocator-backtest-findings-2026-06-29.md`; M21 entry-head ~0.46–0.56 after
a leakage correction, `M21-entry-refinement-DESIGN.md`). All infra to build/wire
one exists; the missing piece is **signal**.

**Closed dead-ends — do NOT re-propose:** faster chop-scalp, price-only
deep/representation models (T0.1/T1.1/T1.2, ×3 negative), HMM/GMM regime,
partial-TP banking, P(win) on OHLCV decision-bar features, standalone cross-asset
*directional* strategies.

## The four directions (operator-approved 2026-07-15)

Each is a different response to *why* small-TF failed. Sequenced by dependency.

### P1 — Maker-fee economics (decisive gate; Tier-1 research)
Re-price the small-TF cells under **maker** execution. If nothing flips positive
even at maker/zero fees, small-TF hard rules are truly dead; if cells flip, the
answer is *execution*, and the follow-up is building post-only entries.
- Additive per-trade emit fields (`entry`, `exit_price`, `risk`) on the small-TF
  harnesses + a re-scorer (`scripts/research/maker_economics.py`) that recomputes
  net metrics under `(maker_entry_bps, exit_bps, maker_fill_rate)`, reported as an
  **optimistic** bound (maker both legs, 100% fill) and a **realistic** bound
  (maker entry × fill-rate discount + taker exit + adverse-selection haircut).
- **Faithfulness check:** reproduce the 7.5bps-taker numbers exactly at the taker
  setting before trusting the maker scenarios. A cell only "flips" if it clears
  the **realistic** bound.
- **Follow-up if positive:** Tier-3 proposal to build post-only limit entry in the
  Bybit executor (the deferred `maker_band_post_only`). Not self-wired.

**Findings (trainer run #6489, BTC 3yr, net-of-fee):**

| cell | tf | trades | gross_R | net_R taker | net maker(both, opt) | net maker(entry/taker-exit, real) | flips? |
|---|--:|--:|--:|--:|--:|--:|:-:|
| fade | 5m | 13,116 | **+338** | −7,226 | +338 | −2,224 | ~ |
| pullback | 5m | 5,087 | **+350** | −996 | +350 | −255 | ~ |
| trend | 5m | 10,812 | **+284** | −2,996 | +284 | −943 | ~ |
| squeeze | 5m | 5,310 | **+241** | −1,035 | +241 | −302 | ~ |
| trend | 15m | 3,243 | +98 | −359 | +98 | −117 | ~ |
| chop_scalp | 5m | 82 | +18 | −22.7 | +18 | −2.4 | ~ |
| fvg_range | 15m | 45 | +30 | +24.1 | +30 | +15.6 | ✅ |
| fvg_range | 5m | 83 | −11 | −22.8 | −11 | −11 | ❌ |
| fade | 15m | 4,349 | −173 | −1,385 | −173 | −520 | ❌ |

1. **The fee-drag diagnosis is CONFIRMED, emphatically.** Nearly every cell has a
   **positive gross edge** (the maker-both/optimistic column) — fade_5m +338R,
   pullback_5m +350R, trend_5m +284R, squeeze_5m +241R. Taker fees are exactly
   what turn them net-negative. The edge is real; the execution destroys it.
2. **But maker execution does NOT realistically rescue the stop-based scalps.**
   The realistic bound (maker ENTRY + TAKER EXIT ≈ half the round-trip cost, ×60%
   fill) stays net-negative for every cell — half the fee is still far larger than
   the thin per-trade gross edge (fade_5m gross is +0.026R/trade). Only **maker on
   BOTH legs** (~0/rebate) clears them — and a **stop-based strategy's exit is
   inherently a TAKER event** (you cross the book at the SL/TP), so both-legs-maker
   is not achievable for these strategies. The only realistic-maker positive is
   `fvg_range_15m`, which was already net-positive at taker (the incumbent live
   strategy) — **no NEW cell flips.**
3. **The strategic payoff — where maker DOES work — is the cross-link to P3.**
   Maker on both legs is only realistic when **both legs can be resting limits**
   (no market-order stop). That excludes the stop-based scalps but *includes* the
   **market-neutral funding carry** (P3): it exits on funding-decay/timeout, not a
   price stop, so BOTH legs can be maker. Its gross harvest (+9 to +17R, sub-2R
   drawdown) is precisely the fee-eaten edge maker execution recovers. **The
   reliable-tool candidate that emerges from P1+P3 is a maker-executed
   market-neutral funding-carry sleeve**, not a faster scalp.
**Maker-carry follow-up RESOLVED (trainer run #6493, neutral funding-carry at
TAKER 7.5 vs MAKER 1.0 vs ZERO fee, full 3yr 2023-01→2026-03 + OOS 2025-01+ at maker):**

| pair | full net_R taker 7.5 | full net_R maker 1.0 | full net_R zero | full maxDD_R (maker) | OOS 2025+ net_R maker 1.0 (trades) |
|---|--:|--:|--:|--:|--:|
| ETH | +0.70 | **+7.72** | +8.80 | 0.047 | **+0.40** (39) |
| SOL | +0.98 | **+6.46** | +7.31 | 0.083 | +0.07 (65, ~flat) |
| BNB | −1.87 | **+11.03** | +13.01 | 0.68 | **−0.61** (102) |

1. **Maker execution IS the lever — confirmed for the neutral carry.** Because
   the neutral carry exits ONLY on timeout/funding-decay (no market stop), BOTH
   legs can rest as maker limits, so the both-legs-maker bound is *achievable*
   here (unlike the stop-based scalps). At 1.0bps the full-history net recovers
   ~70% of the gross funding harvest (`net_funding_r` 10.96/9.00/16.98R) with
   *tiny* drawdown (maxDD 0.05–0.68R): ETH +0.70→**+7.72R**, SOL +0.98→**+6.46R**,
   BNB −1.87→**+11.03R**. The fee-drag diagnosis is fully closed: at maker fees
   the gross edge survives.
2. **BUT the current regime is thin-to-negative even at maker.** The OOS window
   (2025-01→2026-03), which is the regime we'd actually trade into, nets only
   ETH **+0.40R** / SOL **+0.07R** (flat) / BNB **−0.61R** at maker — the win is
   front-loaded in 2023-24 (`by_year`: every pair's 2025 is ~0 or negative).
   Funding rates compressed post-2024 (confirms `PB-20260620-002`).
3. **Verdict:** the maker-executed neutral funding-carry is a **real,
   regime-gated candidate**, not a live-now proposal. It is *validated in
   principle* (maker recovers the edge, drawdown is trivially small) but
   *unattractive in the present regime*. **Parked with a documented
   re-trigger** (`PB-20260715-MAKER-CARRY`): re-evaluate the OOS maker net when
   funding elevates; a Tier-3 proposal for (a) a post-only/limit carry executor
   and (b) wiring the neutral-carry sleeve is warranted **only** once a
   *current-regime* OOS maker net clears a meaningful bar (not the front-loaded
   3yr average). Building post-only entry for the stop-based scalps is **not**
   worth it either way (their exits stay taker; the realistic bound proves it).

### P2 — Order-flow capture clock (start now; forward-only)
Order-flow is the academically-grounded small-TF edge and the one untested feature
family, but forward-capture-only — every day not capturing is lost.
- Verify `ict-orderflow-capture.service` is running + accruing `market_microstructure`
  bars + symbol coverage (extend to ETH/SOL if BTC-only); start if not.
- **A/B trigger (weeks out):** once ≥N bars, train `btc-regime-5m-lgbm-flow-v1`
  (the ready flow manifest) vs the v2 head, and feed `ofi/vpin/microprice_dev` to a
  P(win) entry-head candidate (→ P4). This is a *kickoff + calendar gate*.

**Findings:** _(pending — trainer run #6488)_

### P3 — Market-neutral / non-price sleeve (parallel; Tier-1 research)
Change the edge to something less fee/noise-fragile. Harnesses + multi-year data
already exist (research-only, unwired).
- Run `backtest_funding_carry.py` (directional + `--hedge neutral`) and
  `backtest_xsec_momentum.py` (BTC/ETH/SOL/BNB) net-of-fee, walk-forward.
  **Report the current funding regime** (carry is regime-dependent, `PB-20260620-002`).
- **Follow-up if positive + low-correlation:** Tier-3 wiring proposal via the
  `new-strategy` skill (incl. `account_compat_matrix`).

**Findings (trainer run #6490, 3yr 2023-01→2026-03, net-of-fee 7.5bps):**
- **Funding-carry, market-neutral (pure carry):** harvests a **real** funding edge
  — gross `net_funding_r` **+10.96R (ETH) / +9.00R (SOL) / +16.98R (BNB)** with
  *tiny* drawdown (ETH maxDD **0.81R**, SOL 1.41R) — but **round-trip taker fees +
  hedge cost eat ~94% of it**: net **ETH +0.70R, SOL +0.98R, BNB −1.87R**. Same
  fee-drag class as the scalps: the gross edge is real, taker execution annihilates it.
- **Funding regime compressed:** all funding-carry positive years are 2023–24;
  2025–26 are negative — funding rates have fallen, so the carry is smaller now
  (confirms `PB-20260620-002`; the current regime is carry-unfavorable).
- **Funding-carry directional** (funding + unhedged price): dominated by the price
  bet, not the carry — ETH −25.7R, SOL +18.3R (front-loaded 2023), BNB +13.4R
  (bumpy). Not a reliable funding edge.
- **Cross-sectional momentum:** GATE **FAILED** — net total return **−0.31** over
  3yr, Sharpe −0.21, a k-fold not net-positive after fees, 2×-fee Sharpe negative
  (only the holdout passed). The 4-symbol crypto universe is too small for a
  cross-sectional rank-N sleeve; needs a much wider universe.
- **Verdict:** no reliable sleeve *as-is*. But the neutral carry's substantial
  gross funding harvest (+9 to +17R) that taker fees destroy is **the same
  fee-drag story as the small-TF scalps** — so **P1 (maker execution) is the
  common lever**. **This was tested directly (#6493, see the P1 maker-carry block
  above):** at maker 1.0bps the full-history net recovers ~70% of the gross
  (ETH +7.72R / SOL +6.46R / BNB +11.03R, maxDD 0.05–0.68R) — the P1 lever
  works. **However the current-regime OOS (2025-01+) maker net is thin-to-negative**
  (ETH +0.40 / SOL +0.07 / BNB −0.61), so the maker-carry sleeve is a real but
  **regime-gated** candidate, parked with a re-trigger (`PB-20260715-MAKER-CARRY`),
  NOT a live-now proposal. Xsec is parked pending a wider universe.

### P4 — P(win) entry filter, attempt 3 (GATED; downstream)
Do **NOT** re-run on OHLCV decision-bar features (twice failed). Only attempt with
a genuinely new input: (a) the **order-flow features** from P2 once data accrues,
and/or (b) a **net-R regression label** (requires per-trade cost-capture
`MB-20260629-ALLOC-COSTCAP` first). Reuse `scripts/ml/train_entry_head.py`,
`src/runtime/entry_head_pwin.py`, `ml/evaluators/classification_auc.py` (AUC>0.55
kill-criterion), and the one-line hook `allocator_ev.py::candidate_p_win`. A head
that clears the honest decision-bar OOS gate **unblocks the parked M18 allocator
selection**.

**Findings:** _(gated — not started)_

## Cross-cutting unblocker
**Per-trade net-R cost capture** (`MB-20260629-ALLOC-COSTCAP`) enables P1
(measurable live maker savings), P3 (net sleeve PnL), and P4b (net-R label).

## Verification
- P1: re-scorer reproduces native net_R exactly at 7.5bps taker (unit test);
  report optimistic + realistic bounds; flip = clears the realistic bound.
- P2: capture service `active` and `market_microstructure` row count **increasing**
  across two reads; A/B trigger + threshold documented.
- P3: harness self-tests + walk-forward IS/OOS, net-of-fee, current-regime caveat;
  Tier-3 proposal only if net-positive OOS AND low-correlation to the live book.
- P4 (when unblocked): `classification_auc` OOS AUC materially > 0.55 at the
  **decision bar**, honest features only.
- Every negative result is documented (the negatives are the deliverable);
  Tier-3 items proposed, never self-merged.
