#!/usr/bin/env python3
"""Harness-lever-map coupling guard (BL-20260730-HARNESS-LEVER-MAP-COUPLING-GUARD).

THE COUPLING BUG THIS CLOSES. `scripts/research/regime_debt_matrix.py` decides a
strategy's backtest FIDELITY by classifying every `config/strategies.yaml` key as
PLAIN (the harness models it inherently), a LEVER_FLAG (the harness models it via a
CLI flag), or _UNREPLAYABLE (no offline harness can replay it). Anything ELSE is
treated as an *omitted lever* → the row silently degrades to `approximate`. That
last branch is IMPLICIT: add a new tuning key to a live strategy and the debt
matrix quietly drops that strategy to `approximate` — blocking (or, worse,
silently weakening the evidence behind) any regime cell authored off it, with
nobody deciding whether the key SHOULD have been modelled. That is exactly the
"falls-through-the-cracks" failure this repo keeps paying for.

THE GUARD. Fail-CLOSED, like `env-gate-guard`: every key on an ENABLED,
harness-classified strategy MUST be EXPLICITLY accounted for in exactly one of

    family PLAIN  |  family LEVER_FLAG  |  _UNREPLAYABLE  |  family UNMODELLED

where the per-family UNMODELLED registries below enumerate the keys a harness
DELIBERATELY does not model (so the row degrades to `approximate` on purpose — the
honest outcome, named in `omitted_levers`). A key in NONE of the four is an
unclassified coupling gap: the guard errors and names the strategy + key, forcing a
human to make the call — model it (add to PLAIN/LEVER_FLAG + wire the flag), mark it
UNMODELLED here (deliberate omission), or _UNREPLAYABLE.

The PLAIN / LEVER_FLAG / _UNREPLAYABLE maps are imported from regime_debt_matrix so
there is ONE source of truth; this guard adds only the explicit UNMODELLED registry
(the branch that was implicit). Only strategies `classify()` routes to a real
harness (trend / pullback / squeeze) are in scope — a strategy family the debt
matrix does not model at all (e.g. ict_scalp, vwap) has no lever maps to check
against and is skipped.

Usage:
    python3 scripts/check_harness_lever_coupling.py            # whole-config audit
    python3 scripts/check_harness_lever_coupling.py --config path/to/strategies.yaml
Exit 0 = every classified strategy's keys are accounted for; 1 = a coupling gap.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO = str(Path(__file__).resolve().parents[1])
if REPO not in sys.path:
    sys.path.insert(0, REPO)

_RDM_PATH = os.path.join(REPO, "scripts", "research", "regime_debt_matrix.py")
_spec = importlib.util.spec_from_file_location("regime_debt_matrix", _RDM_PATH)
_rdm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rdm)

# --- Explicit UNMODELLED registries -----------------------------------------
# Keys present on LIVE, enabled strategies that the family's harness does NOT
# model — so a row carrying one degrades to `approximate` (the key is named in
# omitted_levers). This is the EXPLICIT declaration the coupling gap needs: it
# converts the debt matrix's implicit "anything-not-in-PLAIN/LEVER_FLAG is
# omitted" branch into a reviewed allowlist. Adding a key here is a DELIBERATE
# "we know this harness omits it"; the alternatives are to MODEL it (add to the
# family PLAIN/LEVER_FLAG in regime_debt_matrix + wire the flag) or mark it
# _UNREPLAYABLE. Keep each key's rationale in the debt matrix's own map comments.
#
# NOTE many of these ARE modelled by a DIFFERENT family (e.g. the pullback harness
# models vol_skip_* / trail_vol_*; the squeeze harness models giveback_*) —
# "unmodelled" is always relative to THIS family's harness.
#
# ⚠️ `timeout_bars` IS THE ONE ENTRY HERE WHOSE REASON IS THE OPPOSITE OF THE OTHERS,
# and this comment said the wrong thing until 2026-08-29. It previously implied only
# the squeeze harness models `timeout_bars`. MEASURED by reading all three parsers:
# `--timeout-bars` exists in backtest_trend.py:982 (default 200),
# backtest_pullback.py:961 (200) AND backtest_squeeze.py:545 (48) — every family
# models it, via the same flag. `regime_debt_matrix._SQZ_LEVER_FLAG` maps it; the
# trend/pullback LEVER_FLAG maps do not.
#
# ⚠️ DO NOT "FIX" THIS BY MOVING `timeout_bars` INTO _TREND_LEVER_FLAG / _PB_LEVER_FLAG.
# The code plainly justifies that move and it is the wrong direction. NO LIVE
# trend/pullback unit implements a bar-count exit — `timeout_bars` is read only by
# fvg_range_15m.py and fade_breakout_4h.py, each from its own _DEFAULTS, with no
# generic reader — so live's effective timeout is INFINITE. Promoting the key to
# LEVER_FLAG would stop regime_debt_matrix naming it in `omitted_levers` and would
# UPGRADE any trend/pullback leg declaring the key from `approximate` to full
# fidelity for a key production never reads.
#
# ⚠️ NO ENABLED trend/pullback LEG CARRIES THE KEY ANY MORE (2026-08-29,
# operator-approved): `mgc_pullback_1d` and `mhg_pullback_1d` declared
# `timeout_bars: 200` and NOTHING read it -- not the live unit, and not the
# pullback branch of `m20_fleet_exit_sweep.base_args` either (only its `squeeze`
# and `fvg` branches emit `--timeout-bars`; verified by calling base_args on both
# legs with and without the key and diffing the arg list). Both were deleted.
# The entries below therefore have no carrier today and are DELIBERATELY KEPT:
# they are what stops a future leg re-declaring the key from tripping the guard
# as an unclassified gap, and deleting them would make that a silent re-entry.
#
# So the key stays UNMODELLED, for a corrected reason: not that the harness cannot
# model it, but that the harness models an exit the LIVE unit does not have, so
# replaying the config key faithfully would make the backtest LESS like production.
# MEASURED 2026-08-29 (scripts/research/timeout_binding_audit.py over
# docs/research/e35-bracket-corpus.jsonl, 41 legs / 1,588 graded pairs): that
# force-close BINDS on 27.6% of pairs and 18 of 41 legs, so this is not academic.
# BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES
# Write-up: docs/research/timeout-bars-harness-vs-live-2026-08-29.md
_TREND_UNMODELLED: Set[str] = {
    "atr_stop_buffer", "confirm_bars", "giveback_min_mfe_r", "giveback_r",
    "pierce_min", "skip_hours", "timeout_bars",
    "trail_decay_arm_r", "trail_decay_stall_bars", "trail_decay_tight_mult",
    "trail_vol_below_pctl", "trail_vol_tight_mult", "vol_pctl_window",
    "vol_skip_above_pctl", "vol_skip_below_pctl",
}
_PB_UNMODELLED: Set[str] = {
    "skip_hours", "timeout_bars",
    "trail_decay_arm_r", "trail_decay_stall_bars", "trail_decay_tight_mult",
}
# `tp_r` on a squeeze strategy is DELIBERATELY not in _SQZ_PLAIN — backtest_squeeze.py
# has no --tp-r flag (the Chandelier trail is the sole profit-exit). regime_debt_matrix
# special-cases it (_SQZ_TP_R_NONBINDING): it counts as an omitted lever only when set
# near enough to actually bind. Either way it is a KNOWN, handled key, so it is
# classified here rather than left to trip the guard.
_SQZ_UNMODELLED: Set[str] = {"tp_r"}

_FAMILY_MAPS: Dict[str, Tuple[Set[str], Set[str], Set[str]]] = {
    # family -> (PLAIN, LEVER_FLAG keys, UNMODELLED)
    "trend": (_rdm._TREND_PLAIN, set(_rdm._TREND_LEVER_FLAG), _TREND_UNMODELLED),
    "pullback": (_rdm._PB_PLAIN, set(_rdm._PB_LEVER_FLAG), _PB_UNMODELLED),
    "squeeze": (_rdm._SQZ_PLAIN, set(_rdm._SQZ_LEVER_FLAG), _SQZ_UNMODELLED),
}


def _is_enabled(cfg: dict) -> bool:
    return cfg.get("enabled", True) not in (False, "false", "False", 0, "0")


def find_coupling_gaps(strategies: Dict[str, dict]) -> List[Tuple[str, str, str]]:
    """Return [(strategy, family, unclassified_key), ...] over enabled, classified
    strategies whose config carries a key in NONE of PLAIN|LEVER_FLAG|_UNREPLAYABLE|UNMODELLED."""
    gaps: List[Tuple[str, str, str]] = []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or not _is_enabled(cfg):
            continue
        family = _rdm.classify(cfg)
        if family not in _FAMILY_MAPS:
            continue  # unclassifiable family — no lever maps to check against
        plain, lever, unmodelled = _FAMILY_MAPS[family]
        for key in cfg:
            if (key in plain or key in lever or key in _rdm._UNREPLAYABLE
                    or key in unmodelled):
                continue
            gaps.append((name, family, key))
    return gaps


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(REPO, "config", "strategies.yaml"))
    args = ap.parse_args(argv)

    import yaml
    doc = yaml.safe_load(Path(args.config).read_text())
    strategies = (doc or {}).get("strategies", doc) if isinstance(doc, dict) else {}
    if not isinstance(strategies, dict):
        print("::error::could not read a strategies mapping from the config", file=sys.stderr)
        return 2

    gaps = find_coupling_gaps(strategies)
    if not gaps:
        n = sum(1 for c in strategies.values()
                if isinstance(c, dict) and _is_enabled(c) and _rdm.classify(c) in _FAMILY_MAPS)
        print(f"harness-lever-coupling: OK — every key on {n} enabled, "
              f"harness-classified strateg{'y' if n == 1 else 'ies'} is "
              f"classified (PLAIN | LEVER_FLAG | _UNREPLAYABLE | UNMODELLED).")
        return 0

    print("::error::harness-lever-map coupling gap "
          "(BL-20260730-HARNESS-LEVER-MAP-COUPLING-GUARD): the following "
          "config key(s) on enabled, harness-classified strategies are in NONE of "
          "the family's PLAIN / LEVER_FLAG maps, _UNREPLAYABLE, or the guard's "
          "UNMODELLED registry — so regime_debt_matrix would silently degrade the "
          "row to `approximate` with nobody deciding. Classify each: MODEL it "
          "(regime_debt_matrix family PLAIN/LEVER_FLAG + wire the flag), mark it "
          "UNMODELLED in scripts/check_harness_lever_coupling.py (deliberate "
          "omission), or _UNREPLAYABLE.", file=sys.stderr)
    for name, family, key in sorted(gaps):
        print(f"  {name} [{family}]: unclassified key '{key}'", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
