"""Exit-reconstruction validator v2 — three fixes the v1 run identified.

v1 (n=276): median |err| 1.09 bps but p90 369 / max 980. The examples named the
causes; all three are scoping defects in the estimator, not evidence against it.

  FIX 1  Anchor on the DECISION-TIME bracket (order_packages.sl/.tp), not
         trades.stop_loss. trades.stop_loss is the POST-RATCHET stop:
         trade 4076 (BTCUSDT SHORT, entry 64921.7) carries stop_loss
         64877.64 -- BELOW entry on a short, i.e. break-even already applied.
         Walking from entry with that finds a touch that never happened
         (+172 bps, called SL on a row that really hit TP).

  FIX 2  Replay the break-even ratchet forward, mirroring
         _base.monitor_breakeven_sl / the harness's --sim-breakeven: SL/TP are
         checked on the bar FIRST, then BE arms at bar CLOSE once >= 1R in
         favour. So the stop is time-varying, as it is live.

  FIX 3  Exclude intent_reduce legs. They carry the PRIMARY leg's SL/TP and are
         not bracket exits at all (trade 3650, -141 bps).

Plus a self-consistency check: closed_at is when the RECONCILER OBSERVED flat,
so it is an upper bound on the true exit. A reconstruction landing far before it
is suspect. Error is reported stratified by that flag, which tests whether the
check actually discriminates rather than assuming it does.
"""
import json
import sqlite3
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DB = '/home/ubuntu/ict-trading-bot/data/trade_journal.db'
MEASURED = {"bybit_closed_pnl", "bybit_closed_pnl_rebuild",
            "bybit_closed_pnl_backfill", "recorded_exit_price", "exchange",
            "operator_flatten_fill"}
BYBIT = "https://api.bybit.com/v5/market/kline"
STALE_MIN = 30          # recon >this many minutes before closed_at -> flagged


def to_ms(v):
    if v is None:
        return None
    s = str(v).strip().replace('T', ' ')
    if not s:
        return None
    if s.replace('.', '').isdigit() and len(s.split('.')[0]) >= 12:
        try:
            return int(float(s))
        except ValueError:
            return None
    s = s.split('+')[0].split('Z')[0].strip()
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, f)
                       .replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    return None


_cache = {}


def klines(symbol, start_ms, end_ms):
    """1m bars -> [(ts, high, low, close)] or None."""
    key = (symbol, start_ms // 60000, end_ms // 60000)
    if key in _cache:
        return _cache[key]
    out, cur, guard = [], start_ms, 0
    try:
        while cur <= end_ms and guard < 12:
            guard += 1
            url = (f"{BYBIT}?category=linear&symbol={symbol}&interval=1"
                   f"&start={cur}&end={end_ms}&limit=1000")
            req = urllib.request.Request(url, headers={"User-Agent": "recon-v2"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read().decode())
            rows = (d.get("result") or {}).get("list") or []
            if not rows:
                break
            rows = sorted(rows, key=lambda x: int(x[0]))
            for b in rows:
                out.append((int(b[0]), float(b[2]), float(b[3]), float(b[4])))
            nxt = int(rows[-1][0]) + 60000
            if nxt <= cur:
                break
            cur = nxt
            if len(rows) < 1000:
                break
    except Exception:
        _cache[key] = None
        return None
    out = sorted(set(out))
    _cache[key] = out or None
    return out or None


def reconstruct(bars, direction, entry, sl, tp, *, replay_be=True,
                be_offset_bps=0.0):
    """Harness-faithful walk. SL/TP checked on the bar first; BE arms at CLOSE.
    -> (price, reason, ts, ambiguous, be_armed) or (None, ...)"""
    d = (direction or "").lower()
    if d not in ("long", "short") or not entry:
        return None, None, None, False, False
    risk = abs(entry - sl)
    cur_sl, armed = sl, False
    for ts, hi, lo, close in bars:
        if d == "long":
            hit_sl, hit_tp = lo <= cur_sl, hi >= tp
        else:
            hit_sl, hit_tp = hi >= cur_sl, lo <= tp
        if hit_sl or hit_tp:
            amb = hit_sl and hit_tp
            if hit_sl:
                return cur_sl, ("be_stop" if armed else "sl"), ts, amb, armed
            return tp, "tp", ts, amb, armed
        if replay_be and not armed and risk > 0:
            if d == "long" and close >= entry + risk:
                cur_sl = entry * (1 + be_offset_bps / 10000.0)
                armed = True
            elif d == "short" and close <= entry - risk:
                cur_sl = entry * (1 - be_offset_bps / 10000.0)
                armed = True
    return None, None, None, False, armed


def stats(label, errs, signed, extra=""):
    n = len(errs)
    print(f"\n  --- {label}  (n={n}) {extra}")
    if n == 0:
        print(f"      (n={n} rows — vacuous, not clean)")
        return
    s = sorted(errs)
    print(f"      median |err| {statistics.median(s):8.2f} bps"
          f"    p90 {s[int(0.9*(n-1))]:8.2f}    max {s[-1]:8.2f}")
    print(f"      median signed{statistics.median(signed):8.2f} bps"
          f"    <=10bps {sum(1 for x in errs if x<=10)}/{n}"
          f"    <=50bps {sum(1 for x in errs if x<=50)}/{n}")


def main():
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT t.id, t.account_id, t.symbol, t.direction, t.entry_price,
               t.exit_price, t.stop_loss AS t_sl, t.take_profit_1 AS t_tp,
               t.exit_reason, t.created_at, t.closed_at, t.notes, t.setup_type,
               p.sl AS p_sl, p.tp AS p_tp, p.entry AS p_entry
        FROM trades t
        LEFT JOIN order_packages p ON p.order_package_id = t.order_package_id
        WHERE t.status='closed' AND COALESCE(t.is_backtest,0)=0
          AND t.exit_price IS NOT NULL AND t.exit_price > 0
        ORDER BY t.created_at DESC LIMIT 900
    """).fetchall()
    c.close()

    st = defaultdict(int)
    A = {"e": [], "s": []}          # arm A: v1 rule (journal bracket, no BE)
    Aok = {"e": [], "s": []}        # arm A, time-consistent
    Aflag = {"e": [], "s": []}      # arm A, flagged
    B = {"e": [], "s": []}          # arm B: v2 (package bracket + BE replay)
    Bok = {"e": [], "s": []}        # arm B, time-consistent only
    Bflag = {"e": [], "s": []}      # arm B, flagged
    ragree = [0, 0]
    ex = []

    for r in rows:
        st["candidates"] += 1
        try:
            nt = json.loads(r["notes"] or "{}") or {}
        except Exception:
            nt = {}
        if str(nt.get("exit_price_source") or "") not in MEASURED:
            continue
        st["ground_truth"] += 1
        # FIX 3 — reduce legs are not bracket exits
        if str(r["setup_type"] or "").lower() == "intent_reduce" or nt.get("intent_reduce"):
            st["excluded_reduce_leg"] += 1
            continue
        st["after_reduce_filter"] += 1
        sym = str(r["symbol"] or "")
        if not sym.endswith("USDT"):
            st["skipped_non_bybit"] += 1
            continue
        c0, c1 = to_ms(r["created_at"]), to_ms(r["closed_at"])
        if c0 is None or c1 is None or c1 <= c0:
            st["bad_timestamps"] += 1
            continue
        # FIX 1 — decision-time bracket from the package; journal is post-ratchet
        entry = r["p_entry"] or r["entry_price"]
        p_sl, p_tp = r["p_sl"], r["p_tp"]
        if not (p_sl and p_tp and p_sl > 0 and p_tp > 0):
            st["no_package_bracket"] += 1
            continue
        st["package_bracket_ok"] += 1
        bars = klines(sym, c0, c1 + 120000)
        if not bars:
            st["no_candles"] += 1
            continue
        st["candles_ok"] += 1
        actual = float(r["exit_price"])

        # arm A — v1 behaviour for direct comparison
        if r["t_sl"] and r["t_tp"] and r["t_sl"] > 0 and r["t_tp"] > 0:
            pa, _, ats, _, _ = reconstruct(bars, r["direction"], entry,
                                           float(r["t_sl"]), float(r["t_tp"]),
                                           replay_be=False)
            if pa:
                ae = (pa-actual)/actual*1e4
                A["e"].append(abs(ae))
                A["s"].append(ae)
                # Same time-consistency flag as arm B, so the two are comparable.
                a_flag = ((c1 - ats) / 60000.0) > STALE_MIN
                (Aflag if a_flag else Aok)["e"].append(abs(ae))
                (Aflag if a_flag else Aok)["s"].append(ae)

        # arm B — v2
        pb, reason, ts, amb, armed = reconstruct(
            bars, r["direction"], entry, float(p_sl), float(p_tp),
            replay_be=True)
        if pb is None:
            st["no_bracket_touch"] += 1
            continue
        st["bracket_resolved"] += 1
        e = (pb - actual) / actual * 1e4
        B["e"].append(abs(e))
        B["s"].append(e)
        lag_min = (c1 - ts) / 60000.0
        flagged = lag_min > STALE_MIN
        (Bflag if flagged else Bok)["e"].append(abs(e))
        (Bflag if flagged else Bok)["s"].append(e)
        if flagged:
            st["flagged_time_inconsistent"] += 1
        ar = str(r["exit_reason"] or "").lower()
        if ar in ("sl", "tp"):
            ragree[1] += 1
            if ar == reason or (ar == "sl" and reason == "be_stop"):
                ragree[0] += 1
        if len(ex) < 10:
            ex.append((r["id"], sym, r["direction"], round(actual, 4),
                       round(pb, 4), round(e, 1), ar or "-", reason,
                       "BE" if armed else "-", f"{lag_min:.0f}m",
                       "FLAG" if flagged else "ok"))

    print("=== STAGE FUNNEL (honest denominators) ===")
    for k in ("candidates", "ground_truth", "excluded_reduce_leg",
              "after_reduce_filter", "skipped_non_bybit", "bad_timestamps",
              "no_package_bracket", "package_bracket_ok", "no_candles",
              "candles_ok", "no_bracket_touch", "bracket_resolved",
              "flagged_time_inconsistent"):
        print(f"  {k:28} {st[k]}")

    print("\n=== ERROR vs BROKER TRUTH ===")
    stats("ARM A  v1 (journal bracket, no BE replay)", A["e"], A["s"])
    stats("ARM A  time-consistent only", Aok["e"], Aok["s"],
          f"<= {STALE_MIN}m before closed_at  <-- THE CANDIDATE ESTIMATOR")
    stats("ARM A  flagged (time-inconsistent)", Aflag["e"], Aflag["s"])
    stats("ARM B  v2 (package bracket + BE replay)", B["e"], B["s"])
    stats("ARM B  time-consistent only", Bok["e"], Bok["s"],
          f"<= {STALE_MIN}m before closed_at")
    stats("ARM B  flagged (time-inconsistent)", Bflag["e"], Bflag["s"],
          "-- should be WORSE if the check discriminates")
    if ragree[1]:
        print(f"\n  reason agree {ragree[0]}/{ragree[1]} "
              f"({100.0*ragree[0]/ragree[1]:.0f}%) on rows labelled sl/tp"
              f"   [n={ragree[1]} — small, do not lean on it]")
    print("\n  id | symbol | dir | actual | recon | err_bps | actual_reason | recon | be | lag | flag")
    for e in ex:
        print("   " + " | ".join(str(x) for x in e))


main()
