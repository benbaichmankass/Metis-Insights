import json, subprocess
BASE="https://ict-bot.duckdns.org/api/bot/db/table/order_packages"
rows=[];off=0
while True:
    url=f"{BASE}?limit=500&offset={off}&order_by=order_package_id&order_dir=asc"
    d=json.loads(subprocess.run(["curl","-sS",url],capture_output=True,text=True).stdout)
    r=d.get("rows",[]); rows+=r
    if len(r)<500: break
    off+=500
print("fetched",len(rows),"total",d.get("total"))
json.dump(rows,open("/tmp/claude-0/work/pkgs.json","w"))
print("cols:",list(rows[0].keys()) if rows else None)
