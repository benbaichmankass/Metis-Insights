import json, subprocess, sys
BASE="https://ict-bot.duckdns.org/api/bot/db/table/trades"
def page(offset, limit=500, filt=None):
    url=f"{BASE}?limit={limit}&offset={offset}&order_by=id&order_dir=asc"
    if filt: url+=f"&filter_col={filt[0]}&filter_op=eq&filter_val={filt[1]}"
    out=subprocess.run(["curl","-sS",url],capture_output=True,text=True).stdout
    return json.loads(out)
def fetch_all(filt=None):
    rows=[];off=0;state=None;total=None
    while True:
        d=page(off,500,filt)
        state=d.get("filter_state"); total=d.get("total")
        r=d.get("rows",[])
        rows+=r
        if len(r)<500: break
        off+=500
        if off>20000: break
    return rows,state,total
if __name__=="__main__":
    which=sys.argv[1]
    if which=="b2":
        rows,state,total=fetch_all(("account_id","bybit_2"))
        print("filter_state",state,"total",total,"fetched",len(rows))
        json.dump(rows,open("/tmp/claude-0/work/b2_trades.json","w"))
    else:
        rows,state,total=fetch_all()
        print("filter_state",state,"total",total,"fetched",len(rows))
        json.dump(rows,open("/tmp/claude-0/work/all_trades.json","w"))
