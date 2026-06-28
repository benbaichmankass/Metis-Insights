#!/usr/bin/env bash
# Fleet model scorecard — read every shadow regime head through BOTH replay-pregate
# stages and emit a keep/fix-skew/kill table.
#
#   RG3 (Stage-1, clean-candle replay, dataset-label parity): does the model
#        DISCRIMINATE the realized regime when fed clean candles through the LIVE
#        feature builder? (necessary)
#   RG4 (Stage-2, logged-live-row replay): re-score the EXACT feature rows the live
#        runtime logged to shadow_predictions.jsonl — a per-stage AUC collapse vs
#        Stage-1 is train/serve SKEW (the btc-regime-yz failure mode).
#
# Tier-1 research tooling: reads datasets / registry / the shadow log, writes a
# text table to stdout. Never touches the order path. Designed to run on the
# trainer VM (where the datasets + registry live), ideally as a background job
# (nohup) since the full candle-replay outlives a single SSH session.
#
# Usage:  bash scripts/ml/fleet_scorecard.sh [MAX_BARS]   (default 8000)
set -uo pipefail
cd "$(dirname "$0")/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
MAXBARS="${1:-8000}"

echo "== RG3 Stage-1 fleet (clean-candle discrimination; max_bars=$MAXBARS) =="
PYTHONPATH=. "$PY" scripts/ml/replay_pregate_fleet.py --max-bars "$MAXBARS" --json /tmp/rg3.json \
  >/dev/null 2>/tmp/rg3.err || { echo "RG3 FAILED"; tail -8 /tmp/rg3.err; }
"$PY" -c "import json;d=json.load(open('/tmp/rg3.json'));print('heads scored:',d.get('n_scored'),'/',d.get('n_models'));[print('  S1',r['model_id'],r['symbol']+'/'+r['timeframe'],'auc='+str((r.get('overall') or {}).get('auc')),r.get('auc_verdict'),'n='+str(r.get('n_scored'))) for r in d.get('results',[])];e=d.get('errors',[]);print('  errors:',[(x.get('model_id'),x.get('error')) for x in e]) if e else None" 2>/dev/null || echo "RG3 parse failed"

SL=""
for c in runtime_logs/shadow_predictions.jsonl \
         runtime_logs/trainer_mirror/shadow_predictions.jsonl \
         runtime_logs/trainer_mirror/live/shadow_predictions.jsonl; do
  [ -f "$c" ] && SL="$c" && break
done
echo "== shadow_log=${SL:-NONE} rows=$([ -n "$SL" ] && wc -l < "$SL" || echo 0) =="

echo "== RG4 Stage-2 per head (train/serve skew) =="
if [ -n "$SL" ]; then
  for row in $("$PY" -c "import json;d=json.load(open('/tmp/rg3.json'));print(' '.join(r['model_id']+'|'+r['symbol']+'|'+r['timeframe'] for r in d.get('results',[])))" 2>/dev/null); do
    mid="${row%%|*}"; rest="${row#*|}"; sym="${rest%%|*}"; tf="${rest##*|}"
    cf=$(ls -1 datasets-out/market_raw/"$sym"/"$tf"/*/data.jsonl 2>/dev/null | sort | tail -1)
    [ -z "$cf" ] && { echo "  S2 $mid: no candles for $sym/$tf"; continue; }
    PYTHONPATH=. "$PY" scripts/ml/replay_pregate_live.py --model-id "$mid" \
      --shadow-log "$SL" --candles "$cf" --json /tmp/rg4.json >/dev/null 2>/dev/null
    "$PY" -c "import json;d=json.load(open('/tmp/rg4.json'));bs=d.get('by_stage',{});print('  S2 $mid recs='+str(d.get('n_records')),'unlab='+str(d.get('n_unlabeled')),{s:(v.get('auc'),v.get('verdict'),'mf='+str(v.get('has_market_features'))) for s,v in bs.items()})" 2>/dev/null || echo "  S2 $mid: no records / failed"
  done
else
  echo "  RG4 skipped — live shadow_predictions.jsonl not present on this host"
fi
echo "== SCORECARD DONE =="
