#!/usr/bin/env bash
# R-metric contamination probe. READ-ONLY. No writes anywhere.
set -uo pipefail

DB=""
for c in /home/ubuntu/ict-trading-bot/trade_journal.db \
         /data/bot-data/trade_journal.db \
         /home/ubuntu/data/trade_journal.db \
         /home/ubuntu/ict-trading-bot/data/trade_journal.db; do
  [ -f "$c" ] && DB="$c" && break
done
if [ -z "$DB" ]; then
  echo "DB_SEARCH: none of the canned paths exist. find results:"
  find /home/ubuntu /data -maxdepth 4 -name 'trade_journal*.db' 2>/dev/null | head -20
  exit 3
fi
echo "DB_PATH: $DB"
echo "DB_MTIME_UTC: $(date -u -r "$DB" +%Y-%m-%dT%H:%M:%SZ)"
echo

sq() { sqlite3 -readonly "file:${DB}?mode=ro" "$@" 2>&1; }

echo "=== 0. FRESHNESS (this is a SYNCED COPY; state its lag) ==="
sq "SELECT 'max_created_at', MAX(created_at) FROM trades;
    SELECT 'max_closed_at',  MAX(closed_at)  FROM trades;
    SELECT 'total_rows',     COUNT(*)        FROM trades;"
echo

echo "=== 1. POPULATION + WRONG-SIDE COUNT (all closed, pnl NOT NULL, non-backtest) ==="
sq "
WITH p AS (
  SELECT id, direction, entry_price, stop_loss, position_size, pnl,
         strategy_name, exit_reason, account_class, is_demo, account_id,
         order_package_id, closed_at, created_at
  FROM trades
  WHERE status='closed' AND pnl IS NOT NULL
    AND COALESCE(is_backtest,0)=0
)
SELECT 'n_total', COUNT(*) FROM p
UNION ALL SELECT 'n_stop_null',  COUNT(*) FROM p WHERE stop_loss IS NULL
UNION ALL SELECT 'n_entry_null', COUNT(*) FROM p WHERE entry_price IS NULL
UNION ALL SELECT 'n_gradeable',  COUNT(*) FROM p WHERE stop_loss IS NOT NULL AND entry_price IS NOT NULL
UNION ALL SELECT 'n_wrong_side', COUNT(*) FROM p
  WHERE stop_loss IS NOT NULL AND entry_price IS NOT NULL AND (
    (LOWER(direction) IN ('buy','long')  AND stop_loss > entry_price) OR
    (LOWER(direction) IN ('sell','short') AND stop_loss < entry_price))
UNION ALL SELECT 'n_at_entry_exact', COUNT(*) FROM p
  WHERE stop_loss IS NOT NULL AND entry_price IS NOT NULL AND stop_loss = entry_price
UNION ALL SELECT 'n_direction_unrecognised', COUNT(*) FROM p
  WHERE LOWER(COALESCE(direction,'')) NOT IN ('buy','long','sell','short');
"
echo

echo "=== 1b. NEGATIVE CONTROL — same probe on OPEN rows (trailing has had less time) ==="
sq "
WITH o AS (SELECT * FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0)
SELECT 'open_n', COUNT(*) FROM o
UNION ALL SELECT 'open_wrong_side', COUNT(*) FROM o
  WHERE stop_loss IS NOT NULL AND entry_price IS NOT NULL AND (
    (LOWER(direction) IN ('buy','long')  AND stop_loss > entry_price) OR
    (LOWER(direction) IN ('sell','short') AND stop_loss < entry_price));
"
echo

echo "=== 2. CUT BY STRATEGY (closed, pnl NOT NULL, non-backtest, gradeable) ==="
sq -header -column "
WITH p AS (
  SELECT * FROM trades
  WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0
    AND stop_loss IS NOT NULL AND entry_price IS NOT NULL
)
SELECT COALESCE(strategy_name,'(null)') AS strategy,
       COALESCE(account_class,'(null)') AS acct_class,
       COUNT(*) AS n,
       SUM(CASE WHEN (LOWER(direction) IN ('buy','long') AND stop_loss>entry_price)
                  OR (LOWER(direction) IN ('sell','short') AND stop_loss<entry_price)
                THEN 1 ELSE 0 END) AS wrong_side,
       ROUND(100.0*SUM(CASE WHEN (LOWER(direction) IN ('buy','long') AND stop_loss>entry_price)
                  OR (LOWER(direction) IN ('sell','short') AND stop_loss<entry_price)
                THEN 1 ELSE 0 END)/COUNT(*),1) AS pct
FROM p GROUP BY 1,2 HAVING COUNT(*)>0 ORDER BY wrong_side DESC, n DESC;
"
echo

echo "=== 3. CUT BY EXIT PATH ==="
sq -header -column "
WITH p AS (
  SELECT * FROM trades
  WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0
    AND stop_loss IS NOT NULL AND entry_price IS NOT NULL
)
SELECT COALESCE(exit_reason,'(null)') AS exit_path, COUNT(*) AS n,
       SUM(CASE WHEN (LOWER(direction) IN ('buy','long') AND stop_loss>entry_price)
                  OR (LOWER(direction) IN ('sell','short') AND stop_loss<entry_price)
                THEN 1 ELSE 0 END) AS wrong_side,
       ROUND(100.0*SUM(CASE WHEN (LOWER(direction) IN ('buy','long') AND stop_loss>entry_price)
                  OR (LOWER(direction) IN ('sell','short') AND stop_loss<entry_price)
                THEN 1 ELSE 0 END)/COUNT(*),1) AS pct
FROM p GROUP BY 1 ORDER BY wrong_side DESC, n DESC;
"
echo

echo "=== 4. RECOVERABILITY — does order_packages preserve an independent initial risk? ==="
echo "-- 4a: does order_packages.sl differ from trades.stop_loss? (both are overwritten by _apply_update,"
echo "--     but the package write is skipped when not every leg amends, so a DIFFERENCE is informative)"
sq "
WITH p AS (
  SELECT t.id, t.direction, t.entry_price, t.stop_loss, t.order_package_id, op.sl AS pkg_sl, op.meta AS pkg_meta
  FROM trades t LEFT JOIN order_packages op ON op.order_package_id = t.order_package_id
  WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
    AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL
)
SELECT 'n', COUNT(*) FROM p
UNION ALL SELECT 'no_pkg_joined',   COUNT(*) FROM p WHERE pkg_sl IS NULL
UNION ALL SELECT 'pkg_sl_equals_trade_sl', COUNT(*) FROM p WHERE pkg_sl IS NOT NULL AND ABS(pkg_sl - stop_loss) < 1e-9
UNION ALL SELECT 'pkg_sl_differs',  COUNT(*) FROM p WHERE pkg_sl IS NOT NULL AND ABS(pkg_sl - stop_loss) >= 1e-9
UNION ALL SELECT 'pkg_sl_ALSO_wrong_side', COUNT(*) FROM p WHERE pkg_sl IS NOT NULL AND (
    (LOWER(direction) IN ('buy','long')  AND pkg_sl > entry_price) OR
    (LOWER(direction) IN ('sell','short') AND pkg_sl < entry_price))
UNION ALL SELECT 'pkg_meta_has_risk_per_unit', COUNT(*) FROM p WHERE pkg_meta LIKE '%risk_per_unit%'
UNION ALL SELECT 'WRONGSIDE_and_meta_has_rpu', COUNT(*) FROM p
  WHERE pkg_meta LIKE '%risk_per_unit%' AND (
    (LOWER(direction) IN ('buy','long')  AND stop_loss > entry_price) OR
    (LOWER(direction) IN ('sell','short') AND stop_loss < entry_price));
"
echo
echo "-- 4b: is position_telemetry a recovery source for CLOSED rows?"
sq "SELECT 'telemetry_rows', COUNT(*) FROM position_telemetry;
    SELECT 'telemetry_with_risk_per_unit', COUNT(*) FROM position_telemetry WHERE risk_per_unit IS NOT NULL;"
echo
echo "-- 4c: sample 5 wrong-side rows WITH their pkg meta risk_per_unit, to see if recovery reconciles"
sq -header -line "
SELECT t.id, t.strategy_name, t.symbol, t.direction, t.entry_price, t.stop_loss, op.sl AS pkg_sl,
       t.position_size, t.pnl, t.exit_reason,
       SUBSTR(op.meta, MAX(1, INSTR(op.meta,'risk_per_unit')-2), 60) AS meta_rpu_window
FROM trades t LEFT JOIN order_packages op ON op.order_package_id=t.order_package_id
WHERE t.status='closed' AND t.pnl IS NOT NULL AND COALESCE(t.is_backtest,0)=0
  AND t.stop_loss IS NOT NULL AND t.entry_price IS NOT NULL
  AND ((LOWER(t.direction) IN ('buy','long') AND t.stop_loss>t.entry_price)
    OR (LOWER(t.direction) IN ('sell','short') AND t.stop_loss<t.entry_price))
ORDER BY ABS(t.pnl) DESC LIMIT 5;
"
echo
echo "=== 5. R MAGNITUDE — how extreme does R get on near-breakeven stops? ==="
sq -header -column "
WITH p AS (
  SELECT strategy_name, account_class, pnl,
         ABS(entry_price-stop_loss)*ABS(position_size) AS risk_raw,
         CASE WHEN (LOWER(direction) IN ('buy','long') AND stop_loss>entry_price)
                OR (LOWER(direction) IN ('sell','short') AND stop_loss<entry_price)
              THEN 1 ELSE 0 END AS wrong
  FROM trades
  WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0
    AND stop_loss IS NOT NULL AND entry_price IS NOT NULL AND position_size IS NOT NULL
)
SELECT wrong, COUNT(*) AS n,
       SUM(CASE WHEN risk_raw<=0 THEN 1 ELSE 0 END) AS risk_zero,
       ROUND(MAX(CASE WHEN risk_raw>0 THEN ABS(pnl)/risk_raw END),2) AS max_abs_R_nocontract,
       ROUND(SUM(CASE WHEN risk_raw>0 THEN pnl/risk_raw END),2) AS sum_R_nocontract
FROM p GROUP BY wrong;
"
echo
echo "=== 6. TOP |R| ROWS (contract multiplier NOT applied — ranking only, not a quotable R) ==="
sq -header -column "
SELECT id, strategy_name, account_class, direction, entry_price, stop_loss,
       ROUND(ABS(entry_price-stop_loss),8) AS stop_dist, position_size, ROUND(pnl,2) AS pnl,
       ROUND(pnl/(ABS(entry_price-stop_loss)*ABS(position_size)),2) AS R_nocontract, exit_reason
FROM trades
WHERE status='closed' AND pnl IS NOT NULL AND COALESCE(is_backtest,0)=0
  AND stop_loss IS NOT NULL AND entry_price IS NOT NULL AND position_size IS NOT NULL
  AND ABS(entry_price-stop_loss)*ABS(position_size) > 0
ORDER BY ABS(pnl/(ABS(entry_price-stop_loss)*ABS(position_size))) DESC LIMIT 12;
"
echo "=== END ==="
