# ict_scalp_5m — Phase-4 regime-gate re-promotion packet (2026-07-20)

**Status:** Tier-3 PROPOSAL awaiting explicit operator approval — nothing in
this packet is applied. Follows the Phase-0 gate verdict
(`ict_scalp_5m-phase0-findings-2026-07-20.md`) and the operator's 2026-07-20
"push forward with the proposed path" directive, which per the path's own
discipline required k-fold OOS validation before any config change.
Owner item: `PB-20260630-ICTSCALP-DEGRADE`.

## What is proposed (exact changes)

**Change 1 — author two 2-D OFF cells** in `config/regime_policy.yaml`
(`trend_vol` block; same shape as the existing Design-A cells):

```yaml
trend_vol:
  trending:
    volatile:
      ict_scalp_5m:        { long: off, short: off }   # net-of-fee bleed cell (Phase-0)
  chop:
    volatile:
      ict_scalp_5m:        { long: off, short: off }   # worst net cell (Phase-0)
```

**Change 2 — re-promote** `config/strategies.yaml::ict_scalp_5m.execution:
shadow → live`, WITH change 1 landed first as the protection. Routing/account
placement per the operator (previously bybit_2 real + bybit_1/bybit_portfolio
paper); `account_compat_matrix` re-run required if the account set changes.

**Deliberately NOT proposed now:** `min_confidence: 0.7` — it failed the
fold-robustness bar (see below). It stays a Phase-2/3 candidate.

## Evidence

Anchored walk-forward, 4 OOS folds over the Run B (live-exit-faithful)
regime-stamped walk, net of 7.5bps round-trip
(`scripts/research/ict_scalp_phase0/kfold_oos.py`,
artifact `docs/research/artifacts/ict_scalp_phase0/kfold_runB.json`):

| rule (OOS, net) | folds + | n | tot R | exp R |
|---|---|---|---|---|
| unfiltered baseline | 2/4 | 528 | −6.3 | −0.012 |
| **OFF cells (5m frozen label)** | **3/4** | 243 | **+20.3** | **+0.083** |
| **OFF cells (15m frozen label — live-enforcement proxy)** | **3/4** | 256 | **+29.1** | **+0.114** |
| OFF cells + conf≥0.7 | 3/4 | 150 | +13.5 | +0.090 |
| calm-only (5m / 15m) | 2/4 / 2/4 | 102 / 123 | +8.3 / +12.1 | +0.081 / +0.099 |
| fitted min_confidence (M8-style per-fold selection) | 2/4 | 223 | +7.0 | +0.031 |

- The OFF-cells rule is the most fold-robust candidate and holds under BOTH
  vol-label sources. The one negative fold (Nov-2024→Jul-2025) is small
  (−5.9R / −2.4R) against +12.8/+1.1/+12.2 (5m) and +15.1/+0.1/+16.2 (15m).
- No rule passes 4/4 — fold 3 is a near-flat regime for the strategy under
  every filter (baseline −0.4R). Stated plainly rather than hidden.
- min_confidence: the per-fold-selected threshold is unstable (0.8/0.75/0.7)
  and only 2/4 folds positive → not proposed.

## Label-source note (read before approving)

The live 2-D gate evaluates the vol axis via the **ML 15m vol verdict**
(`REGIME_ML_VERDICT_MODE=use`, per-symbol BTC advisory head) — not the 5m
frozen edges used for Phase-0 attribution. The 15m-frozen-edge robustness row
above is the closest offline proxy for that label (the ML head's vol classes
are defined by those frozen buckets); the true ML-label counterfactual can't
be replayed offline from this sandbox. Both proxies agree on sign and
robustness. Precedent: the existing Design-A cells were likewise validated
against a label proxy before the enforce flip.

## Rollback / blast radius

- Change 1 alone is a pure trade-suppressor for ict_scalp_5m in two cells —
  while the strategy is still `shadow` it only changes would-gate logging;
  it has no effect on any other strategy.
- After change 2, rollback is one Tier-3 revert of `execution: live → shadow`
  (the same lever used on 2026-07-14), or `REGIME_ROUTER_DISABLED` for the
  gate layer itself (affects all strategies — not the preferred lever).
- First-live-fire monitoring: M20 P7 first-decision health check applies.

## Conditions attached (from Phase-0 caveats)

1. Backtest evidence ends 2026-02-28; May–Jul 2026 live window is unmodeled.
2. `BL-20260720-ICTSCALP-PASTSTOP-EXITS` (P1) should be dispositioned
   **before real-money re-routing**. Classified 2026-07-20: the Jun 21–22
   positions ran ~24h+ with **no effective bracket on the exchange** (both
   SL and TP levels crossed without executing; bulk-flattened Jun 23 at
   worse-than-either prices). Until the mechanism is found and fixed, the
   backtest's assumption that a stop is a stop does not hold on this
   account under stress — this is the strongest single condition on
   change 2.
3. Fee-load work (Phase 2: wider-stop / maker-entry / higher-TF variants)
   remains the highest-value follow-up; this gate makes the strategy modestly
   net-positive, not good.
