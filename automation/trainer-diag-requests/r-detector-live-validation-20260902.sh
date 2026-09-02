#!/usr/bin/env bash
# LIVE VALIDATION of the shipped detector. READ-ONLY.
# Runs `src/runtime/r_provenance.py` AS SHIPPED (fetched from the branch's raw
# URL, not a re-implementation — a second copy could drift from the one under
# test) over the live journal, and asserts the partition sums.
set -uo pipefail
RAW="https://raw.githubusercontent.com/benbaichmankass/Metis-Insights/claude/r-metric-contamination/src/runtime/r_provenance.py"
mkdir -p /tmp/rprov && curl -fsSL "$RAW" -o /tmp/rprov/r_provenance.py || { echo "FETCH FAILED"; exit 4; }
echo "module sha256: $(sha256sum /tmp/rprov/r_provenance.py | cut -d' ' -f1)"
echo "module bytes:  $(wc -c < /tmp/rprov/r_provenance.py)"
python3 - <<'PY'
import glob, os, sqlite3, json, datetime as dt, importlib.util, sys
spec = importlib.util.spec_from_file_location("r_provenance", "/tmp/rprov/r_provenance.py")
rp = importlib.util.module_from_spec(spec); spec.loader.exec_module(rp)
print("loaded states:", rp.R_STATES, "bar:", rp.DISAGREEMENT_RATIO_BAR)

cands = sorted(set(glob.glob('/home/ubuntu/**/trade_journal*.db', recursive=True)
                   + glob.glob('/data/**/trade_journal*.db', recursive=True)),
               key=lambda p: os.path.getmtime(p), reverse=True)
DB = cands[0]; print("DB_PATH:", DB)
print("DB_MTIME_UTC:", dt.datetime.utcfromtimestamp(os.path.getmtime(DB)).isoformat()+"Z")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
print("max_created_at:", con.execute("SELECT MAX(created_at) FROM trades").fetchone()[0]); print()

SQL = """
SELECT t.id, t.strategy_name, t.exit_reason, t.setup_type,
       COALESCE(t.account_class, CASE WHEN t.is_demo THEN 'paper' ELSE 'real_money' END) acct,
       t.direction, t.entry_price, t.stop_loss, t.take_profit_1,
       t.position_size AS qty, t.pnl, op.meta AS package_meta
FROM trades t LEFT JOIN order_packages op ON op.order_package_id = t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
"""
rows = [dict(r) for r in con.execute(SQL)]
print(f"=== POPULATION: closed, pnl NOT NULL, non-backtest — n={len(rows)} ===")
print("(NOTE: this is WIDER than /performance's, which also excludes reconciler /")
print(" superseded / reset-flat rows. Stated so the number is not read as that route's.)")
print()

s = rp.summarize(rows)
print("summarize():", json.dumps(s, indent=2))
tot = sum(s["counts"].values())
print(f"\nPARTITION CHECK: sum(counts)={tot} vs graded={s['graded']} -> "
      f"{'OK' if tot == s['graded'] else '*** MISMATCH ***'}")
print(f"PARTITION CHECK: sum(counts)={tot} vs rows={len(rows)} -> "
      f"{'OK' if tot == len(rows) else '*** MISMATCH ***'}")
print()

# per-reason, so the grade is checkable rather than trusted
byreason = {}
bystate_strat = {}
for r in rows:
    st, why = rp.classify_r(r)
    byreason[(st, why)] = byreason.get((st, why), 0) + 1
    k = (r["strategy_name"], r["acct"])
    b = bystate_strat.setdefault(k, dict(n=0, **{x: 0 for x in rp.R_STATES}))
    b["n"] += 1; b[st] += 1
print("=== BY (state, reason) ===")
for k in sorted(byreason, key=lambda x: -byreason[x]):
    print(f"  {byreason[k]:>5d}  {k[0]:18s} {k[1]}")
print()
print("=== MIRRORED-BRACKET rows: are they ALL setup_type='intent_reduce'? ===")
mir = [r for r in rows if rp.classify_r(r)[1] == "bracket_mirrored_vs_direction"]
st = {}
for r in mir: st[r["setup_type"]] = st.get(r["setup_type"], 0) + 1
print(f"  n={len(mir)} setup_type histogram: {st}")
print("  (if a setup_type OTHER than intent_reduce appears, the mirrored-bracket")
print("   population is wider than the resolved anomaly and needs its own look)")
print()
print("=== PER-STRATEGY (n>=5), most CONTAMINATED first ===")
print(f"{'strategy':30s} {'acct':11s} {'n':>4s} {'contam':>7s} {'confirm':>8s} {'unverif':>8s} {'nobasis':>8s}")
for k, b in sorted(bystate_strat.items(), key=lambda kv: (-kv[1]['contaminated'], -kv[1]['n'])):
    if b["n"] < 5: continue
    print(f"{str(k[0])[:30]:30s} {str(k[1])[:11]:11s} {b['n']:>4d} {b['contaminated']:>7d} "
          f"{b['confirmed_initial']:>8d} {b['unverified']:>8d} {b['no_basis']:>8d}")
print("\n=== END ===")
PY
