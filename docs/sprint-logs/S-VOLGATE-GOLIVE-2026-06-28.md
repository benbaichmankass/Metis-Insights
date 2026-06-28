# Sprint Log: S-VOLGATE-GOLIVE-2026-06-28

## Date Range
2026-06-28 (single session; continuation of S-ETH-REGIME-RG4-RETRAIN-2026-06-28).

## Objective
Take the Design-A regime ML vol-verdict from a documented placeholder to a real
order-routing influence on real-money `bybit_2` (use+enforce), then work the
flagged follow-ups: the 4h-cell keep/drop decision, gate-fragility hardening of
the regime router, and the SOL regime head — and leave the ETH/SOL graduation
path documented for the soak-gated next step.

## Tier
Mixed. Go-live env flips + the gate-fragility default change are **Tier-3**
(order-routing-affecting, operator-approved). SOL head training + the runbook +
doc corrections are **Tier-1**.

## Starting Context
The vol-gate observe/shadow layer + the 4-arm A/B (strongly positive) had
shipped (PR #4748, 2026-06-27); `btc-regime-15m-lgbm-v2` was at advisory; the
`trend_vol` OFF-cells were authored. `REGIME_ML_VERDICT_MODE=use` was a
documented PLACEHOLDER — the gate still used the frozen vol label.

## Repo State Checked
`src/runtime/intents.py` (gates + flags), `src/runtime/regime/ml_vol_verdict.py`
(advisory resolution), `config/regime_policy.yaml` (`trend_vol` cells),
`scripts/backtest_system.py` (A/B harness), the env-gate / canonical-doc-coherence
CI guards, the trainer fleet + registry via the trainer-vm-diag relay.

## Files and Systems Inspected
intents.py gate loops + `_regime_router_enabled` + `_decision_vol_regime`;
`ml_vol_regime_for_symbol` / `_advisory_entry_for_symbol`; the regime test
suite; `build_trainer_datasets.sh`; the live VM (diag relays) + trainer VM
(trainer-vm-diag relay).

## Work Completed
- **Built the ML-label apply path** (the placeholder → real wiring):
  `intents._decision_vol_regime` substitutes the **per-symbol** advisory head's
  ML vol label into the gate decision under `REGIME_ML_VERDICT_MODE=use`;
  `ml_vol_regime_for_symbol` resolves the advisory head by SYMBOL (BTC → the 15m
  head) so 1h/4h strategy cells resolve the ML label, matching the validated
  A/B; an **ML-only-enforce guard** (`vol_enforced`) only drops a vol cell when
  the label is ML-sourced (never a money-losing frozen fallback). 5 new tests.
- **GO-LIVE on real-money `bybit_2`** (Tier-3, operator-approved): merged #4896
  → deployed `e0d052e` → `REGIME_ML_VERDICT_MODE=use` → soak PASS (live
  `vol_regime_ml=calm` from `btc-regime-15m-lgbm-v2`, per-symbol resolution
  confirmed) → `REGIME_ROUTER_ENABLED=true` → trader healthy → enforce verified.
- **4h-cell decision = KEEP all 4** `trend_vol` cells. The walk-forward
  validated the set (4/4 folds); dropping the weakest post-hoc
  (`squeeze_breakout_4h` calm/short, −$55/30t) is itself overfitting. Flagged it
  as the cell to watch. No config change.
- **Gate-fragility hardening** (PR #4920, Tier-3): `_regime_router_enabled` →
  `_regime_router_active`, **baseline-on + `REGIME_ROUTER_DISABLED`
  kill-switch** (+ legacy explicit `REGIME_ROUTER_ENABLED=0` honoured for
  rollback). Closes the env-drop-silently-reverts class (the netting-guard /
  Ampere-migration failure mode) so the live enforce survives a migration. The
  backtest harness sets `REGIME_ROUTER_DISABLED=1` on non-`on` runs so the A/B
  baseline arm stays shadow-only; 190 regime/intents/backtest tests pass.
- **SOL regime head** (PR #4918, Tier-1): `sol-regime-{5m,15m}-lgbm-v1` (mirror
  the validated BTC/ETH 5m/15m recipe) + SOL 5m/15m in the daily dataset build.
  Trained on the trainer VM; **RG3 PASS — 15m AUC 0.803 (n=30k, strongest of
  the multi-symbol heads), 5m TRUSTWORTHY.** Both at shadow, soaking.
- **soak→advisory runbook** (PR #4924): `docs/runbooks/regime-head-soak-to-advisory.md`
  codifies the deterministic shadow→RG4→A/B→walk-forward→author-cells→promote
  pipeline + a readiness tracker, capturing the load-bearing nuances (RG4 is the
  gate not `live_agreement`; score RG4 at the head's training threshold; the
  pre-drafted ETH cells used the failed 1h head and must be re-derived under the
  15m head; only the 15m head needs advisory).

## Validation Performed
- Go-live each step verified via diag relays (soak agreement row, trader
  heartbeat/CPU, enforce path).
- 190 regime/intents/backtest tests pass locally; env-gate-guard +
  canonical-doc-coherence clean on the diffs.
- SOL RG3 read cleanly (filtered fleet replay): 15m 0.803 / 5m TRUSTWORTHY.

## Documentation Updated
- ROADMAP go-live entry + follow-ups-done update; CLAUDE.md (`REGIME_ROUTER_DISABLED`
  row + per-symbol `REGIME_ML_VERDICT_MODE` correction, in #4920); the runbook;
  `MB-20260628-VOLGATE-GOLIVE` (resolved), `MB-20260628-REGIME-SOAK-READINESS`,
  `BL-20260628-VOLGATE-LIVE-VERIFY`.

## Contradictions or Drift Found
- **Corrected a stale honesty-critical claim:** the earlier go-live note said
  BTC 1h/4h cells "resolve unknown→frozen" and enforce was gated on promoting
  BTC 1h/4h advisory heads. The code resolves **per-symbol**, so BTC cells
  resolve the 15m advisory head and ENFORCE NOW. Fixed in ROADMAP + CLAUDE.md +
  the intents.py docstring (field-beats-comment).

## Risks and Follow-Ups
- **Live verification still open** (`BL-20260628-VOLGATE-LIVE-VERIFY`): no
  `regime_hard_gate` vol_gated:true+ml fire OBSERVED yet (infrequent candidate
  combos, not frozen); verify #4920's deploy left the trader healthy; confirm
  ETH/SOL shadow rows accrue.
- The live VM's `REGIME_ROUTER_ENABLED=true` is now redundant (baseline-on) —
  removable later, harmless to leave.

## Deferred Items
- **ETH/SOL post-soak RG4** (`MB-20260628-REGIME-SOAK-READINESS`, ~2026-07-02):
  time-gated on the heads accruing ≥~300–500 live shadow rows. Then RG4 → vol-split
  A/B under the 15m label → walk-forward → author live cells (T3) → promote 15m →
  advisory (T3), per the runbook.
- Re-derive the pre-drafted ETH `trend_vol` cells under `eth-regime-15m-lgbm-v1`
  (the draft used the failed 1h head).

## Next Recommended Sprint
A data-driven `/system-review` (or `/ml-review`) to confirm the soaks are
accruing, surface any other promote/demote-ready heads, grade recent trades, and
set the next priority from production data.

## Wrap-Up Check
ROADMAP + this sprint log + the three review backlogs all updated; doc-freshness
run (canonical-doc-coherence PASS); PRs #4918/#4920/#4924 auto-merging.
