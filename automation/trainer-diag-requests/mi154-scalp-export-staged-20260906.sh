#!/usr/bin/env bash
# MI-154 — inspect the scalp exit-head datasets and STAGE an export.
# ⚠️ DELIBERATELY WRITES OUTSIDE runtime_logs/trainer_mirror/ — nothing is published.
#    The mirror is the trainer->live channel; publishing a scalp head before the
#    ict_scalp consumer AND the family gate exist would put a family=ict_scalp
#    artifact in front of a guard that does not check family. Staged only.
set -u
cd /home/ubuntu/ict-trading-bot || { echo "NO REPO DIR"; exit 2; }
STAGE=/home/ubuntu/mi154_staged
mkdir -p "$STAGE"

echo "=== 0. venv + lightgbm ==="
ls -d .venv 2>/dev/null && .venv/bin/python3 -c "import lightgbm,sys;print('lightgbm',lightgbm.__version__,'py',sys.version.split()[0])" 2>&1 | head -2

echo
echo "=== 1. candidate scalp family dirs with rows.jsonl ==="
find ./runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z \
     ./runtime_logs/m20_exit_head/scalp_15m_20260814T135244Z \
     ./runtime_logs/m20_exit_head/cand15m_20260815T042247Z \
     ./datasets-out/exit_head \
     -name rows.jsonl 2>/dev/null -printf '%10s %p\n' | sort -k2 | head -30

echo
echo "=== 2. shape of each candidate: n rows, harness rows, symbols, tf, bar_t span ==="
for f in $(find ./runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z \
                ./runtime_logs/m20_exit_head/scalp_15m_20260814T135244Z \
                ./runtime_logs/m20_exit_head/cand15m_20260815T042247Z/ict_scalp_sol_15m_off0 \
                ./runtime_logs/m20_exit_head/cand15m_20260815T042247Z/ict_scalp_xrp_15m_off0 \
                ./datasets-out/exit_head/1h/ict_scalp_5m \
                -name rows.jsonl 2>/dev/null | head -12); do
  echo "--- $f"
  .venv/bin/python3 - "$f" <<'PY' 2>&1 | head -8
import json,sys,collections,datetime
p=sys.argv[1]; n=0; h=0; syms=collections.Counter(); tfs=collections.Counter(); ts=[]
for line in open(p):
    line=line.strip()
    if not line: continue
    try: r=json.loads(line)
    except Exception: continue
    n+=1
    if r.get("source")=="harness":
        h+=1; syms[r.get("symbol")]+=1; tfs[r.get("tf") or r.get("timeframe")]+=1
        t=r.get("bar_t")
        if t:
            try: ts.append(int(t))
            except Exception: pass
def iso(x): return datetime.datetime.fromtimestamp(x,tz=datetime.timezone.utc).date().isoformat()
print(f"    rows={n} harness={h} symbols={dict(syms)} tf_field={dict(tfs)}")
print(f"    bar_t span={iso(min(ts))}..{iso(max(ts))} coverage={round(len(ts)/h,3) if h else None}" if ts else "    bar_t: NONE")
PY
done
echo
echo "=== 3. disk before ==="; df -h / | tail -1
echo "=== END ==="
