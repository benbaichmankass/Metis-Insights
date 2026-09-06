import json,sys,collections
sys.path.insert(0,'/home/user/Metis-Insights')
from src.runtime.r_provenance import classify_r,R_CONTAMINATED,R_CONFIRMED_INITIAL,R_UNVERIFIED,R_NO_BASIS
from src.web.api._clean_trades import r_multiple
from src.runtime.local_pnl import contract_value_usd_for
W="/tmp/claude-0/work/"
tr=json.load(open(W+"b2_trades.json"))
pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
def ct(t):
    for k in ("closed_at",):
        v=t.get(k)
        if v: return str(v)
    p=pkgs.get(t.get("order_package_id"))
    if p and p.get("updated_at"): return str(p["updated_at"])
    return str(t.get("timestamp") or "")
def norm(s): return s.replace(" ","T")[:19]
pop=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest")]
print(f"bybit_2 status='closed', non-backtest: n={len(pop)}")
def inwin(t,a,b):
    c=norm(ct(t)); return c and a<=c<b
WINS=[("PRIOR 2026-08-16..08-29","2026-08-16","2026-08-30"),
      ("POST  2026-08-30..09-06","2026-08-30","2026-09-07")]
def exit_path(t):
    er=(t.get("exit_reason") or "").strip().lower()
    if t.get("pnl") is None: return "pnl-is-null"
    if er in ("sl","stop_loss","stop"): return "sl"
    if er in ("tp","take_profit","tp1","take_profit_1"): return "tp"
    if "reconcil" in er: return "reconciler"
    if not er: return "other(no exit_reason)"
    return f"other:{er}"
for label,a,b in WINS:
    rows=[t for t in pop if inwin(t,a,b)]
    graded=[t for t in rows if t.get("pnl") is not None]
    pnl=sum(float(t["pnl"]) for t in graded)
    wins=sum(1 for t in graded if float(t["pnl"])>0)
    print(f"\n=== {label} ===")
    print(f"  closed rows n={len(rows)}   graded (pnl NOT NULL) n={len(graded)}   wins={wins}  winrate={100*wins/len(graded) if graded else 0:.1f}%  PnL={pnl:+.2f}")
    print("  EXIT PATH SPLIT (all closed rows in window):")
    for k,v in sorted(collections.Counter(exit_path(t) for t in rows).items(),key=lambda x:-x[1]):
        sub=[t for t in rows if exit_path(t)==k and t.get("pnl") is not None]
        p=sum(float(t["pnl"]) for t in sub); w=sum(1 for t in sub if float(t["pnl"])>0)
        print(f"    {k:26s} n={v:3d}  graded={len(sub):3d}  wins={w:2d}  pnl={p:+8.2f}")
    st=collections.Counter()
    for t in graded:
        pk=pkgs.get(t.get("order_package_id"))
        s,_=classify_r({"direction":t.get("direction"),"entry_price":t.get("entry_price"),
            "stop_loss":t.get("stop_loss"),"take_profit_1":t.get("take_profit_1"),
            "qty":t.get("position_size"),"package_meta":(pk or {}).get("meta")})
        st[s]+=1
    print(f"  R-provenance over graded: {dict(st)}")
    print("  PER-STRATEGY (graded):")
    per=collections.defaultdict(lambda:[0,0,0.0])
    for t in graded:
        n=t.get("strategy_name") or t.get("setup_type") or "?"
        per[n][0]+=1
        if float(t["pnl"])>0: per[n][1]+=1
        per[n][2]+=float(t["pnl"])
    for n,(c,w,p) in sorted(per.items(),key=lambda x:x[1][2]):
        print(f"    {n:28s} n={c:3d} wins={w:2d} pnl={p:+8.2f}")
