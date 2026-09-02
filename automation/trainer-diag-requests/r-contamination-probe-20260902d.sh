#!/usr/bin/env bash
# R-contamination probe v4. READ-ONLY.
# v3's OWN CROSS-CHECK FAILED: 34 rows read CONFIRMED-initial on distance yet
# WRONG-SIDE on sign. Resolve that before shipping a detector built on both axes.
set -uo pipefail
python3 - <<'PY'
import glob, os, sqlite3, json, datetime as dt
cands = sorted(set(glob.glob('/home/ubuntu/**/trade_journal*.db', recursive=True)
                   + glob.glob('/data/**/trade_journal*.db', recursive=True)),
               key=lambda p: os.path.getmtime(p), reverse=True)
DB = cands[0]; print("DB_PATH:", DB)
print("DB_MTIME_UTC:", dt.datetime.utcfromtimestamp(os.path.getmtime(DB)).isoformat()+"Z")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
print("max_created_at:", con.execute("SELECT MAX(created_at) FROM trades").fetchone()[0]); print()

rows = con.execute("""
SELECT t.id, t.strategy_name, t.symbol, t.direction AS t_dir, op.direction AS op_dir,
       t.entry_price, t.stop_loss, t.take_profit_1, op.entry AS op_entry, op.sl AS op_sl,
       op.tp AS op_tp, t.position_size, t.pnl, t.exit_reason, t.setup_type,
       COALESCE(t.account_class, CASE WHEN t.is_demo THEN 'paper' ELSE 'real_money' END) acct,
       op.meta pkg_meta, t.created_at
FROM trades t LEFT JOIN order_packages op ON op.order_package_id=t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
  AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL""").fetchall()

def declared(m):
    if not m: return None
    try:
        v = json.loads(m).get("risk_per_unit")
        return float(v) if v is not None else None
    except Exception: return None
def wrong(d, e, s):
    d = (d or "").lower()
    if d in ("buy","long"):  return s > e
    if d in ("sell","short"): return s < e
    return None

anom, ok_side_ratios, wrong_ratios, dir_mismatch = [], [], [], 0
for r in rows:
    e, s = float(r["entry_price"]), float(r["stop_loss"])
    dist = abs(e - s); dec = declared(r["pkg_meta"])
    w = wrong(r["t_dir"], e, s)
    if r["op_dir"] and str(r["op_dir"]).lower() != str(r["t_dir"] or "").lower():
        dir_mismatch += 1
    if dec and dec > 0 and dist > 0:
        (wrong_ratios if w else ok_side_ratios).append(dec / dist)
        if w and abs(dist - dec)/dec <= 1e-4:
            anom.append(r)

print("=== A. `trades.direction` vs `order_packages.direction` disagreement count ===")
print(f"  rows where the two direction columns DISAGREE: {dir_mismatch} of {len(rows)}")
print("  (a non-zero count would mean the SIDE test's input is itself unreliable)")
print()
print(f"=== B. THE 34-ROW ANOMALY — distance MATCHES declared, side is WRONG (n={len(anom)}) ===")
print("id | strategy | acct | t_dir | op_dir | entry | stop | op_entry | op_sl | tp | declared_rpu | dist | exit_reason | setup_type")
for r in anom[:40]:
    e, s = float(r["entry_price"]), float(r["stop_loss"])
    print(f"{r['id']} | {r['strategy_name']} | {r['acct']} | {r['t_dir']} | {r['op_dir']} | "
          f"{e} | {s} | {r['op_entry']} | {r['op_sl']} | {r['take_profit_1']} | "
          f"{declared(r['pkg_meta'])} | {abs(e-s):.8f} | {r['exit_reason']} | {r['setup_type']}")
print()
print("=== B2. do those rows' TP sit on the same side as their SL? (a mirrored bracket) ===")
mir = 0
for r in anom:
    e, s, tp = float(r["entry_price"]), float(r["stop_loss"]), r["take_profit_1"]
    if tp is None: continue
    tp = float(tp)
    d = (r["t_dir"] or "").lower()
    # For a coherent bracket: long -> sl<e<tp ; short -> tp<e<sl
    coherent = (d in ("buy","long") and s < e < tp) or (d in ("sell","short") and tp < e < s)
    if not coherent: mir += 1
print(f"  of {len(anom)} anomaly rows, {mir} have an INCOHERENT entry/sl/tp bracket ordering")
print()

def dist_summary(name, xs):
    if not xs: print(f"{name}: (none)"); return
    xs = sorted(xs); n=len(xs)
    p=lambda q: xs[min(n-1,int(q*n))]
    print(f"{name}: n={n} min {xs[0]:.4f} p10 {p(.10):.4f} p25 {p(.25):.4f} "
          f"median {p(.5):.4f} p75 {p(.75):.4f} p90 {p(.90):.4f} p95 {p(.95):.4f} max {xs[-1]:.1f}")
    for bar in (1.01, 1.05, 1.1, 1.25, 1.5, 2.0, 5.0, 10.0):
        print(f"    ratio >= {bar:>5}: {sum(1 for x in xs if x>=bar):>5d}  "
              f"({100*sum(1 for x in xs if x>=bar)/n:.1f}%)")
    print(f"    ratio <  0.99 (stored WIDER than declared — NOT a trail signature): "
          f"{sum(1 for x in xs if x<0.99)}")

print("=== C. RATIO declared_initial_risk / stored_stop_distance ===")
print("    (>1 == the stored stop is TIGHTER than the declared initial risk == the trail signature)")
dist_summary("  CORRECT-SIDE rows", ok_side_ratios)
print()
dist_summary("  WRONG-SIDE rows ", wrong_ratios)
print()
print("=== D. is the ~1.02 cluster a TRAIL or a systematic offset? cut by strategy family ===")
fam = {}
for r in rows:
    e, s = float(r["entry_price"]), float(r["stop_loss"])
    dist = abs(e-s); dec = declared(r["pkg_meta"])
    if not dec or dec <= 0 or dist <= 0: continue
    if wrong(r["t_dir"], e, s): continue
    k = r["strategy_name"]
    fam.setdefault(k, []).append(dec/dist)
print(f"{'strategy':30s} {'n':>4s} {'median':>8s} {'p90':>8s} {'>=1.01':>7s} {'>=1.25':>7s} {'<0.99':>6s}")
for k, xs in sorted(fam.items(), key=lambda kv: -len(kv[1])):
    if len(xs) < 5: continue
    xs = sorted(xs); n = len(xs)
    print(f"{str(k)[:30]:30s} {n:>4d} {xs[n//2]:>8.4f} {xs[min(n-1,int(.9*n))]:>8.4f} "
          f"{sum(1 for x in xs if x>=1.01):>7d} {sum(1 for x in xs if x>=1.25):>7d} "
          f"{sum(1 for x in xs if x<0.99):>6d}")
print()
print("=== E. does /api/bot/trades/closed's population have order_package_id populated? ===")
r = con.execute("""SELECT COUNT(*) n,
  SUM(CASE WHEN order_package_id IS NOT NULL AND order_package_id!='' THEN 1 ELSE 0 END) with_opid
 FROM trades WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0""").fetchone()
print(dict(r))
print("=== END ===")
PY
