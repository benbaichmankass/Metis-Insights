#!/usr/bin/env bash
# scripts/ops/prem_runs/03_spx_retune.sh
#
# PURPOSE. Test whether the one proven winner, trend_donchian, holds up on an
# UNCORRELATED market (SPX / MES futures) — the diversification idea from the
# portfolio audit. Sweeps the donchian params on SPX history and reports the
# best-by-return/DD variant, so the operator can decide whether to route a
# second instance of the proven strategy to a market that does not co-move
# with the BTC book.
#
# This is EVIDENCE-GATHERING (Tier-1), not a live change. Wiring trend_donchian
# to an SPX account is a Tier-3 config/strategies.yaml + config/accounts.yaml PR
# the operator approves after seeing these numbers.
#
# PREREQS:  $DATA_SPX -> SPX/MES OHLCV (CSV/parquet, ascending) at the donchian
#           native TF (2h). If you only have a finer TF, pre-resample first.
#           If $DATA_SPX is unset/missing this script EXITS CLEANLY with a note
#           — SPX data acquisition is its own upstream task.
# Tier-1, read-only. Throttled. Idempotent.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if [[ -z "${DATA_SPX:-}" || ! -f "${DATA_SPX:-/nonexistent}" ]]; then
  log "DATA_SPX unset or missing — SPX/MES history not yet acquired."
  log "Acquire SPX 2h OHLCV first, then: DATA_SPX=/path/spx_2h.csv $0"
  notify "prem-spx-retune" 3 "deferred: no SPX data"
  exit 0   # clean no-op, not a failure — data is an upstream prerequisite
fi

OUT="$(mk_outdir spx_retune)"
log "SPX re-tune -> $OUT"

# Donchian param grid (entry/exit lookback). Kept small to be polite on a
# shared VM; widen once dedicated cores are confirmed.
SPEC="$OUT/donchian_spx_spec.json"
cat > "$SPEC" <<'JSON'
{"variants": [
  {"name": "dc_20_10", "overrides": {"trend_donchian.entry_lookback": 20, "trend_donchian.exit_lookback": 10}},
  {"name": "dc_30_15", "overrides": {"trend_donchian.entry_lookback": 30, "trend_donchian.exit_lookback": 15}},
  {"name": "dc_40_20", "overrides": {"trend_donchian.entry_lookback": 40, "trend_donchian.exit_lookback": 20}},
  {"name": "dc_55_20", "overrides": {"trend_donchian.entry_lookback": 55, "trend_donchian.exit_lookback": 20}}
]}
JSON

log "running donchian sweep on SPX ..."
throttle "$PY" -m sim sweep \
  --candles "$DATA_SPX" --spec "$SPEC" --symbol SPX --timeframe 2h \
  --fee-bps 7.5 --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0 \
  --out-root "$OUT/sweep" --run-id spx_donchian > "$OUT/sweep_stdout.txt" 2>&1 \
  || die "sim sweep failed — see $OUT/sweep_stdout.txt"

# --- rank variants by return/DD and surface the winner ---
log "ranking variants by return/DD ..."
throttle "$PY" - "$OUT/sweep" <<'PYEOF' || true
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
rows = []
for f in root.rglob("*.json"):
    try:
        d = json.loads(f.read_text())
    except Exception:
        continue
    acct = d.get("account", d)
    rdd = acct.get("return_over_dd", d.get("return_dd_ratio"))
    if rdd is None:
        continue
    rows.append((rdd, d.get("run_id") or d.get("name") or f.stem,
                 acct.get("net_pnl"), d.get("total_trades")))
rows.sort(reverse=True, key=lambda r: (r[0] is not None, r[0]))
print(f"{'variant':<14}{'ret/DD':>10}{'net_pnl':>12}{'trades':>9}")
for rdd, name, pnl, n in rows:
    print(f"{str(name):<14}{rdd!s:>10}{pnl!s:>12}{n!s:>9}")
if rows:
    print(f"\nBEST: {rows[0][1]} (ret/DD={rows[0][0]})")
PYEOF

log "SPX sweep artifacts: $OUT/sweep"
notify "prem-spx-retune" 0 "donchian/SPX sweep ranked"
