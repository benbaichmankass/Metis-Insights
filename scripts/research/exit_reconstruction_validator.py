"""Exit-reconstruction VALIDATOR — measure the bracket-resolution estimator
against known broker fills before trusting it anywhere.

Ground truth = closed rows whose exit_price came from the broker
(exit_price_source in MEASURED_SOURCES). We HIDE that fill, reconstruct the
exit from 1m klines using the harness's own rule (walk forward from entry;
first bar touching SL -> exit at SL; first touching TP -> exit at TP; SL first
when both land in one bar), then compare.

Reports honest denominators at every stage: candidates -> ground truth ->
candles fetched -> resolved. An error stat is printed ONLY over rows that
cleared every stage, with n stated.
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
    """1m bars in [start,end]. Returns list of (ts, high, low) or None on failure."""
    key = (symbol, start_ms // 60000, end_ms // 60000)
    if key in _cache:
        return _cache[key]
    out, cur, guard = [], start_ms, 0
    try:
        while cur <= end_ms and guard < 12:
            guard += 1
            url = (f"{BYBIT}?category=linear&symbol={symbol}&interval=1"
                   f"&start={cur}&end={end_ms}&limit=1000")
            req = urllib.request.Request(url, headers={"User-Agent": "recon-validator"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.loads(r.read().decode())
            rows = (d.get("result") or {}).get("list") or []
            if not rows:
                break
            rows = sorted(rows, key=lambda x: int(x[0]))
            for b in rows:
                out.append((int(b[0]), float(b[2]), float(b[3])))
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


def reconstruct(bars, direction, sl, tp):
    """Harness rule. -> (price, reason, ts, ambiguous) or (None,...)"""
    d = (direction or "").lower()
    for ts, hi, lo in bars:
        if d == "long":
            hit_sl, hit_tp = lo <= sl, hi >= tp
        else:
            hit_sl, hit_tp = hi >= sl, lo <= tp
        if hit_sl or hit_tp:
            amb = hit_sl and hit_tp
            if hit_sl:                      # SL-first, mirrors the harness
                return sl, "sl", ts, amb
            return tp, "tp", ts, amb
    return None, None, None, False


def main():
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT id, account_id, symbol, direction, entry_price, exit_price,
               stop_loss, take_profit_1, exit_reason, created_at, closed_at,
               notes, pnl
        FROM trades
        WHERE status='closed' AND COALESCE(is_backtest,0)=0
          AND exit_price IS NOT NULL AND exit_price > 0
          AND stop_loss > 0 AND take_profit_1 > 0
        ORDER BY created_at DESC LIMIT 700
    """).fetchall()
    c.close()

    stage = defaultdict(int)
    by_venue_cov = defaultdict(lambda: [0, 0])   # venue -> [attempted, fetched]
    errs, signed, reason_hit, reason_tot, amb = [], [], 0, 0, 0
    examples = []

    for r in rows:
        stage["candidates"] += 1
        src = ""
        try:
            src = str((json.loads(r["notes"] or "{}") or {})
                      .get("exit_price_source") or "")
        except Exception:
            src = ""
        if src not in MEASURED:
            continue
        stage["ground_truth"] += 1
        sym = str(r["symbol"] or "")
        venue = "bybit" if sym.endswith("USDT") else "other(ibkr/alpaca)"
        c0, c1 = to_ms(r["created_at"]), to_ms(r["closed_at"])
        if c0 is None or c1 is None or c1 <= c0:
            stage["bad_timestamps"] += 1
            continue
        stage["timestamps_ok"] += 1
        by_venue_cov[venue][0] += 1
        if venue != "bybit":
            continue                       # public kline path is Bybit-only
        bars = klines(sym, c0, c1 + 120000)
        if not bars:
            stage["no_candles"] += 1
            continue
        by_venue_cov[venue][1] += 1
        stage["candles_ok"] += 1
        px, reason, ts, ambiguous = reconstruct(
            bars, r["direction"], float(r["stop_loss"]),
            float(r["take_profit_1"]))
        if px is None:
            stage["no_bracket_touch"] += 1
            continue
        stage["bracket_resolved"] += 1
        if ambiguous:
            amb += 1
        actual = float(r["exit_price"])
        e = (px - actual) / actual * 10_000.0
        errs.append(abs(e))
        signed.append(e)
        ar = str(r["exit_reason"] or "").lower()
        if ar in ("sl", "tp"):
            reason_tot += 1
            if ar == reason:
                reason_hit += 1
        if len(examples) < 12:
            examples.append((r["id"], r["account_id"], sym, r["direction"],
                             round(actual, 6), round(px, 6), round(e, 1),
                             ar or "-", reason))

    print("=== STAGE FUNNEL (honest denominators) ===")
    for k in ("candidates", "ground_truth", "bad_timestamps", "timestamps_ok",
              "no_candles", "candles_ok", "no_bracket_touch", "bracket_resolved"):
        print(f"  {k:20} {stage[k]}")

    print("\n=== CANDLE COVERAGE BY VENUE ===")
    for v, (a, f) in sorted(by_venue_cov.items()):
        pct = f"{100.0*f/a:.1f}%" if a else "n/a"
        note = "" if v == "bybit" else "  <-- NOT ATTEMPTED (needs gateway/other feed)"
        print(f"  {v:22} attempted={a:<5} fetched={f:<5} {pct}{note}")

    n = len(errs)
    print(f"\n=== RECONSTRUCTION ERROR vs BROKER TRUTH  (n={n}) ===")
    if n == 0:
        print(f"  *** NO ROWS CLEARED EVERY STAGE (n={n}) — result is "
              f"VACUOUS, not clean. ***")
    else:
        errs_s = sorted(errs)
        print(f"  median |err|   {statistics.median(errs_s):8.2f} bps")
        print(f"  p90    |err|   {errs_s[int(0.9*(n-1))]:8.2f} bps")
        print(f"  max    |err|   {errs_s[-1]:8.2f} bps")
        print(f"  median signed  {statistics.median(signed):8.2f} bps")
        print(f"  within 10bps   {sum(1 for x in errs if x<=10)}/{n}")
        print(f"  within 50bps   {sum(1 for x in errs if x<=50)}/{n}")
        print(f"  ambiguous(1m)  {amb}/{n}  (SL and TP in the same 1m bar)")
        if reason_tot:
            print(f"  reason agree   {reason_hit}/{reason_tot} "
                  f"({100.0*reason_hit/reason_tot:.0f}%) on rows labelled sl/tp")
        else:
            print("  reason agree   n/a (no ground-truth rows labelled sl/tp)")
        print("\n  id | account | symbol | dir | actual | recon | err_bps | actual_reason | recon_reason")
        for e in examples:
            print("   " + " | ".join(str(x) for x in e))


main()
