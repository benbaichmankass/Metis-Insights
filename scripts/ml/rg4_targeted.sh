#!/usr/bin/env bash
# Targeted RG4 (Stage-2 logged-live-row replay) for a specific set of regime
# heads — the train/serve SKEW gate. Re-scores the EXACT feature rows the live
# runtime logged to shadow_predictions.jsonl: a per-stage AUC collapse vs the
# clean-candle RG3 number is skew (the btc-regime-yz / mes-yz failure mode).
#
# Generalises the RG4 loop in fleet_scorecard.sh to a caller-supplied head list,
# so we can gate just the ETH/MES multi-symbol-A candidates without re-scoring
# the whole 25-head fleet. Tier-1 research: reads the registry + the mirrored
# live shadow log + datasets, writes a text table. Never touches the order path.
#
# Usage:  bash scripts/ml/rg4_targeted.sh "mid|SYM|tf" "mid2|SYM2|tf2" ...
#   e.g.  bash scripts/ml/rg4_targeted.sh \
#           "mes-regime-15m-lgbm-v2|MES|15m" "eth-regime-1h-lgbm-v1|ETHUSDT|1h"
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
export PYTHONPATH=.

# Locate the live shadow log (mirrored onto the trainer).
_find_shadow_log() {
  SL=""
  for c in runtime_logs/shadow_predictions.jsonl \
           runtime_logs/trainer_mirror/shadow_predictions.jsonl \
           runtime_logs/trainer_mirror/live/shadow_predictions.jsonl; do
    [ -f "$c" ] && SL="$c" && break
  done
}
_log_age_min() {  # minutes since the shadow log's last mtime, or empty
  [ -n "${SL:-}" ] && [ -f "$SL" ] && echo $(( ( $(date +%s) - $(stat -c %Y "$SL") ) / 60 )) || echo ""
}

_find_shadow_log

# MIRROR FRESHNESS GATE (MB-20260705-FC-ADVISORY-READINESS / D4a): the
# 2026-07-04 fc first-look read a ~19h-stale mirror and produced a
# noise-verdict. An RG4 read on a stale mirror is not evidence — so when the
# log is older than RG4_MAX_AGE_MIN (default 90) and the live→trainer sync
# script is present, refresh it first (best-effort; RG4_NO_SYNC=1 skips).
# Either way the age is printed loudly so a stale read can never pass silently.
RG4_MAX_AGE_MIN="${RG4_MAX_AGE_MIN:-90}"
AGE_MIN="$(_log_age_min)"
if [ -n "${SL:-}" ] && [ -n "$AGE_MIN" ] && [ "$AGE_MIN" -gt "$RG4_MAX_AGE_MIN" ] \
   && [ -z "${RG4_NO_SYNC:-}" ] && [ -x scripts/ops/sync_trainer_data.sh ]; then
  echo "== shadow_log is ${AGE_MIN}min old (> ${RG4_MAX_AGE_MIN}min) — syncing from live VM =="
  bash scripts/ops/sync_trainer_data.sh >/tmp/rg4_sync.log 2>&1 \
    || echo "== WARN: sync_trainer_data.sh failed (see /tmp/rg4_sync.log) — reading the STALE mirror =="
  _find_shadow_log
  AGE_MIN="$(_log_age_min)"
fi

echo "== shadow_log=${SL:-NONE} rows=$([ -n "${SL:-}" ] && wc -l < "$SL" || echo 0) age_min=${AGE_MIN:-n/a} =="
if [ -n "$AGE_MIN" ] && [ "$AGE_MIN" -gt "$RG4_MAX_AGE_MIN" ]; then
  echo "== WARN: MIRROR IS STALE (${AGE_MIN}min) — treat every verdict below as UNPOWERED =="
fi
if [ -z "${SL:-}" ]; then
  echo "RG4 cannot run — no live shadow_predictions.jsonl on this host"
  echo "RG4_TARGETED_DONE"
  exit 0
fi

for spec in "$@"; do
  mid="${spec%%|*}"; rest="${spec#*|}"; sym="${rest%%|*}"; tf="${rest##*|}"
  cf=$(ls -1 datasets-out/market_raw/"$sym"/"$tf"/*/data.jsonl 2>/dev/null | sort | tail -1)
  if [ -z "$cf" ]; then
    echo "  $mid ($sym/$tf): no candles"
    continue
  fi
  "$PY" scripts/ml/replay_pregate_live.py --model-id "$mid" \
    --shadow-log "$SL" --candles "$cf" --json "/tmp/rg4_${mid}.json" \
    >/dev/null 2>>/tmp/rg4_targeted.err || { echo "  $mid: replay failed"; continue; }
  "$PY" scripts/ml/_rg4_print.py "/tmp/rg4_${mid}.json" "$mid" 2>/dev/null \
    || echo "  $mid: no records / parse failed"
done
echo "RG4_TARGETED_DONE"
