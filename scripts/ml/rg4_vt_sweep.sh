#!/usr/bin/env bash
# RG4 vol_threshold sweep — re-score logged-live rows for a regime head across a
# set of vol_threshold values (MB-20260627-003).
#
# WHY: replay_pregate_live.py defaults vol_threshold=0.003, but the Bybit
# dataset build (scripts/ops/build_trainer_datasets.sh::build_bybit_pair) labels
# regime_label at vol_threshold=0.005. RG4 must score a head against the SAME
# label definition it trained on, else a good head reads NO_EDGE purely from the
# threshold mismatch (the eth-regime-1h-lgbm-v1 0.46->0.58 finding). This sweep
# exposes the sensitivity so the matched-threshold RG4 is the one of record.
#
# Trainer-side, read-only. Usage:
#   bash scripts/ml/rg4_vt_sweep.sh "mid|SYM|tf|bar_seconds" vt1 vt2 ...
#   e.g. bash scripts/ml/rg4_vt_sweep.sh "eth-regime-1h-lgbm-v1|ETHUSDT|1h|3600" 0.003 0.004 0.005 0.006
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
export PYTHONPATH=.

spec="$1"; shift
mid="${spec%%|*}"; rest="${spec#*|}"; sym="${rest%%|*}"; rest="${rest#*|}"
tf="${rest%%|*}"; bs="${rest##*|}"
SL=""
for c in runtime_logs/shadow_predictions.jsonl \
         runtime_logs/trainer_mirror/shadow_predictions.jsonl; do
  [ -f "$c" ] && SL="$c" && break
done
cf=$(ls -1 datasets-out/market_raw/"$sym"/"$tf"/*/data.jsonl 2>/dev/null | sort | tail -1)
echo "== $mid ($sym/$tf bs=$bs) shadow=$SL candles=$cf =="
if [ -z "$SL" ] || [ -z "$cf" ]; then echo "  missing shadow log or candles"; exit 0; fi

for vt in "$@"; do
  "$PY" scripts/ml/replay_pregate_live.py --model-id "$mid" --shadow-log "$SL" \
    --candles "$cf" --vol-threshold "$vt" --bar-seconds "$bs" \
    --json "/tmp/rg4sw_${mid}.json" >/dev/null 2>/tmp/rg4sw.err \
    || { echo "  vt=$vt replay failed"; continue; }
  printf "  vt=%s " "$vt"
  "$PY" scripts/ml/_rg4_print.py "/tmp/rg4sw_${mid}.json" "$mid" 2>/dev/null \
    || echo "(no records)"
done
echo "RG4_VT_SWEEP_DONE"
