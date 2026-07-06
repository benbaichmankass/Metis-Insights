# Design-A — ETH/SOL evidence-based `trend_vol` OFF-cell studies (2026-07-06)

Extends the BTC vol-gate money win (`A-vol-gating-OFFcell-design-2026-06-27.md`,
live-enforced for BTC since 2026-06-28) to **ETHUSDT and SOLUSDT**, per
`MB-20260628-VOLGATE-GOLIVE`. Method mirrors the BTC study **per symbol** —
cells are re-derived from each symbol's own attribution under its **own 15m
regime head**; BTC's cells are **not** copied across symbols (operator
directive, empirically vindicated below).

- ML vol labels: `eth-regime-15m-lgbm-v1` / `sol-regime-15m-lgbm-v1` (both
  `shadow` since 2026-06-28; live soak 861 / 825 preds as of this session,
  scores non-degenerate). Harness pins the head by id (`--ml-model-id`,
  `--ml-stage shadow`) — observe-only, no registry mutation.
- Rosters (live strategy names, per-symbol):
  ETH `trend_donchian_eth, trend_donchian_eth_4h, eth_pullback_2h`;
  SOL `trend_donchian_sol, trend_donchian_sol_4h, sol_pullback_2h`.
- Data: trainer `data/ETHUSDT_5m.csv` (2021-03-15 → 2026-06-18) and
  `data/SOLUSDT_5m.csv` (2021-10-15 → 2026-06-18); clock TF 15m.
- Runs: trainer-vm-diag #5726 (phase 1 attribution), #5729 (phase 2 A/B +
  walk-forwards); artifacts under trainer `runtime_logs/volgate_ethsol/`.
- The stale 2026-06-27 ETH draft
  (`regime_policy_eth_trend_vol-2026-06-27.yaml`) was derived under
  `eth-regime-1h-lgbm-v1`, which **failed RG4** — it is superseded by this
  study and must not be used for live authoring.

## Phase 1 — per-(strategy, trend, vol, side) attribution (ungated, ML label)

Both heads scored every bar: ETH `scored=3007, fell_back=0`; SOL
`scored=2004, fell_back=0`.

**ETHUSDT** — ungated book net **$850** / maxDD $1876 (15.7%!) / 1004t / WR 32.5%.
Meaningful-sample (≥10t) net-negative cells (the mechanical evidence rule):

| cell (strategy \| trend \| vol \| side) | net $ | trades | wins |
|---|---:|---:|---:|
| trend_donchian_eth \| trending \| calm \| long | −434.90 | 121 | 33 |
| trend_donchian_eth \| transitional \| volatile \| short | −312.49 | 22 | 5 |
| eth_pullback_2h \| trending \| calm \| short | −172.31 | 25 | 5 |
| trend_donchian_eth \| transitional \| calm \| long | −142.09 | 21 | 5 |
| eth_pullback_2h \| trending \| volatile \| short | −140.04 | 39 | 15 |
| eth_pullback_2h \| transitional \| volatile \| short | −80.32 | 15 | 5 |
| eth_pullback_2h \| chop \| calm \| long | −14.02 | 10 | 3 |

Sum of gated sleeves ≈ **−$1296** on a +$850 book.

**SOLUSDT** — ungated book net **$1831** / maxDD $1063 / 688t / WR 36.1%
(already the healthiest book of the three symbols).
Meaningful-sample net-negative cells:

| cell | net $ | trades | wins |
|---|---:|---:|---:|
| trend_donchian_sol \| trending \| calm \| long | −190.33 | 49 | 11 |
| sol_pullback_2h \| transitional \| volatile \| long | −104.33 | 15 | 6 |
| trend_donchian_sol \| trending \| volatile \| long | −103.70 | 241 | 76 |
| sol_pullback_2h \| chop \| calm \| short | −101.58 | 21 | 5 |
| sol_pullback_2h \| transitional \| calm \| short | −84.91 | 10 | 2 |
| sol_pullback_2h \| trending \| calm \| long | −22.96 | 12 | 3 |
| sol_pullback_2h \| chop \| volatile \| short | −22.30 | 27 | 9 |

Sum of gated sleeves ≈ **−$630**.

### Cross-symbol finding — the cells do NOT transfer

On BTC, `trend_donchian | trending | calm | long` was **the** winning cell
(+$1238); on ETH the same cell is the **biggest loser** (−$435/121t) and on SOL
it is also negative (−$190/49t). Copying BTC's OFF-cells to ETH/SOL would have
gated BTC's winner-shape and left each symbol's real losers ON. Per-symbol
re-derivation was mandatory, exactly as the go-live plan required. It also
confirms the label-source lesson: under the (RG4-failed) 1h ETH head the
2026-06-27 draft had `trending|volatile|long` as ETH's dominant loser (−$968);
under the RG4-passing 15m head that cell is **positive** (+$31/199t) — the vol
classifier IS the load-bearing piece.

## Phase 2 — confirmation A/B (full-history, in-sample by construction)

<!-- RESULTS_AB -->

## Phase 2 — fixed-cell walk-forward (yearly folds, 2022-07 → 2026-06)

Acceptance bars (the BTC/FLIP shape, per fold):
(1) ev-ml net ≥ ungated net; (2) ev-ml maxDD ≤ ungated maxDD;
(3, secondary) ev-ml vs ev-frozen.

<!-- RESULTS_WF -->

## Phase 2 — cell-SELECTION walk-forward (the strict test)

Cells re-derived per fold from only prior data (≥10t net-negative), applied OOS.

<!-- RESULTS_CELLSEL -->

## Verdict

<!-- VERDICT -->
