# FIX-CA-09 measurement. READ-ONLY (sqlite mode=ro) over the trainer's synced
# mirror of the live trade_journal.db. Per account x UTC day, last 30 days:
#   BEFORE = what the gate could ever see on day d under main:
#            trades created on d AND realized on d (a trade created d, closed
#            d+1 is never seen: on d it is still open, on d+1 the key is d+1).
#   AFTER  = trades realized on d (closed_at, epoch-ms aware; created_at fallback).
#   cap    = daily_loss_pct x daily_risk_state.daily_high_equity (else daily_usd).
#   min_cum = most negative intraday running sum, ordered by close time
#             (the gate trips the first time running PnL < -cap).
import glob, os, sqlite3, datetime as dt, collections
cands = sorted(set(glob.glob('/home/ubuntu/**/trade_journal*.db', recursive=True)
                   + glob.glob('/data/**/trade_journal*.db', recursive=True)),
               key=os.path.getmtime, reverse=True)
DB = cands[0]
print("DB_PATH:", DB)
print("DB_MTIME_UTC:", dt.datetime.utcfromtimestamp(os.path.getmtime(DB)).isoformat() + "Z")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print("max_created_at:", con.execute("SELECT MAX(created_at) FROM trades").fetchone()[0])
print("max_closed_at:", con.execute("SELECT MAX(closed_at) FROM trades WHERE closed_at NOT GLOB '[0-9]*' OR closed_at GLOB '????-*'").fetchone()[0])
CFG = {"bybit_1": (0.05, 100), "bybit_2": (0.05, 100), "bybit_portfolio": (0.05, 100),
       "ib_paper": (0.05, 200), "alpaca_paper": (0.05, 200), "alpaca_portfolio": (0.05, 200),
       "alpaca_live": (0.10, 200), "alpaca_options_paper": (0.05, 200), "breakout_1": (0.03, 150)}
NORM = ("CASE WHEN closed_at IS NOT NULL AND closed_at <> '' AND closed_at GLOB '[0-9]*' "
        "AND NOT closed_at GLOB '*[^0-9]*' AND length(closed_at) >= 12 "
        "THEN datetime(CAST(closed_at AS INTEGER)/1000, 'unixepoch') ELSE datetime(closed_at) END")
since = str(dt.datetime.utcnow().date() - dt.timedelta(days=30))
rows = con.execute(
    f"SELECT account_id, pnl, date(datetime(created_at)) AS od, "
    f"COALESCE({NORM}, datetime(created_at)) AS ct, closed_at IS NULL OR closed_at='' AS noclose "
    f"FROM trades WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0 "
    f"AND date(COALESCE({NORM}, datetime(created_at))) >= ? ORDER BY ct", (since,)).fetchall()
print("population: closed, pnl not null, non-backtest, realized >=", since, "rows:", len(rows))
try:
    eq = {(a, d): e for a, d, e in con.execute(
        "SELECT account_id, date, daily_high_equity FROM daily_risk_state WHERE date >= ?", (since,))}
    persisted = {(a, d): p for a, d, p in con.execute(
        "SELECT account_id, date, daily_pnl FROM daily_risk_state WHERE date >= ?", (since,))}
except Exception as e:
    print("daily_risk_state unreadable:", e); eq = {}; persisted = {}
per = collections.defaultdict(lambda: {"b": [], "a": []})
nocl = collections.Counter(); xday = collections.Counter(); xday_pnl = collections.defaultdict(float)
for acc, pnl, od, ct, noclose in rows:
    rd = ct[:10]
    per[(acc, rd)]["a"].append(pnl)
    if od == rd:
        per[(acc, rd)]["b"].append(pnl)
    else:
        xday[acc] += 1; xday_pnl[acc] += pnl
    if noclose: nocl[acc] += 1
def mincum(xs):
    c = 0.0; m = 0.0
    for x in xs:
        c += x; m = min(m, c)
    return m
print()
print("== per account summary (last 30d) ==")
print("account | trades | cross_day_n | cross_day_pnl | closed_at_null_n | days | sum_before | sum_after | cap_days_before | cap_days_after | days_eq_known")
by_acc = collections.defaultdict(list)
for (acc, d), v in per.items(): by_acc[acc].append((d, v))
detail = []
for acc in sorted(by_acc, key=str):
    pct, usd = CFG.get(acc, (0.0, 100))
    n = sum(len(v["a"]) for _, v in by_acc[acc])
    sb = sa = 0.0; cb = ca = 0; ek = 0
    for d, v in sorted(by_acc[acc]):
        e = eq.get((acc, d))
        cap = pct * e if (pct > 0 and e and e > 0) else usd
        if e: ek += 1
        tb, ta = sum(v["b"]), sum(v["a"])
        mb, ma = mincum(v["b"]), mincum(v["a"])
        hb, ha = mb < -cap, ma < -cap
        sb += tb; sa += ta; cb += hb; ca += ha
        if abs(tb - ta) > 0.005 or hb != ha:
            detail.append((acc, d, round(cap, 2), e is not None, round(tb, 2), round(ta, 2), round(mb, 2), round(ma, 2), hb, ha, persisted.get((acc, d))))
    print(f"{acc} | {n} | {xday[acc]} | {xday_pnl[acc]:.2f} | {nocl[acc]} | {len(by_acc[acc])} | {sb:.2f} | {sa:.2f} | {cb} | {ca} | {ek}")
print()
print("== account-days where BEFORE != AFTER (sum or cap-hit) ==")
print("account | day | cap_usd | eq_known | sum_before | sum_after | min_cum_before | min_cum_after | cap_hit_before | cap_hit_after | persisted_daily_pnl")
for r in detail: print(" | ".join(str(x) for x in r))
