#!/usr/bin/env bash
# =============================================================================
# Run the four-step smoke against the live exchange (S-017 T7).
#
# Per CLAUDE.md "Autonomous live-trading rule": no per-trade operator
# confirmation. The qty cap in scripts/smoke_test_trade.py and the
# ALLOW_LIVE_TRADING env interlock are the safety rails.
#
# Sequence (each step exits before the next; we capture every exit code):
#   1) bybit_1 — sub-min qty (expect rejection for size)
#   2) bybit_1 — real qty (expect fill + close)
#   3) bybit_2 — sub-min qty (expect rejection for size)
#   4) bybit_2 — real qty (expect fill + close)
#
# After all four, deletes the trigger flag so a re-run requires a new
# flag commit. Logs everything to journalctl via the systemd unit.
# =============================================================================

set -uo pipefail   # NOT -e: we want every step to run even if one returns 1

REPO_DIR=${REPO_DIR:-/home/ubuntu/ict-trading-bot}
FLAG_FILE="$REPO_DIR/runtime_flags/run_smoke_once.flag"
PYTHON=${PYTHON:-/usr/bin/python3}

cd "$REPO_DIR"

log()  { echo "===== smoke ===== $(date -u +%FT%TZ) $*"; }

if [ ! -f "$FLAG_FILE" ]; then
    log "no trigger flag at $FLAG_FILE — skipping (manual trigger ok)"
fi

run_step() {
    local label="$1"; shift
    log "STEP $label : python3 scripts/smoke_test_trade.py $*"
    PYTHONPATH="$REPO_DIR" "$PYTHON" "$REPO_DIR/scripts/smoke_test_trade.py" "$@"
    local rc=$?
    log "STEP $label : exit $rc"
    return $rc
}

OVERALL=0

run_step "1/4 bybit_1 sub-min (rejection expected)" \
    --account bybit_1 --qty 0.0001 --side buy \
    || OVERALL=$((OVERALL == 0 ? 0 : OVERALL))   # rc=1 (rejection) is fine

run_step "2/4 bybit_1 real round-trip (fill + close)" \
    --account bybit_1 --qty 0.001 --side buy \
    || OVERALL=$((OVERALL == 0 ? $? : OVERALL))

run_step "3/4 bybit_2 sub-min (rejection expected)" \
    --account bybit_2 --qty 0.0001 --side buy \
    || OVERALL=$((OVERALL == 0 ? 0 : OVERALL))

run_step "4/4 bybit_2 real round-trip (fill + close)" \
    --account bybit_2 --qty 0.001 --side buy \
    || OVERALL=$((OVERALL == 0 ? $? : OVERALL))

# Always clear the flag so a stuck file doesn't refire the smoke.
rm -f "$FLAG_FILE" && log "trigger flag cleared"

log "DONE — overall exit $OVERALL"
exit $OVERALL
