#!/usr/bin/env bash
# R4 — research→results shadow-gate reporter, fired daily by
# ict-research-results-gate.timer (design §6 P1, observe-only).
#
# Runs scripts/research/research_results_gate_report.py over the live journal
# and writes the per-leg measured-net verdict report to the DATA_DIR-aware
# runtime_logs dir, so a review session (and a future endpoint) reads the
# accruing evidence trail. NOTHING is enforced — the gate stays observe-only
# until the operator flips it to enforcing (P2, Tier-3).
#
# Why resolve the out-dir via runtime_logs_dir() instead of a repo-relative
# path: the reporter reads the canonical DATA_DIR journal, and the report must
# land next to the other runtime_logs artifacts under $DATA_DIR — a
# repo-relative write would split writer/reader across the data-dir migration
# (the ict-hourly-snapshot / ict-db-integrity path-split class). The service
# ships the same data-dir drop-in for exactly this reason.
#
# Tier-1 read path — opens the journal read-only via the reporter; no order
# path, no mutation, no notification.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="run_research_results_gate"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"
# Heal a mid-run-clobbered /dev/null BEFORE any redirect. The reporter's
# `>/dev/null` (and the `2>/dev/null` on the lines just below) EACCES on a
# stripped /dev/null and false-fail this oneshot — the
# ict-research-results-gate.service journal caught exactly that on 2026-08-02,
# during the pull-and-deploy /dev/null-clobber window (BL-20260730-DEVNULL-DEPLOY-REDIRECT-FRAGILITY
# recurrence). heal_devnull is cheap when healthy and self-heals the variant-(a)
# mode-strip via `sudo -n chmod`. Never fatal here (`|| true`): if it can't heal,
# the reporter fails as it does today and the 60s ict-devnull-guard.timer is the belt.
heal_devnull || true
cd "${SCRIPT_DIR}/../.." 2>/dev/null || true
PY=python3; for c in .venv/bin/python venv/bin/python; do [ -x "$c" ] && PY="$c" && break; done
export PYTHONPATH="${PYTHONPATH:-.}"

OUT_BASE="$("$PY" -c 'from src.utils.paths import runtime_logs_dir; print(runtime_logs_dir())' 2>/dev/null || true)"
[ -n "$OUT_BASE" ] || OUT_BASE="runtime_logs"
OUT_DIR="${OUT_BASE%/}/research_results_gate"
mkdir -p "$OUT_DIR"

rc=0
for w in 7d 30d; do
  heal_devnull || true  # re-heal per iteration — the strip can land mid-run
  if ! "$PY" -m scripts.research.research_results_gate_report \
        --window "$w" --out "$OUT_DIR/report-${w}.json" --json >/dev/null; then
    echo "run_research_results_gate: reporter failed for window ${w}" >&2
    rc=1
  fi
done
exit "$rc"
