#!/usr/bin/env bash
# R-contamination probe v3. READ-ONLY. Fixes v2's mangled 4a and adds the
# THREE-STATE grading against order_packages.meta.risk_per_unit — the
# independent initial-risk record written at SIGNAL time.
set -uo pipefail
python3 - <<'PY'
import glob, os, sqlite3, json, datetime as dt, statistics, sys

cands = sorted(set(glob.glob('/home/ubuntu/**/trade_journal*.db', recursive=True)
                   + glob.glob('/data/**/trade_journal*.db', recursive=True)),
               key=lambda p: os.path.getmtime(p), reverse=True)
DB = cands[0]
print("DB_PATH:", DB)
print("DB_MTIME_UTC:", dt.datetime.utcfromtimestamp(os.path.getmtime(DB)).isoformat()+"Z")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
print("max_created_at:", con.execute("SELECT MAX(created_at) FROM trades").fetchone()[0])
print()

WRONG = ("((LOWER(t.direction) IN ('buy','long')  AND t.stop_loss > t.entry_price) OR "
         " (LOWER(t.direction) IN ('sell','short') AND t.stop_loss < t.entry_price))")

print("=== 4a. order_packages.sl vs trades.stop_loss (v2 SQL was mangled) ===")
r = con.execute(f"""
SELECT COUNT(*) n,
 SUM(CASE WHEN op.sl IS NULL THEN 1 ELSE 0 END) no_pkg,
 SUM(CASE WHEN op.sl IS NOT NULL AND ABS(op.sl-t.stop_loss)<1e-9 THEN 1 ELSE 0 END) pkg_eq,
 SUM(CASE WHEN op.sl IS NOT NULL AND ABS(op.sl-t.stop_loss)>=1e-9 THEN 1 ELSE 0 END) pkg_diff,
 SUM(CASE WHEN op.meta LIKE '%risk_per_unit%' THEN 1 ELSE 0 END) meta_has_rpu,
 SUM(CASE WHEN op.meta LIKE '%risk_per_unit%' AND {WRONG} THEN 1 ELSE 0 END) wrong_and_rpu
FROM trades t LEFT JOIN order_packages op ON op.order_package_id=t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
  AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL""").fetchone()
print(dict(r)); print()

print("=== 4d. is intent_reduce ALREADY excluded from /performance? (setup_type + notes) ===")
for row in con.execute("""
SELECT COALESCE(setup_type,'(null)') st, COUNT(*) n,
  SUM(CASE WHEN COALESCE(notes,'') LIKE '%"intent_reduce": true%' THEN 1 ELSE 0 END) notes_flag
FROM trades WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0
  AND exit_reason='intent_reduce_executed' GROUP BY 1"""):
    print(dict(row))
print()

print("=== 7. THREE-STATE GRADING against order_packages.meta.risk_per_unit ===")
print("stored_dist = |entry - stop_loss| ; declared = meta.risk_per_unit (signal time)")
rows = con.execute(f"""
SELECT t.id, t.strategy_name, t.symbol, t.direction, t.entry_price, t.stop_loss,
       t.position_size, t.pnl, t.exit_reason,
       COALESCE(t.account_class, CASE WHEN t.is_demo THEN 'paper' ELSE 'real_money' END) acct,
       op.meta pkg_meta,
       CASE WHEN {WRONG} THEN 1 ELSE 0 END wrong
FROM trades t LEFT JOIN order_packages op ON op.order_package_id=t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
  AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL""").fetchall()

TOL = 1e-6
states = {}
by_strat = {}
ratios = []
conf_wrong = 0
for r in rows:
    dist = abs(float(r["entry_price"]) - float(r["stop_loss"]))
    declared = None
    m = r["pkg_meta"]
    if m:
        try:
            declared = json.loads(m).get("risk_per_unit")
            declared = float(declared) if declared is not None else None
        except Exception:
            declared = None
    if declared is None or declared <= 0:
        st = "unverifiable_no_declared_risk"
    else:
        rel = abs(dist - declared) / declared
        if rel <= 1e-4:
            st = "confirmed_initial"
        else:
            st = "contaminated_vs_declared"
            if dist > 0:
                ratios.append(declared / dist)
    if r["wrong"]:
        st_side = "wrong_side"
        if st == "confirmed_initial":
            conf_wrong += 1
    else:
        st_side = "side_ok"
    states[(st, st_side)] = states.get((st, st_side), 0) + 1
    k = (r["strategy_name"], r["acct"])
    b = by_strat.setdefault(k, {"n":0,"conf":0,"cont":0,"unv":0,"wrong":0})
    b["n"] += 1; b["wrong"] += r["wrong"]
    b["conf" if st=="confirmed_initial" else ("cont" if st=="contaminated_vs_declared" else "unv")] += 1

print(f"total gradeable rows: {len(rows)}")
for k in sorted(states, key=lambda x: -states[x]):
    print(f"  {k[0]:34s} x {k[1]:10s} : {states[k]}")
print()
print("!! CROSS-CHECK — rows CONFIRMED-initial yet WRONG-SIDE (must be ~0; a non-zero")
print("   count means one of the two instruments is wrong):", conf_wrong)
print()
if ratios:
    ratios.sort()
    print(f"R INFLATION FACTOR (declared_initial_risk / stored_stop_dist) over "
          f"n={len(ratios)} contaminated-vs-declared rows:")
    def pct(p): return ratios[min(len(ratios)-1, int(p*len(ratios)))]
    print(f"  min {ratios[0]:.3f} | p25 {pct(.25):.3f} | median {pct(.5):.3f} | "
          f"p75 {pct(.75):.3f} | p95 {pct(.95):.3f} | max {ratios[-1]:.1f}")
    print(f"  rows where stored R is INFLATED >2x: {sum(1 for x in ratios if x>2)}")
    print(f"  rows where stored R is INFLATED >10x: {sum(1 for x in ratios if x>10)}")
print()
print("=== 8. PER-STRATEGY three-state (n>=5), worst-verifiable first ===")
print(f"{'strategy':32s} {'acct':11s} {'n':>4s} {'confirmed':>9s} {'contam':>7s} {'unverif':>8s} {'wrongside':>9s}")
for k, b in sorted(by_strat.items(), key=lambda kv: (-kv[1]["cont"], -kv[1]["n"])):
    if b["n"] < 5: continue
    print(f"{str(k[0])[:32]:32s} {str(k[1])[:11]:11s} {b['n']:>4d} {b['conf']:>9d} "
          f"{b['cont']:>7d} {b['unv']:>8d} {b['wrong']:>9d}")
print("=== END ===")
PY
