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

## Honest caveats — what's still gated

1. **In-sample** (cells authored from full ETH history) — needs the
   cell-selection walk-forward (`scripts/ml/walkforward_cell_selection.py` is now
   symbol-capable via `--symbol`) before any live ETH cell authoring.
2. **Live promotion blocked on the labeling gap.** Taking ETH live needs
   `eth-regime-1h-lgbm-v1` at **advisory**, which needs an RG4 live-row pass —
   currently UNSCOREABLE because every live ETH regime row is unlabeled
   (`MB-20260627-002` / `MB-20260626-001` #1). Fix the MES/ETH live-labeling gap
   → RG4 can judge → promotion → live ETH cells (Tier-3).
3. Single backtest pass, one alt-symbol. SOL is the next candidate (head needs
   training; data present).
