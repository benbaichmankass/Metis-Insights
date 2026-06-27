#!/usr/bin/env bash
# Walk-forward robustness check for the Design-A vol-gating A/B: frozen-vol vs
# ML-vol across CONSECUTIVE, non-overlapping BTC time windows. The aggregate
# in-sample A/B (docs/research/A-vol-gating-AB-evidence-2026-06-27.md) showed
# ML-vol >> frozen-vol; this checks that ordering holds out-of-sample per window
# (the FLIP_POLICY-style acceptance bar), so the result isn't one period's luck.
#
# Tier-1 research tooling — never touches the live order path. Reads the registry
# (ML_REGISTRY_ROOT) + the candidate policy; runs the existing harness per fold.
#
#   bash scripts/ml/walkforward_vol_gating.sh [DATA_CSV] [CANDIDATE_POLICY_YAML]
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done

DATA="${1:-data/backtest_BTCUSDT_5m.csv}"
CAND="${2:-docs/research/regime_policy_trend_vol_candidate-2026-06-27.yaml}"
ROSTER="trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h"
MID="btc-regime-15m-lgbm-v2"
export PYTHONPATH=.
export ML_REGISTRY_ROOT="${ML_REGISTRY_ROOT:-ml/registry-store}"

# Consecutive yearly folds (non-overlap = an implicit purge between folds).
FOLDS="2022-07-01:2023-07-01 2023-07-01:2024-07-01 2024-07-01:2025-07-01 2025-07-01:2026-06-01"

run_arm() {  # $1=label $2..=extra flags ; reads $S/$E
  local label="$1"; shift
  local line
  line=$("$PY" scripts/backtest_system.py --data "$DATA" --roster "$ROSTER" \
           --regime-router on --regime-policy "$CAND" --start "$S" --end "$E" "$@" \
           2>/dev/null | grep -m1 "net=")
  echo "  ${label}: ${line:-<no-output>}"
}

echo "== walk-forward vol-gating A/B (frozen vs ML), BTC, candidate trend_vol cells =="
echo "data=$DATA  policy=$CAND  head=$MID"
for f in $FOLDS; do
  S="${f%%:*}"; E="${f##*:}"
  echo "== fold ${S} .. ${E} =="
  run_arm "frozen" --vol-verdict frozen
  run_arm "ml    " --vol-verdict ml --ml-model-id "$MID"
done
echo WF_DONE
