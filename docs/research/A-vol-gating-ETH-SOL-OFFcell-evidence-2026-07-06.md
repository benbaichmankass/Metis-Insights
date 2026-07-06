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

## Harness regression found (and fixed): the vol-axis replay was a no-op

The first phase-2 attempt returned **byte-identical arms** (ungated ==
ev-frozen == ev-ml) for ETH. Root cause: since the #4896 **ML-only-enforce
guard**, `_hard_regime_gate` drops a `trend_vol` OFF-cell only when
`REGIME_ML_VERDICT_MODE=use` AND the **live** per-symbol advisory resolver
returns a concrete label — neither exists in an offline replay, so every
`--regime-router on` vol-cell arm silently equalled ungated (the gate is
fail-permissive by design). The BTC evidence runs (2026-06-27) predate the
guard; every vol-gating walk-forward script regressed to a no-op after it.
**Fix (this branch, BL-20260706-VOLGATE-REPLAY):** `run_system_backtest` now
sets the mode env in-process and points the gate's decision hook at the label
the run stamped on each intent (frozen or ML per `--vol-verdict`), restored on
teardown; regression test added
(`test_regime_router_on_enforces_trend_vol_cell_on_stamped_label`). All
phase-2 numbers below are from the fixed harness.

## Phase 2 — confirmation A/B (full-history, in-sample by construction)

**ETHUSDT** (7 evidence cells):

| arm | net $ | maxDD $ | ret/DD |
|---|---:|---:|---:|
| ungated | 850 | 1876 | 0.45 |
| ev-frozen | 1363 | 1434 | 0.95 |
| **ev-ml** | **1374** | **1446** | **0.95** |

In-sample the gate lifts net +62% and cuts maxDD 23%. Unlike BTC, the frozen
label performs almost identically to ML in-sample on ETH — the ETH cells are
less label-source-sensitive than BTC's were (BTC: frozen −$32 vs ML $1526).

**SOLUSDT** (7 evidence cells; trainer-vm-diag #5741/#5745 — the first SOL
pass ran against a missing policy file and was discarded):

| arm | net $ | maxDD $ | ret/DD |
|---|---:|---:|---:|
| ungated | 1831 | 1063 | 1.72 |
| ev-frozen | 1448 | 523 | 2.77 |
| **ev-ml** | **1633** | **532** | **3.07** |

SOL's ungated book is already the healthiest of the three symbols. In-sample
the gate halves maxDD for −$198 of net (ret/DD 1.72 → 3.07); ML beats frozen
(+$185 at equal DD).

## Phase 2 — fixed-cell walk-forward (yearly folds, 2022-07 → 2026-06)

Acceptance bars (the BTC/FLIP shape, per fold):
(1) ev-ml net ≥ ungated net; (2) ev-ml maxDD ≤ ungated maxDD;
(3, secondary) ev-ml vs ev-frozen.

**ETHUSDT:**

| fold | ungated net / maxDD | ev-frozen net / maxDD | ev-ml net / maxDD | net | DD |
|---|---:|---:|---:|:-:|:-:|
| 2022-07 → 2023-07 | −$265 / $531 | $80 / $584 | **$94 / $514** | ✔ | ✔ |
| 2023-07 → 2024-07 | −$546 / $746 | −$200 / $777 | **−$529 / $939** | ✔ (+$17) | ✘ (+$193) |
| 2024-07 → 2025-07 | −$599 / $923 | −$428 / $775 | **−$326 / $649** | ✔ | ✔ |
| 2025-07 → 2026-06 | $622 / $338 | $194 / $496 | **$697 / $352** | ✔ | ✘ (+$14, marginal) |

ETH fixed-cell: **net PASS 4/4, maxDD PASS 2/4** — the strict BTC bar
(lower maxDD in EVERY fold) is **not met** (fold-2 DD regression is real,
+$193; fold-4 is a $14 rounding-scale miss). ML beats frozen 3/4 folds.

**SOLUSDT:**

| fold | ungated net / maxDD | ev-frozen net / maxDD | ev-ml net / maxDD | net | DD |
|---|---:|---:|---:|:-:|:-:|
| 2022-07 → 2023-07 | $1019 / $327 | $547 / $267 | **$593 / $267** | ✘ (−$426) | ✔ |
| 2023-07 → 2024-07 | $292 / $368 | $375 / $305 | **$452 / $287** | ✔ | ✔ |
| 2024-07 → 2025-07 | $371 / $808 | $349 / $322 | **$394 / $284** | ✔ | ✔ |
| 2025-07 → 2026-06 | $100 / $538 | $1 / $461 | **−$0 / $462** | ✘ (−$100) | ✔ |

SOL fixed-cell: **net PASS 2/4, maxDD PASS 4/4** — drawdown improves every
fold (dramatically in 2024-25: $808 → $284), but the gate gives back net in
SOL's profitable years. ML beats frozen 3/4.

## Phase 2 — cell-SELECTION walk-forward (the strict test)

Cells re-derived per fold from only prior data (≥10t net-negative), applied OOS.

**ETHUSDT** (expanding-window, cells re-derived per fold, ≥10t net-negative):

| OOS fold | cells authored in-sample | ungated net / maxDD | ev-ml net / maxDD | net | DD |
|---|---|---:|---:|:-:|:-:|
| 2023-07 → 2024-07 | 4 | −$546 / $746 | **−$228 / $622** | ✔ | ✔ |
| 2024-07 → 2025-07 | 6 | −$599 / $923 | **−$530 / $890** | ✔ | ✔ |
| 2025-07 → 2026-06 | 8 | $622 / $338 | **$345 / $255** | ✘ (−$277) | ✔ |

ETH cell-selection: **net PASS 2/3, maxDD PASS 3/3** — the selection keeps
re-discovering the same load-bearing cells (`trending|calm|long` Donchian,
`trending|volatile|short` Donchian), and it reliably cuts drawdown, but in the
one profitable OOS year (2025-26) it gives back $277 of net. Not BTC's clean
3/3 + 3/3.

**SOLUSDT:**

| OOS fold | cells authored in-sample | ungated net / maxDD | ev-ml net / maxDD | net | DD |
|---|---|---:|---:|:-:|:-:|
| 2023-07 → 2024-07 | 1 | $292 / $368 | **$89 / $420** | ✘ (−$203) | ✘ |
| 2024-07 → 2025-07 | 3 | $371 / $808 | **$329 / $483** | ✘ (−$42) | ✔ |
| 2025-07 → 2026-06 | 4 | $100 / $538 | **$196 / $597** | ✔ | ✘ |

SOL cell-selection: **net PASS 1/3, maxDD PASS 1/3** — the in-sample-derived
cell sets are small (1/3/4 cells) and unstable across folds (mostly
`sol_pullback_2h` sides that flip sign OOS). The selection does NOT
generalize on SOL.

## Verdict — NEITHER symbol clears the go-live gate; honest negative recorded

The promotion bar (operator-set, the BTC shape): **ev-ml net ≥ ungated net
AND ev-ml maxDD ≤ ungated maxDD in every fold**, plus a generalizing
cell-selection walk-forward.

| | full-history A/B | fixed-cell WF (net / DD) | cell-selection WF (net / DD) | gate |
|---|---|---|---|---|
| **BTC** (2026-06-27, ref) | $353→$1526, DD ↓ | 4/4 / 4/4 | 3/3 / 3/3 | **PASS → live since 06-28** |
| **ETH** | $850→$1374, DD ↓23% | 4/4 / **2/4** | 2/3 / 3/3 | **FAIL** |
| **SOL** | $1831→$1633, DD ↓50% | **2/4** / 4/4 | 1/3 / 1/3 | **FAIL** |

- **ETH:** the gate reliably rescues losing years (both losing OOS folds
  improve on net AND DD) and the same two cells keep being re-discovered
  (`trending|calm|long`, `trending|volatile|short` Donchian) — but the fold-2
  DD regression (+$193) and the −$277 giveback in the one profitable year
  keep it below the bar. The deeper issue is the ETH book itself: 3 of 4
  yearly folds are net-negative ungated — that is a strategy-review problem
  the vol gate cannot fix.
- **SOL:** clean negative. The book is already healthy (ret/DD 1.72, all
  folds profitable); the ≥10t negative cells are small and unstable, and
  gating them costs net in good years. SOL does not need this gate.
- **No Tier-3 bundle is proposed.** `eth-regime-15m-lgbm-v1` /
  `sol-regime-15m-lgbm-v1` stay at **shadow** (accruing soak; also inputs to
  the separate fc program), and no ETH/SOL `trend_vol` cells are authored
  into `config/regime_policy.yaml`. Re-visit when materially more history /
  a retrained head changes the picture, or after the ETH strategy-review
  acts on the underlying book.
- Deliberately NOT done: post-hoc cherry-picking a smaller "better" cell
  subset after seeing the walk-forward — the same overfitting move declined
  in the BTC follow-ups.
