#!/usr/bin/env bash
# Walk-forward robustness check for the Design-A EVIDENCE trend_vol OFF-cells.
#
# The aggregate confirmation A/B (docs/research/A-vol-gating-OFFcell-design-2026-06-27.md)
# showed evidence-cells + the ML vol label lift the full-history BTC book 4.3x
# ($353 -> $1526) while trimming maxDD, and that the SAME cells under the frozen
# label lose money. But the evidence cells were AUTHORED from the full-history
# per-cell net-PnL split, so the aggregate result is in-sample by construction.
#
# This script is the overfit check: apply the FIXED evidence policy across
# CONSECUTIVE, non-overlapping BTC windows and verify, per fold —
#   (1) evidence-ML-gated net >= ungated net           (the cells help, not hurt)
#   (2) evidence-ML-gated maxDD <= ungated maxDD        (no DD regression — FLIP bar)
#   (3) evidence-ML-gated net  >  evidence-frozen net   (ML label is the edge OOS)
# holds out-of-sample, not just in the pooled run. Same acceptance shape the
# FLIP_POLICY walk-forward used before that live flip.
#
# Tier-1 research tooling — never touches the live order path. Reads the registry
# (ML_REGISTRY_ROOT) + the evidence policy; runs the existing harness per fold.
#
#   bash scripts/ml/walkforward_evidence_cells.sh [DATA_CSV] [EVIDENCE_POLICY_YAML]
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done

DATA="${1:-data/backtest_BTCUSDT_5m.csv}"
EV="${2:-docs/research/regime_policy_trend_vol_evidence-2026-06-27.yaml}"
ROSTER="trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h"
MID="btc-regime-15m-lgbm-v2"
export PYTHONPATH=.
export ML_REGISTRY_ROOT="${ML_REGISTRY_ROOT:-ml/registry-store}"

# Consecutive yearly folds (non-overlap = an implicit purge between folds).
FOLDS="2022-07-01:2023-07-01 2023-07-01:2024-07-01 2024-07-01:2025-07-01 2025-07-01:2026-06-01"

run_arm() {  # $1=label $2..=extra flags ; reads $S/$E ; prints net+maxDD line
  local label="$1"; shift
  local line
  line=$("$PY" scripts/backtest_system.py --data "$DATA" --roster "$ROSTER" \
           --start "$S" --end "$E" "$@" \
           2>/dev/null | grep -m1 -E "net=")
  echo "  ${label}: ${line:-<no-output>}"
}

echo "== walk-forward EVIDENCE trend_vol OFF-cells, BTC =="
echo "data=$DATA  policy=$EV  head=$MID"
for f in $FOLDS; do
  S="${f%%:*}"; E="${f##*:}"
  echo "== fold ${S} .. ${E} =="
  run_arm "ungated  "
  run_arm "ev-frozen" --regime-router on --regime-policy "$EV" --vol-verdict frozen
  run_arm "ev-ml    " --regime-router on --regime-policy "$EV" --vol-verdict ml --ml-model-id "$MID"
done
echo WF_EV_DONE
