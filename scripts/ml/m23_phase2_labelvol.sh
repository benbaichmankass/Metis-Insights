#!/usr/bin/env bash
# M23 Phase 2 (P2) — LABEL-VOLUME expansion (MB-20260717-M23-META-LABEL).
#
# P1's definitive conclusion (docs/research/M23-phase1-C1-results-2026-07-17.md):
# every lever (τ sweep, regression, faithfulness haircut) hit the SAME wall —
# the net-positive region caps at ~2–11 trades because the eval book is 376
# BTC-only real trades and the train pool 1,685 BTC-only backtest rows. This
# run pushes label VOLUME on both sides by pooling THREE symbols:
#   1. Per symbol ∈ {BTCUSDT, ETHUSDT, SOLUSDT}: market_raw 1h (5y, already
#      built nightly) → 1h CSV, resampled to 2h + 4h (leakage-free downsample).
#   2. Per symbol × strategy roster (trend_donchian@1h, squeeze_breakout_4h@4h,
#      htf_pullback_trend_2h@2h): harness --emit-trades replay → up to 9 legs.
#   3. Record ALL legs into ONE temp db (is_backtest=1; never the money journal).
#   4. Build setup_candidates v020 (pooled 3-crypto-symbol backtest-train +
#      the REAL closed trades of EVERY WS-B-covered shard as the live_holdout
#      eval book — widened past the 376 BTC-only rows via the MR_PATHS roster
#      below, WS-3b MB-20260717 follow-on).
#   5. Train setup-candidates-metalabel-p2pool-v1 (`won`) → population-matched
#      gate with RECOMPUTED references (majority accuracy + win base rate are
#      derived from the NEW pooled eval book — P1's 0.756/0.244 were BTC-book
#      constants).
#   6. Rebuild at v021 with r_label_threshold ∈ {0.5, 0.75} → train
#      setup-candidates-metalabel-p2pool-c1-v1 (`won_r`) → m23_ev_gate.py
#      net-R selection sweep (the gate that matters: does the top slice go
#      net-positive at USABLE volume — ≥40 trades / ≥10% coverage?).
#
# Tier-1 / offline / trainer-side. Writes ONLY temp-db + datasets-out artifacts;
# no src/, config/, order-path, or registry write (--no-register). Takes the
# shared heavy-job lock so it never thrashes the 6 GB box against the nightly
# cycle (docs/claude/trainer-resource-protocol.md).
set -uo pipefail

REPO="${REPO:-/home/ubuntu/ict-trading-bot}"
cd "$REPO"
LIVE_JOURNAL="${LIVE_JOURNAL:-data/trade_journal.db}"
BT_DB="${BT_DB:-/tmp/m23_p2_backtest.db}"
SC_V_WON="${SC_V_WON:-v020}"
SC_V_C1="${SC_V_C1:-v021}"
RUN_TAG="${RUN_TAG:-m23-p2-$(date +%Y%m%d 2>/dev/null || echo 20260719)}"
RESULT="${RESULT:-/tmp/m23_p2_result.txt}"
SYMBOLS=(BTCUSDT ETHUSDT SOLUSDT)
MR_VER="${MR_VER:-v002}"
# L3 paper-book eval population — OFF by default (byte-for-byte the real-only
# build). Set INCLUDE_PAPER=true to ALSO emit the PAPER cohort as a distinct,
# tagged (event_source="live_paper") population for the paper-eval manifest;
# NEVER blended into the real-money eval (see setup_candidates.py::iter_rows).
INCLUDE_PAPER="${INCLUDE_PAPER:-false}"

: > "$RESULT"
log() { echo "[$(date -u +%H:%M:%S 2>/dev/null || echo m23p2)] $*" | tee -a "$RESULT"; }
source .venv/bin/activate 2>/dev/null || log "WARN: no .venv"

# Serialize against the nightly cycle / other heavy jobs (6 GB box).
# shellcheck source=/dev/null
. scripts/ops/_trainer_heavy_lock.sh
if ! take_trainer_heavy_lock "m23_p2_labelvol"; then
  log "ABORT: trainer heavy-job queue busy past the wait; re-run later"
  echo '{"m23_p2_done":false,"error":"heavy_lock_timeout"}' >> "$RESULT"
  exit 0
fi

log "STEP 1: per-symbol market_raw -> 1h/2h/4h CSVs"
declare -A CSV  # "SYM|tf" -> path
for sym in "${SYMBOLS[@]}"; do
  src="datasets-out/market_raw/${sym}/1h/${MR_VER}/data.jsonl"
  if [ ! -f "$src" ]; then log "  WARN: $src missing — $sym dropped from the pool"; continue; fi
  c1="/tmp/m23p2_${sym}_1h.csv"; c2="/tmp/m23p2_${sym}_2h.csv"; c4="/tmp/m23p2_${sym}_4h.csv"
  python3 - "$src" "$c1" "$c2" "$c4" <<'PY' 2>>"$RESULT"
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
def resample(data, n):
    out = []
    for i in range(0, len(data) - n + 1, n):
        g = data[i:i+n]
        out.append((g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g),
                    g[-1][4], sum(x[5] for x in g)))
    return out
write(d1, rows); write(d2, resample(rows, 2)); write(d4, resample(rows, 4))
print("csv rows: 1h", len(rows))
PY
  CSV["$sym|1h"]="$c1"; CSV["$sym|2h"]="$c2"; CSV["$sym|4h"]="$c4"
  log "  $sym: 1h=$(wc -l < "$c1" 2>/dev/null) 2h=$(wc -l < "$c2" 2>/dev/null) 4h=$(wc -l < "$c4" 2>/dev/null)"
done

log "STEP 2: harness replay per (strategy × symbol)"
ROSTER=(
  "trend_donchian|scripts/backtest_trend.py|1h"
  "squeeze_breakout_4h|scripts/backtest_squeeze.py|4h"
  "htf_pullback_trend_2h|scripts/backtest_pullback.py|2h"
)
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

total_bt=0
for sym in "${SYMBOLS[@]}"; do
  declare -a RECORD_ARGS=()
  for entry in "${ROSTER[@]}"; do
    IFS='|' read -r strat harness tf <<< "$entry"
    csv="${CSV[$sym|$tf]:-}"
    [ -z "$csv" ] || [ ! -f "$csv" ] && continue
    jsonl="/tmp/m23p2_${sym}_${strat}_trades.jsonl"
    python3 "$harness" --data "$csv" --timeframe "$tf" --symbol "$sym" \
      --emit-trades "$jsonl" --json "/tmp/m23p2_${sym}_${strat}_summary.json" >>"$RESULT" 2>&1 \
      || log "    WARN: ${sym}/${strat} harness returned nonzero"
    n=$(wc -l < "$jsonl" 2>/dev/null || echo 0)
    log "  ${sym}/${strat}@${tf}: emitted $n"
    [ "${n:-0}" != "0" ] && RECORD_ARGS+=(--trades-jsonl "${jsonl}=${strat}")
  done
  if [ "${#RECORD_ARGS[@]}" != "0" ]; then
    python3 -m scripts.ml.record_harness_trades --db "$BT_DB" --symbol "$sym" \
      "${RECORD_ARGS[@]}" --run-tag "$RUN_TAG" >>"$RESULT" 2>&1
  fi
done
NBT=$(python3 - "$BT_DB" <<'PY' 2>>"$RESULT"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("select count(*) from trades where is_backtest=1").fetchone()[0])
for r in c.execute("select symbol, strategy_name, count(*) from trades where is_backtest=1 group by 1,2"):
    print("  ", r[0], r[1], r[2], file=sys.stderr)
PY
)
log "  pooled backtest rows recorded: $NBT"
if [ "${NBT:-0}" = "0" ]; then log "ABORT: 0 backtest rows"; echo '{"m23_p2_done":false,"error":"no_backtest_rows"}' >> "$RESULT"; exit 1; fi

# --- Eval-book market_raw coverage (WS-3b, MB-20260717 label-volume follow-on) ---
# The eval book (real closed trades — the P1 wall of 376 BTC-only rows) grows only
# for symbols whose market_raw shard is passed here: setup_candidates derives each
# shard's symbol from its bars and path-resolves THAT symbol's REAL closed trades
# at the covering bar (setup_candidates.py::_iter_one_symbol -> _load_live_trades).
# So widen the resolution set to every WS-B-covered shard at its ACTUAL timeframe
# (build_trainer_datasets.sh: crypto 1h, alt-USDT 15m, equities/metals 1d) — ONE
# shard per symbol (a symbol listed at two timeframes would double-count its
# trades). The backtest-TRAIN pool stays the 3 crypto symbols' intraday replay
# above: equities (1d) / alt (15m) have no 1h base for the 1h/2h/4h strategy
# harnesses, so this run grows the EVAL side (a measurement — the honest widening
# of the wall's denominator), NOT a matching train-side claim; the
# alt-15m->intraday-resample train-side growth is a further follow-on.
# Existence-guarded: _load_market_raw_rows RAISES on a missing shard, so a
# not-yet-built shard (e.g. an alt 15m the parity gate skipped that night) is
# skipped and logged, never crashing the setup_candidates build.
EVAL_SHARDS=(
  "BTCUSDT|1h" "ETHUSDT|1h" "SOLUSDT|1h"
  "ADAUSDT|15m" "AVAXUSDT|15m" "XRPUSDT|15m"
  "SPY|1d" "QQQ|1d" "GLD|1d" "TLT|1d" "IWM|1d" "SLV|1d" "IEF|1d"
  "TQQQ|1d" "QLD|1d" "SPLG|1d" "IAUM|1d" "MGC|1d" "MHG|1d"
)
declare -a MR_PATH_LIST=()
declare -a MR_SKIPPED=()
for entry in "${EVAL_SHARDS[@]}"; do
  IFS='|' read -r esym etf <<< "$entry"
  epath="datasets-out/market_raw/${esym}/${etf}/${MR_VER}"
  if [ -f "${epath}/data.jsonl" ]; then
    MR_PATH_LIST+=("$epath")
  else
    MR_SKIPPED+=("${esym}@${etf}")
  fi
done
if [ "${#MR_PATH_LIST[@]}" = "0" ]; then
  log "ABORT: no market_raw eval shards present under datasets-out/market_raw/*/*/${MR_VER}"
  echo '{"m23_p2_done":false,"error":"no_eval_shards"}' >> "$RESULT"; exit 1
fi
MR_PATHS="$(IFS=,; echo "${MR_PATH_LIST[*]}")"
log "  eval-book shards present (${#MR_PATH_LIST[@]}): ${MR_PATH_LIST[*]##*market_raw/}"
[ "${#MR_SKIPPED[@]}" != "0" ] && log "  eval-book shards SKIPPED (not built): ${MR_SKIPPED[*]}"

build_pool() {
  # build_pool <version> [extra build params...]
  local ver="$1"; shift
  python3 -m ml.datasets build setup_candidates --output-dir datasets-out \
    --version "$ver" --source market_raw --symbol-scope all --timeframe all --overwrite -- \
    "market_raw_paths=$MR_PATHS" "backtest_trades_db=$BT_DB" \
    "live_trades_db=$LIVE_JOURNAL" "include_cusum=false" \
    "include_paper=$INCLUDE_PAPER" "$@" >>"$RESULT" 2>&1
  python3 - "datasets-out/setup_candidates/all/all/$ver/data.jsonl" <<'PY' 2>&1 | tee -a "$RESULT"
import json, sys, collections
try:
    es = collections.Counter(); lv = collections.Counter(); sym = collections.Counter()
    live_sym = collections.Counter(); won = collections.Counter(); n = 0
    for line in open(sys.argv[1]):
        line = line.strip()
        if not line: continue
        r = json.loads(line); n += 1
        es[r.get("event_source")] += 1
        lv[bool(r.get("is_live_trade"))] += 1
        sym[r.get("symbol")] += 1
        if r.get("is_live_trade"):
            live_sym[r.get("symbol")] += 1
        won[r.get("won")] += 1
    print("rows:", n, "by_source:", dict(es), "is_live:", dict(lv), "won:", dict(won))
    print("by_symbol:", dict(sym), "LIVE by_symbol:", dict(live_sym))
except Exception as e:
    print("dataset read err:", e)
PY
}

gate_won() {
  # Population-matched accuracy/precision gate with RECOMPUTED references from
  # the pooled eval book (majority + base rate are book properties, not constants).
  python3 - "/tmp/m23p2_train_won.out" "datasets-out/setup_candidates/all/all/$SC_V_WON/data.jsonl" <<'PY' 2>>"$RESULT" | tee -a "$RESULT"
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
# recompute majority + base rate over the LIVE (eval) rows of the pooled book
n_live = wins = 0
for line in open(sys.argv[2]):
    line = line.strip()
    if not line: continue
    r = json.loads(line)
    if r.get("is_live_trade"):
        n_live += 1
        wins += 1 if r.get("won") else 0
base = wins / n_live if n_live else float("nan")
maj = max(base, 1 - base) if n_live else float("nan")
acc = m.get("accuracy"); prec = m.get("precision")
print("METRICS:", json.dumps({k: m[k] for k in m if isinstance(m[k], (int, float))}))
print(f"EVAL BOOK: n_live={n_live} wins={wins} base_rate={base:.4f} majority={maj:.4f}")
if acc is not None and prec is not None:
    verdict = "PASS" if (acc > maj and prec > base) else "FAIL"
    print(f"GATE: accuracy={acc:.4f} vs majority {maj:.4f}; precision={prec:.4f} vs base {base:.4f}; VERDICT={verdict}")
PY
}

log "STEP 3: build pooled dataset $SC_V_WON (won) + train p2pool-v1 + gate"
build_pool "$SC_V_WON"
python3 -m ml train ml/configs/setup-candidates-metalabel-p2pool-v1.yaml \
  --datasets-root datasets-out --no-register > /tmp/m23p2_train_won.out 2>>"$RESULT" \
  || log "WARN: p2pool-v1 train returned nonzero"
gate_won

log "STEP 4: R-aware leg — rebuild $SC_V_C1 per tau, train p2pool-c1-v1, EV-gate sweep"
for tau in 0.5 0.75; do
  log "  tau=$tau: rebuild $SC_V_C1 + train + EV gate"
  build_pool "$SC_V_C1" "r_label_threshold=$tau"
  python3 -m ml train ml/configs/setup-candidates-metalabel-p2pool-c1-v1.yaml \
    --datasets-root datasets-out --no-register > "/tmp/m23p2_train_c1_${tau}.out" 2>>"$RESULT" \
    || log "  WARN: c1 tau=$tau train returned nonzero"
  state="$(ls -t ml/experiments-runs/setup-candidates-metalabel-p2pool-c1-v1/*/model_state.json 2>/dev/null | head -1)"
  if [ -n "$state" ]; then
    log "  EV gate (tau=$tau, model_state=$state):"
    python3 -m scripts.ml.m23_ev_gate \
      --data "datasets-out/setup_candidates/all/all/$SC_V_C1/data.jsonl" \
      --model-state "$state" 2>>"$RESULT" | tee -a "$RESULT"
  else
    log "  WARN: no model_state found for c1 tau=$tau — EV gate skipped"
  fi
done

echo '{"m23_p2_done":true}' >> "$RESULT"
log "DONE — full result in $RESULT"
