import json,sys
sys.path.insert(0,'/home/user/Metis-Insights')
W="/tmp/claude-0/work/"
tr=json.load(open(W+"b2_trades.json")); pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
def ct(t):
    if t.get("closed_at"): return str(t["closed_at"])
    p=pkgs.get(t.get("order_package_id"))
    if p and p.get("updated_at"): return str(p["updated_at"])
    return str(t.get("timestamp") or "")
E35={"trend_donchian","trend_donchian_eth_4h","trend_donchian_xrp_4h"}
rows=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest")
      and "2026-08-30"<=ct(t).replace(" ","T")[:19]<"2026-09-07"]
rows.sort(key=lambda t:ct(t))
print(f"POST-WINDOW closed rows n={len(rows)} (graded n={sum(1 for t in rows if t.get('pnl') is not None)})\n")
hdr=f"{'id':>5} {'strategy':22s} {'sym':9s} {'dir':5s} {'entry':>10s} {'stop':>10s} {'exit':>10s} {'stopdist%':>9s} {'pnl':>8s} {'exit_reason':18s} {'e35':4s} opened"
print(hdr); print("-"*len(hdr))
for t in rows:
    e=t.get("entry_price"); s=t.get("stop_loss"); x=t.get("exit_price")
    sd = (abs(e-s)/e*100) if (e and s) else None
    sn=t.get("strategy_name") or t.get("setup_type") or "?"
    print(f"{t['id']:>5} {sn:22.22s} {str(t.get('symbol')):9.9s} {str(t.get('direction')):5.5s} "
          f"{('%.5f'%e if e else 'None'):>10} {('%.5f'%s if s else 'None'):>10} {('%.5f'%x if x else 'None'):>10} "
          f"{('%.2f'%sd if sd else '-'):>9} {('%.2f'%float(t['pnl']) if t.get('pnl') is not None else 'None'):>8} "
          f"{str(t.get('exit_reason')):18.18s} {'YES' if sn in E35 else '-':4s} {str(t.get('timestamp'))[:19]}")
json.dump([t['id'] for t in rows],open(W+"post_ids.json","w"))
