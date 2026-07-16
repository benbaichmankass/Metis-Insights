#!/usr/bin/env bash
# M23 Phase 1 — in-distribution backtest-augmented meta-label experiment (first leg).
#
# Runs the full chain the `setup-candidates-metalabel-backtest-v1` manifest needs,
# end-to-end, on the trainer VM:
#   1. market_raw jsonl -> OHLCV CSV (rename canonical `ts` -> `timestamp`).
#   2. scripts/backtest_trend.py over deep 1h history -> per-trade JSONL (--emit-trades).
#   3. scripts/ml/record_harness_trades.py -> is_backtest=1 rows in a TEMP db.
#   4. python -m ml.datasets build setup_candidates (backtest-train + real-eval).
#   5. python -m ml train setup-candidates-metalabel-backtest-v1 (live_holdout eval).
#   6. print the gate verdict: accuracy vs the 0.756 majority baseline + precision
#      lift off the 0.244 real-trade base rate.
#
# Tier-1 / offline / trainer-side. Writes ONLY is_backtest=1 rows into a TEMP db
# (never the money journal). No src/, config/, order-path, or registry write.
# The live journal must already be present (pulled read-only via sync_trainer_data.sh).
#
# MB-20260701 lesson: this is a committed script, not an inline relay heredoc.
set -uo pipefail

REPO="${REPO:-/home/ubuntu/ict-trading-bot}"
cd "$REPO"
MARKET_RAW="${MARKET_RAW:-datasets-out/market_raw/BTCUSDT/1h/v002}"
LIVE_JOURNAL="${LIVE_JOURNAL:-data/trade_journal.db}"
BT_DB="${BT_DB:-/tmp/m23_backtest.db}"
SC_VERSION="${SC_VERSION:-v001}"
SYMBOL="${SYMBOL:-BTCUSDT}"
RUN_TAG="${RUN_TAG:-m23-phase1-$(date +%Y%m%d 2>/dev/null || echo 20260716)}"
CSV=/tmp/m23_btc_1h.csv
TRADES_JSONL=/tmp/m23_trend_trades.jsonl
RESULT=/tmp/m23_phase1_result.txt

: > "$RESULT"
log() { echo "[$(date -u +%H:%M:%S 2>/dev/null || echo m23)] $*" | tee -a "$RESULT"; }

source .venv/bin/activate 2>/dev/null || log "WARN: no .venv"

log "STEP 1: market_raw ($MARKET_RAW) -> CSV"
python3 - "$MARKET_RAW/data.jsonl" "$CSV" <<'PY' 2>>"$RESULT"
import json, sys, csv
src, dst = sys.argv[1], sys.argv[2]
cols = ["timestamp", "open", "high", "low", "close", "volume"]
n = 0
with open(src) as fh, open(dst, "w", newline="") as out:
    w = csv.writer(out); w.writerow(cols)
    for line in fh:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        ts = r.get("ts") or r.get("timestamp") or r.get("time")
        w.writerow([ts, r.get("open"), r.get("high"), r.get("low"),
                    r.get("close"), r.get("volume", "")])
        n += 1
print("csv_rows", n)
PY
log "  CSV rows: $(wc -l < "$CSV" 2>/dev/null)"

log "STEP 2: trend_donchian harness -> $TRADES_JSONL (deep 1h)"
python3 scripts/backtest_trend.py --data "$CSV" --timeframe 1h --symbol "$SYMBOL" \
  --emit-trades "$TRADES_JSONL" --json /tmp/m23_trend_summary.json >>"$RESULT" 2>&1
log "  harness trades emitted: $(wc -l < "$TRADES_JSONL" 2>/dev/null)"

log "STEP 3: record harness trades -> $BT_DB (is_backtest=1)"
rm -f "$BT_DB"
# write_backtest_trades requires the `trades` table to PRE-EXIST (schema-adaptive
# INSERT reads PRAGMA table_info). Seed the temp db with the live journal's exact
# trades schema (CREATE TABLE only — no data copy) so the NOT-NULL-aware INSERT works.
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
  --trades-jsonl "$TRADES_JSONL=trend_donchian" --run-tag "$RUN_TAG" >>"$RESULT" 2>&1
NBT=$(python3 - "$BT_DB" <<'PY' 2>>"$RESULT"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); print(c.execute("select count(*) from trades where is_backtest=1").fetchone()[0])
PY
)
log "  backtest rows recorded: $NBT"
if [ "${NBT:-0}" = "0" ]; then log "ABORT: 0 backtest rows recorded (step 3 failed)"; echo '{"m23_phase1_done":false,"error":"no_backtest_rows"}' >> "$RESULT"; exit 1; fi

log "STEP 4: build setup_candidates ($SC_VERSION) backtest-train + real-eval"
python3 -m ml.datasets build setup_candidates --output-dir datasets-out \
  --version "$SC_VERSION" --source market_raw --symbol-scope "$SYMBOL" --timeframe all --overwrite -- \
  "market_raw_path=$MARKET_RAW" "backtest_trades_db=$BT_DB" \
  "live_trades_db=$LIVE_JOURNAL" "include_cusum=false" >>"$RESULT" 2>&1
python3 - "datasets-out/setup_candidates/$SYMBOL/all/$SC_VERSION/data.jsonl" <<'PY' 2>>"$RESULT"
import json, sys, collections
try:
    src = sys.argv[1]
    es = collections.Counter(); lv = collections.Counter(); won = collections.Counter()
    n = 0
    for line in open(src):
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
  --datasets-root datasets-out --no-register > /tmp/m23_train.out 2>>"$RESULT"
log "STEP 6: GATE VERDICT"
python3 - /tmp/m23_train.out <<'PY' 2>>"$RESULT" | tee -a "$RESULT"
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
acc = m.get("accuracy"); prec = m.get("precision"); rec = m.get("recall"); f1 = m.get("f1")
print("METRICS:", json.dumps({k: m[k] for k in m if isinstance(m[k], (int, float))}))
MAJ, BASE = 0.756, 0.244
verdict = "n/a"
if acc is not None and prec is not None:
    beats_maj = acc > MAJ
    lifts_prec = prec > BASE
    verdict = "PASS" if (beats_maj and lifts_prec) else "FAIL"
    print(f"GATE: accuracy={acc:.4f} vs majority {MAJ} -> {'beat' if beats_maj else 'below'}; "
          f"precision={prec:.4f} vs base-rate {BASE} -> {'lift' if lifts_prec else 'no lift'}; VERDICT={verdict}")
else:
    print("GATE: accuracy/precision missing from metrics:", list(m))
PY

echo '{"m23_phase1_done":true}' >> "$RESULT"
log "DONE — full result in $RESULT"
