# Design-A multi-symbol (#1) — ETH confirmation (2026-06-27)

The BTC vol-gate (Design-A) generalizes to **ETHUSDT**, decisively. This is the
first cross-symbol confirmation that the ML vol-verdict gate is not a BTC
artifact.

## Enabler

`scripts/backtest_system.py` was hardcoded to BTCUSDT; this session generalized
it to `--symbol` (default BTCUSDT → byte-identical BTC behavior, all 9 evidence
tests pass) + a per-symbol roster mapping the live `*_eth`/`*_sol` strategy names
to their shared logic modules (`trend_donchian` / `htf_pullback_trend_2h`).

## ETH vol-split (trainer-vm-diag #4849/#4851)

`--symbol ETHUSDT --data data/ETHUSDT_5m.csv --roster trend_donchian_eth,
trend_donchian_eth_4h,eth_pullback_2h --clock-tf 1h --vol-verdict ml
--ml-model-id eth-regime-1h-lgbm-v1`. The ETH head resolved **live in the
harness** (`available=True scored=3007 fell_back_to_frozen=0`).

Ungated ETH book: **net $63 / maxDD $1691 (15%) / 1016 trades** — marginal, large
drawdown. Per-cell decomposition (worst-first, meaningful sample):

| cell | net $ | trades |
|---|---:|---:|
| **trend_donchian_eth \| trending \| volatile \| long** | **−968** | 175 |
| trend_donchian_eth \| transitional \| volatile \| long | −383 | 84 |
| eth_pullback_2h \| trending \| calm \| short | −260 | 22 |
| trend_donchian_eth \| chop \| volatile \| short | −169 | 45 |
| trend_donchian_eth \| chop \| calm \| short | −168 | 18 |
| trend_donchian_eth \| trending \| calm \| short | −138 | 13 |
| eth_pullback_2h \| trending \| volatile \| short | −117 | 81 |
| trend_donchian_eth \| chop \| volatile \| long | −50 | 61 |
| trend_donchian_eth_4h \| trending \| volatile \| short | −32 | 17 |
| … | | |
| **trend_donchian_eth \| trending \| volatile \| SHORT** | **+433** | 193 |
| eth_pullback_2h \| trending \| calm \| long | +464 | 13 |

**The dominant ETH loser is `trend_donchian_eth | trending | volatile | long`
(−$968/175t) — the SAME vol-conditioned pattern as BTC** (`trend_donchian|trending|
volatile|long` was −$224 there). A Donchian long in a *volatile* "trend" is a
false-breakout trap on both symbols; the SHORT side of that same cell *wins*
(+$433/193t on ETH). The cells are authored in
`docs/research/regime_policy_eth_trend_vol-2026-06-27.yaml` (9 OFF-sides,
meaningful ≥10t net-negatives; the marginal −$49/15t `trending|calm|long` left ON
per the strong BTC prior that calm-trend-long is the winner).

## Confirmation A/B (trainer-vm-diag #4852/#4854)

| arm | net $ | maxDD $ | ret/DD | trades |
|---|---:|---:|---:|---:|
| ungated | 63 | 1691 (15%) | 0.04 | 1016 |
| **ev-ml-gated** | **2336 (23%)** | **1270 (10%)** | **1.84** | 669 |

**Gating the evidence cells lifts the ETH book net $63 → $2336 AND cuts maxDD
25% ($1691 → $1270)** — ret/DD 0.04 → 1.84 — by removing ~347 net-negative trades.
The lift is *larger* than BTC's (37× vs 4.3×) because the ungated ETH base was
near break-even, so the losing sleeves dominated it. Same mechanism, second
symbol → **the vol-gate generalizes cross-symbol.**

## Cell-selection walk-forward (the strict OOS test — DONE, trainer #4857/#4861)

`scripts/ml/walkforward_cell_selection.py --symbol ETHUSDT` re-derives the ETH
OFF-cells from each in-sample window and applies them OOS (expanding window):

| OOS fold | ungated net / maxDD | ev-ml net / maxDD | net | maxDD |
|---|---:|---:|:--:|:--:|
| 2023-07 → 2024-07 | $126 / $773 | −$76 / $453 | **FAIL** | PASS |
| 2024-07 → 2025-07 | −$387 / $848 | $412 / $403 | PASS (+$799) | PASS |
| 2025-07 → 2026-06 | $612 / $835 | $751 / $432 | PASS (+$139) | PASS |

**Honest mixed result — weaker than BTC's clean 3/3+3/3:**
- **maxDD: PASS 3/3** — the cells reliably ~halve drawdown out-of-sample every
  fold. The de-risking generalizes.
- **net: 2/3** — the *earliest* fold FAILS (gating hurt net, −$76 vs +$126); its
  cells were authored from a thin <2023-07 window (460 trades) and the broader
  cell set (short-side + chop cells) didn't generalize. The other two folds pass,
  one a large rescue (−$387 → $412).
- **The CORE cell generalizes:** `trend_donchian_eth|trending|volatile|long` is
  net-negative in **all three** in-sample windows (−$211 / −$355 / −$655) — the
  load-bearing volatile-long sleeve is robust; the noise is in the marginal
  short/chop cells the thin early window over-selected.

**Verdict:** the in-sample $2336 was optimistic. The realistic OOS read is "ETH
multi-symbol A reliably **cuts drawdown** and is **net-positive in 2 of 3**
windows, anchored by the robust volatile-long cell." Worth pursuing — primarily
for the drawdown benefit + the strong core cell — but NOT a slam-dunk like BTC; a
live ETH cell set should likely be **conservative** (the robust core cell ±
the largest-sample losers), not the full 9-cell in-sample set.

## Labeling-gap fix + the RG4 live verdict (DONE, trainer #4865/#4866)

Root-caused the MES/ETH live-labeling gap: `scripts/ops/build_trainer_datasets.sh`
rebuilt `market_raw`/`market_features` for **BTCUSDT only**, so the alt regime
heads' realized-label datasets (built once, ETH 06-17) perpetually went stale →
RG4's label join never covered the live shadow rows. **Fixed** (`build_bybit_pair`
+ ETH/SOL added to the daily loop, commit 7a051e5). Validated: refreshed the ETH
dataset → ETH RG4 unlabeled **353/353 → 6**. Gap closed, durably.

**But the fix revealed the decisive finding — the ETH head FAILS RG4 live:**

| head | RG3 offline | RG4 live (post-fix) | verdict |
|---|---:|---:|---|
| eth-regime-1h-lgbm-v1 | 0.73 | **0.46** (111 labeled) | **NO_EDGE** |
| eth-regime-1h-lgbm-xasset-v1 | 0.70 | **0.46** (347 labeled) | **NO_EDGE** |

The ETH head discriminates the vol regime **offline / on clean harness candles**
(RG3 0.70-0.73 → drove the backtest A/B), but its **actual live logged predictions
do not** (RG4 ~0.46 ≈ random, on a non-trivial 347-row sample). That is
train/serve skew — the exact failure RG4 exists to catch.

**Consequence (honest revision):** the backtest A/B ($63 → $2336) is **optimistic
for LIVE** — it scored the head on clean candles where it works; live, the head
would feed the order path a ~random vol label, not the backtest's labels. **ETH
multi-symbol A is NOT live-ready.** RG4 prevented a bad promotion.

## Honest caveats / what's needed for ETH to go live

1. **The ETH head must clear RG4 first.** It's NO_EDGE live (0.46) — needs
   retraining / live-feature-parity investigation (why does it discriminate
   offline but not on the logged live rows? — same skew class as the BTC `yz`
   heads). Until a retrained ETH head passes RG4, no advisory promotion → no live
   ETH cells, regardless of how good the backtest looks.
2. **net only 2/3 OOS** (cell-selection WF above) — even with a good head, a live
   ETH cell set should be conservative (core cell + biggest losers).
3. The labeling-gap fix is the lasting win: ANY alt head can now be RG4-validated
   each cycle — the gate works, and it's already separating the wheat (BTC head,
   RG4 0.72) from the chaff (ETH head, RG4 0.46).
2. **Live promotion blocked on the labeling gap.** Taking ETH live needs
   `eth-regime-1h-lgbm-v1` at **advisory**, which needs an RG4 live-row pass —
   currently UNSCOREABLE because every live ETH regime row is unlabeled
   (`MB-20260627-002` / `MB-20260626-001` #1). Fix the MES/ETH live-labeling gap
   → RG4 can judge → promotion → live ETH cells (Tier-3).
3. Single backtest pass, one alt-symbol. SOL is the next candidate (head needs
   training; data present).
