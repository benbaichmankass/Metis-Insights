#!/usr/bin/env bash
# scripts/ops/prem_runs/02_demotion_evidence.sh
#
# PURPOSE. Produce the EVIDENCE that backs a Tier-3 demotion PR. Runs the
# consolidated harness over the FULL live roster on 5m history to get each
# strategy's IN-SYSTEM, net-of-fee, shared-account contribution, then feeds
# that JSON to scripts/strategy_gate.py to emit a per-strategy scorecard with
# promote/demote recommendations.
#
# This script PROPOSES; it never flips a gate. The output scorecard is the
# attachment for an operator-reviewed config/strategies.yaml PR. Expected
# verdicts (from the 2026-05-30 audit): fade_breakout_4h / turtle_soup /
# ict_scalp_5m -> PROPOSE_DEMOTE_TO_SHADOW; trend_donchian -> KEEP_LIVE.
#
# PREREQS:  $DATA_5M -> full-history 5m BTC CSV/parquet.
# Tier-1, read-only. Throttled. Idempotent.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_file DATA_5M
OUT="$(mk_outdir demotion_evidence)"
log "demotion-evidence -> $OUT"

ROSTER="trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,turtle_soup,ict_scalp_5m"
HARNESS_JSON="$OUT/system_attribution.json"
SCORECARD_JSON="$OUT/strategy_scorecard.json"

# --- full-roster system backtest -> per_strategy_attribution + return/DD ---
# backtest_system carries the multi-TF roster resampling, so it is the right
# producer for the in-system attribution the gate consumes. (Once the e2e
# reproduce in 01_*.sh signs off, swap this to `python -m sim run` over the
# roster — same JSON shape, read by strategy_gate either way.)
log "running full-roster system backtest ..."
throttle "$PY" -m scripts.backtest_system \
  --data "$DATA_5M" --roster "$ROSTER" \
  --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0 \
  --flip-policy reverse --fee-bps-roundtrip 7.5 \
  --json "$HARNESS_JSON" > "$OUT/backtest_stdout.txt" 2>&1 \
  || die "backtest_system failed — see $OUT/backtest_stdout.txt"

[[ -f "$HARNESS_JSON" ]] || die "no attribution JSON produced"

# --- selection gate: scorecard + recommendations (reads live config gates) ---
log "scoring strategies through the selection gate ..."
throttle "$PY" "$REPO_ROOT/scripts/strategy_gate.py" \
  --harness-json "$HARNESS_JSON" \
  --json-out "$SCORECARD_JSON" > "$OUT/scorecard_stdout.txt" 2>&1 \
  || die "strategy_gate failed — see $OUT/scorecard_stdout.txt"

# --- surface the proposed actions for the operator at a glance ---
log "proposed actions:"
throttle "$PY" - "$SCORECARD_JSON" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
for c in d["scorecards"]:
    print(f"  {c['strategy']:<20} {c['current_gate']:<7} -> {c['recommended_action']}")
print(f"\nNOTE: {d.get('note','')}")
PYEOF

log "scorecard: $SCORECARD_JSON"
notify "prem-demotion-evidence" 0 "scorecard ready for Tier-3 PR"
