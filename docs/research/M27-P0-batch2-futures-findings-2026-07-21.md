# M27 P0 — Batch-2 futures findings (MES / MGC / MHG, 2026-07-21)

**Question:** does the ict_scalp_5m setup transfer to the IBKR micro-futures
sleeve (MES / MGC / MHG) under a venue-correct **per-contract** cost model?

**Answer: no verdict is possible — the setup is UNDERPOWERED on futures 5m.**
Over a full year of native IBKR history the scalp fired **8–16 trades per
symbol** (MES 16, MGC 14, MHG 8) versus ~285/yr per crypto symbol in Batch-1.
At that volume the anchored 4-fold k-fold carries 2–4 trades per fold — noise,
not evidence. The gross directional tilt is **MES-positive, MGC/MHG-negative**,
but nothing here clears (or fails) a statistical gate. **Futures scalping should
be re-evaluated at 15m+ (P1)** — which is consistent with the entire existing
futures sleeve trading only 1h/1d (`mgc_trend_1h`, `mes_trend_long_1d`,
`mgc_pullback_1d`, `mhg_pullback_1d`).

## Rig

- Data: native IBKR micro-futures 5m + 15m, ~1 year (the `pull-ibkr-history`
  pulls: MES `2025-07-24 → 2026-07-19` 57,519 bars; MGC `2025-07-29 →
  2026-07-20` 59,109; MHG `2025-08-28 → 2026-07-20` 58,911), pulled on the live
  VM (shares the one IB gateway, clientId 450, paced) and rsynced to the
  trainer. Shard → CSV via `scripts/research/m27/ibkr_jsonl_to_csv.py`
  (tz-aware `+00:00`, the #7199 contract).
- Cost model — **per-contract USD round-trip** (`kfold_oos.py
  --fee-usd-roundtrip --contract-value-usd`, new in #7236): a flat USD cost
  (≈ $1 commission + 1 tick/side slippage) charged against each trade's own
  **dollar** risk (`risk_points × contract_value_usd`), NOT bps-of-price.
  Values: MES `$3.50 / cv 5.0`, MGC `$3.00 / cv 10.0`, MHG `$3.50 / cv 2500.0`.
  Because futures stops are wide in dollar terms and commissions are flat, the
  fee load in R is **much smaller than crypto's** (~0.02–0.08R vs 0.20R) — fees
  are NOT the binding constraint on futures; **signal scarcity is.**
- Vol specs frozen from the **earliest 20% data prefix** (`--derive-window
  prefix:0.2`) — the IBKR history reaches only ~1y, so calendar-2023 is
  unavailable; 20% stays strictly inside the first fold's train territory.
- Artifacts: trainer `/home/ubuntu/m27_out_fut/<SYM>/`; full JSONs mirrored in
  relay issue #7249.

## Results

| Symbol | Trades/yr | Win % | Gross ExpR | Gross TotR | Net baseline k-fold | Net off-cells k-fold |
|---|---|---|---|---|---|---|
| MES | 16 | 68.75 | +0.388 | +6.2 | 2/4, n=14, +4.04R | 2/2, n=4, +2.42R |
| MGC | 14 | 35.71 | −0.338 | −4.73 | 2/4, n=12, −6.83R | 1/3, n=4, −1.22R |
| MHG | 8 | 37.50 | −0.042 | −0.33 | 1/3, n=4, −1.74R | 1/2, n=2, +0.31R |

**Every k-fold cell is n ≤ 14 across the whole period and ≤ 4 per fold.** MES's
positive numbers and MHG's off-cells "1/2 +0.31R" are 2–4 trades apiece — they
are not signal. `calm_only_5m` / `calm_only_15m` / `fitted_conf_oos` are
**empty (n=0)** on all three: with the degenerate 5m vol distribution (below)
and single-digit trade counts, no trades land in those buckets.

## Why so few trades (the actual finding)

1. **Session structure.** Futures trade RTH + a long, thin Globex overnight.
   The ict_scalp FVG+sweep setup needs live liquidity structure; it rarely
   forms in the overnight tape, and even RTH-only the micro-futures 5m
   generates the setup ~3–4× less often than a 24/7 crypto perp. The setup is a
   **crypto-native** structure.
2. **Degenerate 5m vol terciles.** The frozen 5m vol edges came back with
   **q33 = 0.0** on every futures symbol (MES `[0.0, 0.00036]`, MHG
   `[0.0, 0.00037]`, MGC similar) — a third of the derivation bars have
   *exactly zero* rolling log-return vol, i.e. flat overnight bars with
   identical closes. The 5m vol label is therefore near-useless for futures;
   only the 15m edges are healthy (MES `[0.00034, 0.00095]`, MHG
   `[0.00048, 0.00165]`). This is why any futures scalp regime gate must be
   built on the 15m (or coarser) label, never the 5m one.

**Caveat (honest):** the low count is consistent with genuine setup rarity, but
a stitched-`ContFuture` 5m series can also carry roll-boundary gaps that
suppress the rolling-window setup detector. Distinguishing "the setup genuinely
almost never forms" from "a data-continuity artifact eats the signals" is the
open diagnostic — filed to the performance-review backlog
(`PB-20260721-M27-FUTURES-5M-LOWSIGNAL`). Either way, 5m is the wrong timeframe
for a futures scalp.

## Recommendation (no Tier-3 — proposals only)

- **No futures 5m scalp leg.** None of MES/MGC/MHG reaches a gate; the two that
  are gross-negative (MGC/MHG) actively argue against it.
- **Route futures scalp research to P1 at 15m+**, where the vol label is
  non-degenerate and (on a higher timeframe) the bar count per setup is more
  favourable — and where the existing sleeve already lives (1h/1d).
- **Run the low-signal diagnostic** (`PB-20260721-M27-FUTURES-5M-LOWSIGNAL`)
  before any 15m futures run, so a data-continuity artifact isn't mistaken for
  a timeframe verdict.

## Coverage impact

MES/MGC/MHG 5m cells resolve **❌ rejected (underpowered — 8–16 trades/yr, no
gate)**; their 15m cells move to **P1** with the diagnostic as a prerequisite.
