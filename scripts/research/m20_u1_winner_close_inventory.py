#!/usr/bin/env python3
"""M20 U1 — the per-leg inventory of every mechanism that can END a trade.

# wiring: manual-only — a research session runs this; re-run it whenever a unit
# gains or loses a close path, or a leg's exit params change.

WHAT THIS ANSWERS: for any live strategy leg, what could have cut its winner
short, and what ARMS that mechanism. MI-277 established the winner-size
collapse is an R collapse and refuted the exit levers ON ``bybit_1`` IN ITS
WINDOW; its own memo says that is not a general claim. This is the general
claim's denominator.

THE TWO GATES. A per-leg mechanism can fire only when BOTH hold:
  (a) the leg's MONITOR UNIT implements the call site, and
  (b) the leg's YAML DECLARES the arming key.
``exit_levers.py``'s docstring states (b) outright: "THE DECLARE IS STILL THE
GATE ... an undeclared leg evaluates the reference cell and writes one
observe-only row". Neither gate alone is enough, and a leg satisfying (b) but
not (a) is an ORPHANED DECLARE -- a YAML key nothing reads.

SELF-CHECKING, because a hand-written call-site table rots silently:
  * every claimed call site is ASSERTED against the file, and a missing one
    FAILS the run rather than reporting a stale inventory (RULE ONE: put the
    assertion inside the transform);
  * the monitor-unit map is IMPORTED from ``pipeline.monitor_unit_for``, never
    re-derived -- a second copy of that map is what this repo keeps paying for;
  * with ``--trades``, every mechanism OBSERVED firing on a leg is cross-checked
    against what this inventory says is armed. An observed-but-not-armed pair is
    a BUG IN THIS INVENTORY and is reported as one, not silently dropped.

NOT IN SCOPE: the account/venue-level closers (the reconciler family,
``netting_attributed``, the watchdogs). They are not per-leg -- they can end a
trade on ANY leg -- so they are listed once, in ACCOUNT_LEVEL_CLOSERS, rather
than repeated per row.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
UNIT_DIR = REPO / "src" / "units" / "strategies"

#: Per-leg close mechanisms. ``call_sites`` maps a monitor unit to a literal
#: that MUST appear in that unit's source -- the evidence that gate (a) holds.
#: ``arms`` are the config keys that satisfy gate (b); an empty list means the
#: mechanism needs no declaration (it is baseline for any unit implementing it).
MECHANISMS: Dict[str, Dict[str, Any]] = {
    "sl_cross": {
        "exit_reason": "sl_cross",
        "what": "the unit's own monitor sees price at/through the stop and closes at market",
        "call_sites": {
            "trend_donchian": '"reason": "sl_cross"',
            "htf_pullback_trend_2h": '"reason": "sl_cross"',
            "ict_scalp": '"reason": "sl_cross"',
            "turtle_soup": '"reason": "sl_cross"',
            "squeeze_breakout_4h": '"reason": "sl_cross"',
            "vwap": '"reason": "sl_cross"',
        },
        "arms": [],
        "cuts_winners": False,
        "note": "ends a winner only after the stop has trailed above entry.",
    },
    "tp_cross": {
        "exit_reason": "tp_cross",
        "what": "the unit's own monitor sees price at/through the target and closes at market",
        "call_sites": {
            "trend_donchian": '"reason": "tp_cross"',
            "htf_pullback_trend_2h": '"reason": "tp_cross"',
            "ict_scalp": '"reason": "tp_cross"',
            "squeeze_breakout_4h": '"reason": "tp_cross"',
            "vwap": '"reason": "tp_cross"',
        },
        "arms": [],
        "cuts_winners": True,
        "note": "THE designed winner-ender. Its level is min(tp_r, venue cap_r).",
    },
    "stale_stop": {
        "exit_reason": "stale_stop",
        "what": "close a position >= N native bars old that is still below the declared open-R",
        "call_sites": {
            "trend_donchian": "from src.runtime.exit_levers import stale_stop_verdict",
            "htf_pullback_trend_2h": "from src.runtime.exit_levers import giveback_verdict, stale_stop_verdict",
            "ict_scalp": "def _stale_stop_verdict(",
        },
        "arms": ["stale_exit_bars"],
        "cuts_winners": False,
        "note": ("default below_r=0.0 cuts only a still-LOSING trade, so it cannot "
                 "end a winner at the default. A DECLARED stale_exit_below_r > 0 "
                 "makes it able to. ict_scalp uses a PRIVATE copy, not the shared "
                 "lever -- see divergences[]."),
    },
    "giveback_stop": {
        "exit_reason": "giveback_stop",
        "what": "close after peak_r >= min_mfe_r once the trade gives back giveback_r from that peak",
        "call_sites": {
            "trend_donchian": "from src.runtime.exit_levers import giveback_verdict",
            "htf_pullback_trend_2h": "from src.runtime.exit_levers import giveback_verdict, stale_stop_verdict",
        },
        "arms": ["giveback_min_mfe_r"],
        "cuts_winners": True,
        "note": "by construction fires only on a trade that WAS winning. Directly in M20's subject.",
    },
    "exit_head": {
        "exit_reason": "exit_head",
        "what": "an ML exit head scores the open position each bar and may close it",
        "call_sites": {
            "trend_donchian": "exit_head_verdict(",
            "ict_scalp": "exit_head_verdict(",
        },
        "arms": ["exit_head_model"],
        "cuts_winners": True,
        "note": "can close a winner at any R. Needs a PUBLISHED head for the leg's family.",
    },
    "vwap_cross": {
        "exit_reason": "vwap_cross",
        "what": "mean-reversion target reached -- price crossed back to VWAP",
        "call_sites": {"vwap": '"reason": "vwap_cross"'},
        "arms": [],
        "cuts_winners": True,
        "note": "this family's designed winner-ender.",
    },
    "time_decay": {
        "exit_reason": "time_decay",
        "what": "close once the position exceeds a declared hold-minutes budget",
        "call_sites": {"vwap": '"reason": "time_decay"'},
        "arms": ["max_hold_minutes"],
        "cuts_winners": True,
        "note": "closes on the CLOCK, so it is indifferent to open R.",
    },
    "breakeven_ratchet": {
        "exit_reason": None,
        "what": ("MOVES THE STOP (does not close): once price reaches 1R in the trade's "
                 "favour, ratchet the stop to entry (+/- be_offset_bps)"),
        "call_sites": {
            "ict_scalp": "monitor_breakeven_sl(",
            "vwap": "monitor_breakeven_sl(",
            "turtle_soup": "monitor_breakeven_sl(",
            "hf_displacement_cont": "monitor_breakeven_sl(",
        },
        "arms": [],
        "cuts_winners": True,
        "note": ("BASELINE-ON, no declaration required -- _base.monitor_breakeven_sl "
                 "defaults one_r_threshold=1.0. It converts a would-be loser into a "
                 "scratch AND creates a whipsaw exit for a trade that touches 1R and "
                 "retraces. trend_donchian / htf_pullback_trend_2h / squeeze_breakout_4h "
                 "do NOT call it (asserted)."),
    },
    # Trail modifiers do not close a trade; they MOVE the stop, which changes
    # where sl_cross / the venue stop leg fires. Listed because "what cut this
    # winner short" is answered by the trail as often as by the closer.
    "trail_base": {
        "exit_reason": None,
        "what": "MOVES THE STOP (does not close): ATR-multiple trailing stop",
        "call_sites": {
            "trend_donchian": 'cfg_dict.get("trail_mult")',
            "htf_pullback_trend_2h": 'cfg_dict.get("trail_mult")',
        },
        "arms": ["trail_mult"],
        "cuts_winners": True,
        "note": "a tighter trail converts a would-be runner into an earlier stop-out.",
    },
    "trail_decay": {
        "exit_reason": None,
        "what": "MOVES THE STOP: tightens the trail after N stalled bars once armed at arm_r",
        "call_sites": {
            "trend_donchian": "resolve_trail_mult(",
            "htf_pullback_trend_2h": "resolve_trail_mult(",
        },
        "arms": ["trail_decay_arm_r", "trail_decay_stall_bars"],
        "cuts_winners": True,
        "note": "arms only ABOVE arm_r, i.e. exclusively on trades that are already winning.",
    },
    "trail_vol": {
        "exit_reason": None,
        "what": "MOVES THE STOP: volatility-conditional trail multiple",
        "call_sites": {
            "trend_donchian": "resolve_vol_trail_mult(",
            "htf_pullback_trend_2h": "resolve_vol_trail_mult(",
        },
        "arms": ["trail_vol_below_pctl"],
        "cuts_winners": True,
        "note": "",
    },
}

#: Closers that are NOT per-leg. Any leg on the matching venue can end this way.
ACCOUNT_LEVEL_CLOSERS: List[Dict[str, str]] = [
    {"exit_reason": "reconciler_filled", "scope": "any venue with a reconciler",
     "what": "the reconciler saw the ENTRY order filled and the position flat. It never "
             "reads the CLOSING order, so this label names no mechanism.",
     "arms": "always on"},
    {"exit_reason": "sl / tp", "scope": "any venue",
     "what": "reconciler_filled RECLASSIFIED by _classify_broker_exit comparing the exit "
             "price against the package bracket. Strict inequality -- see findings.",
     "arms": "always on"},
    {"exit_reason": "netting_attributed", "scope": "bybit (one-way netting)",
     "what": "a position-level reduction attributed across the journal rows sharing one "
             "exchange position. OI-20260908: can close a LIVE position on a false flat read.",
     "arms": "NETTING_ATTRIBUTION_MODE (apply) + NETTING_ATTRIBUTION_ACCOUNTS"},
    {"exit_reason": "exchange_flat_reconciled", "scope": "any venue",
     "what": "the position-snapshot reconciler saw the venue flat across the confirm window.",
     "arms": "always on; RECONCILER_CLOSE_CONFIRM_SECONDS tunes it"},
    {"exit_reason": "stuck_strategy_watchdog", "scope": "any venue",
     "what": "force-close after a strategy stopped evaluating.",
     "arms": "STUCK_STRATEGY_THRESHOLD_MINUTES"},
    {"exit_reason": "intent_reduce / intent_reduce_executed", "scope": "any venue",
     "what": "the intent layer reduced the position deliberately (a partial close).",
     "arms": "always on"},
    {"exit_reason": "operator_flatten_reconciled", "scope": "any venue",
     "what": "an operator flatten, reconciled afterwards.", "arms": "operator action"},
    {"exit_reason": "adopted_orphan_disappeared", "scope": "any venue",
     "what": "an adopted orphan row stopped appearing in the venue snapshot.", "arms": "always on"},
    {"exit_reason": "exit_coverage_no_strategy", "scope": "any venue",
     "what": "the exit-coverage resolver closed a row no strategy owns.", "arms": "always on"},
    {"exit_reason": "options_expiry_assignment", "scope": "options-expressing accounts",
     "what": "broker-confirmed expiry/assignment.", "arms": "OPTIONS_LIFECYCLE_LOOKBACK_DAYS"},
    {"exit_reason": "manual_closeall", "scope": "any venue",
     "what": "the UI close-all path.", "arms": "operator action"},
    {"exit_reason": "pairs_*", "scope": "the M22 pairs sleeve ONLY",
     "what": "the pairs executor owns its own exit path and does not use the coordinator fold.",
     "arms": "config/pairs.yaml"},
]

#: Divergences worth carrying, each asserted below so it cannot go stale silently.
DIVERGENCES = [
    {"id": "ict_scalp-private-stale-stop",
     "claim": "ict_scalp implements its OWN _stale_stop_verdict instead of importing the "
              "shared exit_levers one, and its copy has NO annotate path -- an undeclared "
              "ict_scalp leg therefore writes NO observe-only soak row, so no evidence "
              "accrues about what the lever would have done on it.",
     "assert_present": [("ict_scalp.py", "def _stale_stop_verdict(")],
     "assert_absent": [("ict_scalp.py", "record_exit_lever_annotation")]},
    {"id": "ict_scalp-inverted-param-precedence",
     "claim": "the shared lever reads meta FIRST then cfg; ict_scalp's private copy reads "
              "cfg FIRST then meta. Latent unless the two disagree, but it is the opposite "
              "resolution order for the same key.",
     "assert_present": [("ict_scalp.py", 'cfg_dict.get("stale_exit_bars") if cfg_dict.get("stale_exit_bars") is not None')]},
    {"id": "donchian-family-has-no-breakeven-ratchet",
     "claim": "trend_donchian, htf_pullback_trend_2h and squeeze_breakout_4h do NOT call "
              "monitor_breakeven_sl -- the break-even-at-1R ratchet is an ict_scalp / vwap / "
              "turtle_soup mechanism only. trend_donchian's monitor docstring REFERENCES it "
              "for the return contract, which is easy to misread as a call.",
     "assert_absent": [("trend_donchian.py", "monitor_breakeven_sl("),
                       ("htf_pullback_trend_2h.py", "monitor_breakeven_sl("),
                       ("squeeze_breakout_4h.py", "monitor_breakeven_sl(")]},
    {"id": "trend_donchian-stale-cfg-docstring",
     "claim": "trend_donchian's monitor docstring says run_monitor_tick 'passes cfg={} in "
              "production'. STALE: order_monitor._load_live_strategy_cfgs (M20 E3) threads "
              "live strategies.yaml into monitor() precisely so a lever declared mid-hold "
              "reaches an ALREADY-OPEN package. Field beats comment.",
     "assert_present": [("trend_donchian.py", "passes ``cfg={}`` in production")]},
    {"id": "turtle_soup-no-tp_cross",
     "claim": "turtle_soup's monitor emits sl_cross and NO tp_cross, so its winners can be "
              "ended only by the venue bracket or an account-level closer -- never by its "
              "own monitor.",
     "assert_present": [("turtle_soup.py", '"reason": "sl_cross"')],
     "assert_absent": [("turtle_soup.py", '"reason": "tp_cross"')]},
]


def _src(unit: str) -> Optional[str]:
    p = UNIT_DIR / f"{unit}.py"
    return p.read_text(encoding="utf-8") if p.exists() else None


def verify_call_sites() -> List[str]:
    """Assert every claimed call site. A missing one is a FAILURE, not a note."""
    problems: List[str] = []
    for mech, spec in MECHANISMS.items():
        for unit, needle in spec["call_sites"].items():
            body = _src(unit)
            if body is None:
                problems.append(f"{mech}: unit source {unit}.py NOT FOUND")
            elif needle not in body:
                problems.append(
                    f"{mech}: claimed call site absent from {unit}.py -- {needle!r}. "
                    "The inventory is STALE; fix the table, do not ignore this.")
    for d in DIVERGENCES:
        for fname, needle in d.get("assert_present", []):
            body = (UNIT_DIR / fname).read_text(encoding="utf-8")
            if needle not in body:
                problems.append(f"divergence {d['id']}: expected-present string missing from {fname}: {needle!r}")
        for fname, needle in d.get("assert_absent", []):
            body = (UNIT_DIR / fname).read_text(encoding="utf-8")
            if needle in body:
                problems.append(f"divergence {d['id']}: expected-ABSENT string now present in {fname}: {needle!r} "
                                "-- the divergence may be fixed; re-verify the claim.")
    return problems


def build(strategies_yaml: Path, accounts_yaml: Path) -> Dict[str, Any]:
    import yaml
    from src.runtime.pipeline import monitor_unit_for  # the ONE resolver

    legs = (yaml.safe_load(strategies_yaml.read_text(encoding="utf-8")) or {}).get("strategies") or {}
    accounts = (yaml.safe_load(accounts_yaml.read_text(encoding="utf-8")) or {}).get("accounts") or {}
    routing: Dict[str, List[str]] = collections.defaultdict(list)
    for acct_id, acct in accounts.items():
        if not isinstance(acct, dict):
            continue
        for s in (acct.get("strategies") or []):
            routing[str(s)].append(f"{acct_id}[{acct.get('mode','?')}]")

    rows = []
    for name, cfg in sorted(legs.items()):
        cfg = cfg if isinstance(cfg, dict) else {}
        try:
            unit = monitor_unit_for(name)
            unit_state = "resolved"
        # NOT swallowed — the failure is RECORDED as its own state: monitor_unit_state
        # carries "unresolved: <ExcType>" and the leg still appears with
        # monitor_unit=None, so "we could not resolve this leg's unit" stays
        # distinguishable from "this leg has no armed mechanism". A narrow except is
        # not available — monitor_unit_for resolves through an import registry and can
        # raise KeyError, ImportError or AttributeError, and a NEW failure mode must
        # surface as unresolved rather than abort the inventory for the other 54 legs.
        except Exception as exc:  # noqa: BLE001  # allow-silent: recorded as unresolved, not swallowed
            unit, unit_state = None, f"unresolved: {type(exc).__name__}"
        armed, orphaned, available = {}, [], []
        for mech, spec in MECHANISMS.items():
            implemented = unit in spec["call_sites"]
            declared_keys = [k for k in spec["arms"] if cfg.get(k) is not None]
            needs_declare = bool(spec["arms"])
            if implemented and (declared_keys or not needs_declare):
                armed[mech] = {"exit_reason": spec["exit_reason"],
                               "declared": {k: cfg.get(k) for k in declared_keys} or "baseline",
                               "can_end_a_winner": spec["cuts_winners"]}
            elif declared_keys and not implemented:
                orphaned.append({"mechanism": mech, "declared": declared_keys,
                                 "why": f"unit {unit!r} has no call site -- the key is read by nothing"})
            elif implemented and needs_declare:
                available.append(mech)
        rows.append({
            "strategy": name,
            "enabled": cfg.get("enabled"),
            "execution": cfg.get("execution", "live"),
            "timeframe": cfg.get("timeframe"),
            "monitor_unit": unit,
            "monitor_unit_state": unit_state,
            "accounts": routing.get(name) or [],
            "armed": armed,
            "available_not_declared": sorted(available),
            "orphaned_declares": orphaned,
            "winner_enders_armed": sorted(m for m, v in armed.items() if v["can_end_a_winner"]),
        })
    return {"legs": rows, "mechanisms": MECHANISMS,
            "account_level_closers": ACCOUNT_LEVEL_CLOSERS, "divergences": DIVERGENCES}


def crosscheck(inv: Dict[str, Any], trades_path: Path) -> Dict[str, Any]:
    """Positive control: every mechanism OBSERVED firing must be armed here."""
    trades = json.loads(trades_path.read_text(encoding="utf-8"))
    by_leg = {r["strategy"]: r for r in inv["legs"]}
    per_leg_reasons = {spec["exit_reason"] for spec in MECHANISMS.values() if spec["exit_reason"]}
    observed = collections.Counter()
    for t in trades:
        if t.get("status") != "closed" or t.get("is_backtest"):
            continue
        er, sn = t.get("exit_reason"), t.get("strategy_name")
        if er in per_leg_reasons:
            observed[(sn, er)] += 1
    contradictions, confirmed = [], []
    for (sn, er), n in sorted(observed.items()):
        row = by_leg.get(sn)
        mechs = [m for m, s in MECHANISMS.items() if s["exit_reason"] == er]
        armed = row is not None and any(m in row["armed"] for m in mechs)
        (confirmed if armed else contradictions).append(
            {"strategy": sn, "exit_reason": er, "n": n,
             "note": "" if armed else
                     ("OBSERVED FIRING BUT THIS INVENTORY SAYS NOT ARMED -- the inventory "
                      "is wrong, or the leg's config changed since these trades closed")})
    return {"population": {"source": str(trades_path), "rows_read": len(trades)},
            "confirmed": confirmed, "contradictions": contradictions}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategies", default=str(REPO / "config" / "strategies.yaml"))
    ap.add_argument("--accounts", default=str(REPO / "config" / "accounts.yaml"))
    ap.add_argument("--trades", help="journal trades JSON, for the observed-vs-armed cross-check")
    ap.add_argument("--out", help="write the full inventory JSON here")
    ap.add_argument("--live-only", action="store_true",
                    help="print only enabled + execution:live legs")
    args = ap.parse_args()

    problems = verify_call_sites()
    if problems:
        print("CALL-SITE VERIFICATION FAILED -- the inventory is stale:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print(f"call-site verification: OK ({sum(len(s['call_sites']) for s in MECHANISMS.values())} "
          f"sites across {len(MECHANISMS)} mechanisms, {len(DIVERGENCES)} divergences asserted)")

    inv = build(Path(args.strategies), Path(args.accounts))
    if args.trades:
        inv["crosscheck"] = crosscheck(inv, Path(args.trades))

    live = [r for r in inv["legs"]
            if r["enabled"] and str(r["execution"]).lower() == "live"]
    print(f"\nlegs: {len(inv['legs'])} declared | {len(live)} enabled+live")
    shown = live if args.live_only else inv["legs"]
    print(f"\n{'leg':28s} {'unit':22s} {'tf':5s}  winner-enders armed")
    print("-" * 104)
    for r in sorted(shown, key=lambda x: (x["monitor_unit"] or "", x["strategy"])):
        we = ",".join(r["winner_enders_armed"]) or "-"
        flag = "  ⚠ORPHANED_DECLARE" if r["orphaned_declares"] else ""
        print(f"{r['strategy']:28s} {str(r['monitor_unit']):22s} {str(r['timeframe']):5s}  {we}{flag}")

    orph = [r for r in inv["legs"] if r["orphaned_declares"]]
    if orph:
        print(f"\nORPHANED DECLARES ({len(orph)} leg(s)) — a YAML key its unit cannot read:")
        for r in orph:
            for o in r["orphaned_declares"]:
                print(f"  {r['strategy']}: {o['mechanism']} {o['declared']} — {o['why']}")

    if "crosscheck" in inv:
        cc = inv["crosscheck"]
        print(f"\ncross-check vs {cc['population']['rows_read']} journal rows: "
              f"{len(cc['confirmed'])} confirmed, {len(cc['contradictions'])} CONTRADICTION(S)")
        for c in cc["contradictions"]:
            print(f"  ⚠ {c['strategy']} fired {c['exit_reason']} × {c['n']} — {c['note']}")

    if args.out:
        Path(args.out).write_text(json.dumps(inv, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
