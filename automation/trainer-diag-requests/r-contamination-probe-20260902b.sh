#!/usr/bin/env bash
# R-metric contamination probe v2. READ-ONLY (sqlite opened mode=ro). No writes.
# v1 failed: `sqlite3` CLI is absent on the trainer. Use python3 stdlib.
set -uo pipefail
echo "=== ALL trade_journal candidates on this box, newest first ==="
find /home/ubuntu /data /opt -maxdepth 5 -name 'trade_journal*.db' 2>/dev/null \
  | while read -r f; do echo "$(date -u -r "$f" +%Y-%m-%dT%H:%M:%SZ)  $f"; done | sort -r
echo
python3 - <<'PY'
import glob, os, sqlite3, json, sys

cands = sorted(
    set(glob.glob('/home/ubuntu/**/trade_journal*.db', recursive=True)
        + glob.glob('/data/**/trade_journal*.db', recursive=True)),
    key=lambda p: os.path.getmtime(p), reverse=True)
if not cands:
    print("NO DB FOUND"); sys.exit(3)
DB = cands[0]
import datetime as dt
print(f"DB_PATH: {DB}")
print("DB_MTIME_UTC:", dt.datetime.utcfromtimestamp(os.path.getmtime(DB)).isoformat()+"Z")
print("!! THIS IS A SYNCED COPY. Read its max(created_at) below before quoting it as current.")
print()

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
def q(sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception as e:
        return [{"ERROR": str(e)}]
def show(title, rows, limit=200):
    print(f"--- {title} ---")
    if not rows: print("(no rows)"); print(); return
    cols = list(rows[0].keys())
    print(" | ".join(cols))
    for r in rows[:limit]:
        print(" | ".join("" if r[c] is None else str(r[c]) for c in cols))
    if len(rows) > limit: print(f"... ({len(rows)-limit} more)")
    print()

# The contamination predicate, ONE definition used everywhere below.
WRONG = ("((LOWER(direction) IN ('buy','long')  AND stop_loss > entry_price) OR "
         " (LOWER(direction) IN ('sell','short') AND stop_loss < entry_price))")
POP = ("status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0")
GRADEABLE = "stop_loss IS NOT NULL AND entry_price IS NOT NULL"

show("0. FRESHNESS", q(
  "SELECT MAX(created_at) AS max_created_at, MAX(closed_at) AS max_closed_at, "
  "COUNT(*) AS all_rows FROM trades"))

show("1. POPULATION + WRONG-SIDE", q(f"""
SELECT
 (SELECT COUNT(*) FROM trades WHERE {POP}) AS n_total,
 (SELECT COUNT(*) FROM trades WHERE {POP} AND stop_loss IS NULL) AS n_stop_null,
 (SELECT COUNT(*) FROM trades WHERE {POP} AND {GRADEABLE}) AS n_gradeable,
 (SELECT COUNT(*) FROM trades WHERE {POP} AND {GRADEABLE} AND {WRONG}) AS n_wrong_side,
 (SELECT COUNT(*) FROM trades WHERE {POP} AND {GRADEABLE} AND stop_loss=entry_price) AS n_stop_eq_entry,
 (SELECT COUNT(*) FROM trades WHERE {POP} AND LOWER(COALESCE(direction,'')) NOT IN
   ('buy','long','sell','short')) AS n_direction_unrecognised
"""))

show("1b. NEGATIVE CONTROL — the same probe on OPEN rows", q(f"""
SELECT COUNT(*) AS open_n,
 SUM(CASE WHEN {GRADEABLE} AND {WRONG} THEN 1 ELSE 0 END) AS open_wrong_side
FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"""))

show("1c. POSITIVE CONTROL — the probe MUST be able to find a clean row", q(f"""
SELECT COUNT(*) AS n_provably_clean_side FROM trades WHERE {POP} AND {GRADEABLE}
 AND ((LOWER(direction) IN ('buy','long')  AND stop_loss < entry_price)
   OR (LOWER(direction) IN ('sell','short') AND stop_loss > entry_price))"""))

show("2. CUT BY STRATEGY x ACCOUNT_CLASS", q(f"""
SELECT COALESCE(strategy_name,'(null)') AS strategy,
       COALESCE(account_class, CASE WHEN is_demo THEN 'paper' ELSE 'real_money' END) AS acct,
       COUNT(*) AS n,
       SUM(CASE WHEN {WRONG} THEN 1 ELSE 0 END) AS wrong,
       ROUND(100.0*SUM(CASE WHEN {WRONG} THEN 1 ELSE 0 END)/COUNT(*),1) AS pct
FROM trades WHERE {POP} AND {GRADEABLE}
GROUP BY 1,2 ORDER BY wrong DESC, n DESC"""))

show("3. CUT BY EXIT PATH", q(f"""
SELECT COALESCE(exit_reason,'(null)') AS exit_path, COUNT(*) AS n,
       SUM(CASE WHEN {WRONG} THEN 1 ELSE 0 END) AS wrong,
       ROUND(100.0*SUM(CASE WHEN {WRONG} THEN 1 ELSE 0 END)/COUNT(*),1) AS pct
FROM trades WHERE {POP} AND {GRADEABLE} GROUP BY 1 ORDER BY wrong DESC, n DESC"""))

show("4a. RECOVERABILITY — order_packages.sl vs trades.stop_loss", q(f"""
WITH p AS (SELECT t.id, t.direction, t.entry_price, t.stop_loss, op.sl AS pkg_sl,
                  op.meta AS pkg_meta
           FROM trades t LEFT JOIN order_packages op
             ON op.order_package_id=t.order_package_id
           WHERE t.{POP.replace('status','t.status').replace('pnl IS','t.pnl IS').replace('is_backtest','t.is_backtest')}
             AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL)
SELECT COUNT(*) AS n,
 SUM(CASE WHEN pkg_sl IS NULL THEN 1 ELSE 0 END) AS no_pkg_joined,
 SUM(CASE WHEN pkg_sl IS NOT NULL AND ABS(pkg_sl-stop_loss)<1e-9 THEN 1 ELSE 0 END) AS pkg_sl_equals,
 SUM(CASE WHEN pkg_sl IS NOT NULL AND ABS(pkg_sl-stop_loss)>=1e-9 THEN 1 ELSE 0 END) AS pkg_sl_differs,
 SUM(CASE WHEN pkg_sl IS NOT NULL AND {WRONG.replace('stop_loss','pkg_sl')} THEN 1 ELSE 0 END) AS pkg_sl_also_wrong,
 SUM(CASE WHEN pkg_meta LIKE '%risk_per_unit%' THEN 1 ELSE 0 END) AS pkg_meta_has_rpu,
 SUM(CASE WHEN pkg_meta LIKE '%risk_per_unit%' AND {WRONG} THEN 1 ELSE 0 END) AS wrong_AND_has_rpu
FROM p"""))

show("4b. position_telemetry as a recovery source?", q(
 "SELECT COUNT(*) AS telemetry_rows, "
 "SUM(CASE WHEN risk_per_unit IS NOT NULL THEN 1 ELSE 0 END) AS with_rpu, "
 "SUM(CASE WHEN terminal_state IS NOT NULL THEN 1 ELSE 0 END) AS terminal_stamped "
 "FROM position_telemetry"))

show("4c. SAMPLE wrong-side rows, biggest |pnl| first (with pkg meta risk_per_unit)", q(f"""
SELECT t.id, t.strategy_name, t.symbol, t.direction, t.entry_price, t.stop_loss,
       op.sl AS pkg_sl, t.position_size, ROUND(t.pnl,2) AS pnl, t.exit_reason,
       SUBSTR(op.meta, MAX(1, INSTR(op.meta,'risk_per_unit')), 45) AS meta_rpu
FROM trades t LEFT JOIN order_packages op ON op.order_package_id=t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
  AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL AND {WRONG.replace('direction','t.direction').replace('stop_loss','t.stop_loss').replace('entry_price','t.entry_price')}
ORDER BY ABS(t.pnl) DESC LIMIT 8"""))

show("5. R MAGNITUDE, wrong-side vs clean (contract multiplier NOT applied)", q(f"""
WITH p AS (SELECT pnl, ABS(entry_price-stop_loss)*ABS(position_size) AS risk_raw,
            CASE WHEN {WRONG} THEN 1 ELSE 0 END AS wrong
           FROM trades WHERE {POP} AND {GRADEABLE} AND position_size IS NOT NULL)
SELECT wrong, COUNT(*) AS n,
 SUM(CASE WHEN risk_raw<=0 THEN 1 ELSE 0 END) AS risk_nonpositive,
 ROUND(MAX(CASE WHEN risk_raw>0 THEN ABS(pnl)/risk_raw END),2) AS max_abs_R,
 ROUND(SUM(CASE WHEN risk_raw>0 THEN pnl/risk_raw END),2) AS sum_R
FROM p GROUP BY wrong"""))

show("6. TOP |R| rows (multiplier NOT applied — ranking only)", q(f"""
SELECT id, strategy_name, COALESCE(account_class,'') AS acct, direction,
 entry_price, stop_loss, ROUND(ABS(entry_price-stop_loss),8) AS stop_dist,
 position_size, ROUND(pnl,2) AS pnl,
 ROUND(pnl/(ABS(entry_price-stop_loss)*ABS(position_size)),2) AS R_nomult,
 exit_reason, CASE WHEN {WRONG} THEN 'WRONG' ELSE 'clean' END AS side
FROM trades WHERE {POP} AND {GRADEABLE} AND position_size IS NOT NULL
 AND ABS(entry_price-stop_loss)*ABS(position_size)>0
ORDER BY ABS(pnl/(ABS(entry_price-stop_loss)*ABS(position_size))) DESC LIMIT 15"""))
print("=== END ===")
PY
