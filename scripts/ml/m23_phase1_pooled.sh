#!/usr/bin/env bash
# M23 Phase 1 — POOLED multi-strategy backtest-augmented meta-label experiment.
#
# The population-matched FAIR TEST (backlog MB-20260716-M23-P1-POPMATCH). The first
# leg (m23_phase1_experiment.sh) trained the meta-label model on ONE strategy's
# backtest (trend_donchian) but evaluated on the ALL-strategy real journal holdout —
# a distribution shift by construction. This pools the roster's backtest trades so
# TRAIN and EVAL span the same multi-strategy population:
#   1. market_raw BTCUSDT/1h -> 1h OHLCV CSV; RESAMPLE it to 2h + 4h CSVs (leakage-
#      free OHLCV downsample: open=first, high=max, low=min, close=last, vol=sum).
#   2. per-strategy harness --emit-trades over the deep history at its own timeframe:
#        trend_donchian      -> backtest_trend.py     @1h
#        squeeze_breakout_4h -> backtest_squeeze.py   @4h
#        htf_pullback_trend_2h -> backtest_pullback.py @2h
#      (roster is data-driven below; add a line to extend it.)
#   3. record ALL strategies' harness trades into ONE temp db (is_backtest=1).
#   4. build setup_candidates (POOLED backtest-train + real live_holdout eval).
#   5. train setup-candidates-metalabel-backtest-v1 (live_holdout eval).
#   6. gate verdict: accuracy vs 0.756 majority + precision lift off 0.244 base rate.
#
# Tier-1 / offline / trainer-side. Writes ONLY is_backtest=1 rows into a TEMP db
# (never the money journal). No src/, config/, order-path, or registry write.
# The live journal must already be present (pulled read-only via sync_trainer_data.sh).
set -uo pipefail

REPO="${REPO:-/home/ubuntu/ict-trading-bot}"
cd "$REPO"
MARKET_RAW_1H="${MARKET_RAW_1H:-datasets-out/market_raw/BTCUSDT/1h/v002}"
LIVE_JOURNAL="${LIVE_JOURNAL:-data/trade_journal.db}"
BT_DB="${BT_DB:-/tmp/m23_pooled_backtest.db}"
# NB: the setup-candidates-metalabel-backtest-v1 manifest PINS dataset.version: v001,
# and `ml train` reads the version the MANIFEST declares (not what we build). So the
# pooled build MUST overwrite v001 or the train silently re-uses the old single-strategy
# v001 (the first-leg dataset) and returns identical metrics. Build at v001.
SC_VERSION="${SC_VERSION:-v001}"
SYMBOL="${SYMBOL:-BTCUSDT}"
RUN_TAG="${RUN_TAG:-m23-pooled-$(date +%Y%m%d 2>/dev/null || echo 20260717)}"
CSV_1H=/tmp/m23_btc_1h.csv
CSV_2H=/tmp/m23_btc_2h.csv
CSV_4H=/tmp/m23_btc_4h.csv
RESULT=/tmp/m23_pooled_result.txt

# roster: "strategy|harness|timeframe|csv"
ROSTER=(
  "trend_donchian|scripts/backtest_trend.py|1h|$CSV_1H"
  "squeeze_breakout_4h|scripts/backtest_squeeze.py|4h|$CSV_4H"
  "htf_pullback_trend_2h|scripts/backtest_pullback.py|2h|$CSV_2H"
)

: > "$RESULT"
log() { echo "[$(date -u +%H:%M:%S 2>/dev/null || echo m23)] $*" | tee -a "$RESULT"; }
source .venv/bin/activate 2>/dev/null || log "WARN: no .venv"

log "STEP 1: market_raw ($MARKET_RAW_1H) -> 1h CSV, resample -> 2h + 4h"
python3 - "$MARKET_RAW_1H/data.jsonl" "$CSV_1H" "$CSV_2H" "$CSV_4H" <<'PY' 2>>"$RESULT"
import json, sys, csv
src, d1, d2, d4 = sys.argv[1:5]
rows = []
with open(src) as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        ts = r.get("ts") or r.get("timestamp") or r.get("time")
        rows.append((int(ts) if str(ts).isdigit() else ts,
                     float(r["open"]), float(r["high"]), float(r["low"]),
                     float(r["close"]), float(r.get("volume") or 0.0)))
rows.sort(key=lambda x: x[0])
cols = ["timestamp", "open", "high", "low", "close", "volume"]
def write(path, data):
    with open(path, "w", newline="") as out:
        w = csv.writer(out); w.writerow(cols)
        for x in data:
            w.writerow(list(x))
write(d1, rows)
# Resample 1h -> Nh by grouping consecutive N bars (deep history is contiguous 1h).
# OHLCV downsample: open=first, high=max, low=min, close=last, volume=sum. The bar
# timestamp is the FIRST 1h ts of the group (bar-open convention, past-only -> no leak).
def resample(data, n):
    out = []
    for i in range(0, len(data) - n + 1, n):
        g = data[i:i+n]
        out.append((g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g),
                    g[-1][4], sum(x[5] for x in g)))
    return out
write(d2, resample(rows, 2))
write(d4, resample(rows, 4))
print("csv rows: 1h", len(rows), "2h", (len(rows)//2), "4h", (len(rows)//4))
PY
log "  1h=$(wc -l < "$CSV_1H" 2>/dev/null)  2h=$(wc -l < "$CSV_2H" 2>/dev/null)  4h=$(wc -l < "$CSV_4H" 2>/dev/null)"

log "STEP 2: per-strategy harness replay -> per-strategy JSONL"
declare -a RECORD_ARGS=()
for entry in "${ROSTER[@]}"; do
  IFS='|' read -r strat harness tf csv <<< "$entry"
  jsonl="/tmp/m23_${strat}_trades.jsonl"
  log "  ${strat}: ${harness} @${tf}"
  python3 "$harness" --data "$csv" --timeframe "$tf" --symbol "$SYMBOL" \
    --emit-trades "$jsonl" --json "/tmp/m23_${strat}_summary.json" >>"$RESULT" 2>&1 \
    || log "    WARN: ${strat} harness returned nonzero (see above)"
  n=$(wc -l < "$jsonl" 2>/dev/null || echo 0)
  log "    emitted: $n"
  [ "${n:-0}" != "0" ] && RECORD_ARGS+=(--trades-jsonl "${jsonl}=${strat}")
done
if [ "${#RECORD_ARGS[@]}" = "0" ]; then log "ABORT: no harness emitted trades"; echo '{"m23_pooled_done":false,"error":"no_harness_trades"}' >> "$RESULT"; exit 1; fi

log "STEP 3: record ALL harness trades -> $BT_DB (is_backtest=1)"
rm -f "$BT_DB"
python3 - "$LIVE_JOURNAL" "$BT_DB" <<'PY' 2>>"$RESULT"
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
row = s.execute("select sql from sqlite_master where type='table' and name='trades'").fetchone()
if not row:
    print("SCHEMA_SEED_ERR: no trades table in journal"); sys.exit(1)
d = sqlite3.connect(dst); d.execute(row[0]); d.commit(); d.close(); s.close()
print("seeded trades schema into temp db")
PY
python3 -m scripts.ml.record_harness_trades --db "$BT_DB" --symbol "$SYMBOL" \
  "${RECORD_ARGS[@]}" --run-tag "$RUN_TAG" >>"$RESULT" 2>&1
NBT=$(python3 - "$BT_DB" <<'PY' 2>>"$RESULT"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("select count(*) from trades where is_backtest=1").fetchone()[0])
for r in c.execute("select strategy, count(*) from trades where is_backtest=1 group by strategy"):
    print("  ", r[0], r[1], file=sys.stderr)
PY
)
log "  backtest rows recorded (pooled): $NBT"
if [ "${NBT:-0}" = "0" ]; then log "ABORT: 0 backtest rows"; echo '{"m23_pooled_done":false,"error":"no_backtest_rows"}' >> "$RESULT"; exit 1; fi

log "STEP 4: build setup_candidates ($SC_VERSION) pooled backtest-train + real-eval"
python3 -m ml.datasets build setup_candidates --output-dir datasets-out \
  --version "$SC_VERSION" --source market_raw --symbol-scope "$SYMBOL" --timeframe all --overwrite -- \
  "market_raw_path=$MARKET_RAW_1H" "backtest_trades_db=$BT_DB" \
  "live_trades_db=$LIVE_JOURNAL" "include_cusum=false" >>"$RESULT" 2>&1
python3 - "datasets-out/setup_candidates/$SYMBOL/all/$SC_VERSION/data.jsonl" <<'PY' 2>&1 | tee -a "$RESULT"
import json, sys, collections
try:
    es = collections.Counter(); lv = collections.Counter(); won = collections.Counter()
    n = 0
    for line in open(sys.argv[1]):
        line = line.strip()
        if not line: continue
        r = json.loads(line); n += 1
        es[r.get("event_source")] += 1
        lv[bool(r.get("is_live_trade"))] += 1
        won[r.get("won")] += 1
    print("setup_candidates rows:", n, "by_source:", dict(es), "is_live:", dict(lv), "won:", dict(won))
except Exception as e:
    print("setup_candidates read err:", e)
PY

log "STEP 5: train setup-candidates-metalabel-backtest-v1 (live_holdout eval)"
python3 -m ml train ml/configs/setup-candidates-metalabel-backtest-v1.yaml \
  --datasets-root datasets-out --no-register > /tmp/m23_pooled_train.out 2>>"$RESULT"
log "STEP 6: GATE VERDICT"
python3 - /tmp/m23_pooled_train.out <<'PY' 2>>"$RESULT" | tee -a "$RESULT"
import json, sys
txt = open(sys.argv[1]).read()
dec = json.JSONDecoder(); objs = []; i = 0
while True:
    b = txt.find("{", i)
    if b < 0: break
    try:
        o, e = dec.raw_decode(txt, b); objs.append(o); i = e
    except Exception:
        i = b + 1
cand = [o for o in objs if isinstance(o, dict) and "metrics" in o]
if not cand:
    print("NO_TRAIN_SUMMARY; tail:", txt[-500:]); raise SystemExit
m = cand[-1]["metrics"]
acc = m.get("accuracy"); prec = m.get("precision")
print("METRICS:", json.dumps({k: m[k] for k in m if isinstance(m[k], (int, float))}))
MAJ, BASE = 0.756, 0.244
if acc is not None and prec is not None:
    beats_maj = acc > MAJ; lifts_prec = prec > BASE
    verdict = "PASS" if (beats_maj and lifts_prec) else "FAIL"
    print(f"GATE: accuracy={acc:.4f} vs majority {MAJ} -> {'beat' if beats_maj else 'below'}; "
          f"precision={prec:.4f} vs base-rate {BASE} -> {'lift' if lifts_prec else 'no lift'}; VERDICT={verdict}")
else:
    print("GATE: accuracy/precision missing from metrics:", list(m))
PY

echo '{"m23_pooled_done":true}' >> "$RESULT"
log "DONE — full result in $RESULT"
