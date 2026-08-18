#!/usr/bin/env python3
"""Does THIS live trade have a path to close — and can any of them close it on a DECISION?

The prior two audits are per-LEG and read only config:

* ``lever_reachability_audit.py`` (M31 P1) — can a DECLARED R-threshold lever
  arm under its own TP cap?
* ``exit_mechanism_coverage.py`` — does the leg's unit module IMPLEMENT that
  lever at all?

Neither asks the question an operator actually holds a position against:
**for this open trade, right now, what can close it?** A leg can grade clean on
both of the above and still be carrying a trade whose only remaining exit is a
resting stop nobody chose to place there weeks ago.

THE DISTINCTION THIS SCRIPT EXISTS TO DRAW
------------------------------------------
``price_level`` — fires only when price REACHES a level fixed at entry: the
broker stop, the broker target, the monitor's own ``sl_cross`` / ``tp_cross``.
It cannot act on anything learned since entry. A trade holding through three
weeks of chop, never approaching either level, has these paths "live" and is
nonetheless un-closeable.

``decision`` — fires on evidence ACCUMULATED after entry: ``stale_stop`` (this
has gone nowhere), ``giveback_stop`` (it gave back its gain), ``exit_head`` (a
model says leave), ``trail_decay`` (tighten because the move stalled),
``time_decay`` / ``vwap_cross`` (the thesis has aged out).

**A trade with price paths and no decision path cannot be closed by the system
for any reason other than price arriving somewhere.** That is the state the
motivating XRP short was in for 20 days, and it is invisible to every existing
surface: the leg grades ``ok`` on reachability (its arms are honest), ``ok`` on
mechanism coverage (it declares only what its module reads), and the trade shows
a populated stop_loss and take_profit_1 in the journal.

FOUR STATES PER PATH, NEVER COLLAPSED
--------------------------------------
``live`` · ``absent`` (we looked; it is not there) · ``unknown`` (we could not
look — a missing ``--broker-json`` is NOT evidence of an absent bracket) ·
``not_applicable`` (no such path exists for this venue/module — not a failure).

WHAT THIS DOES NOT DO
---------------------
Reports. Decides nothing, changes nothing, opens no socket. Every arm value and
every new declare remains Tier-3.

Usage
-----
    python3 scripts/ops/exit_path_coverage.py --journal-json trades.json \
        --telemetry-json pt.json [--broker-json ib_open_orders.json] [--json]
    python3 scripts/ops/exit_path_coverage.py --self-test

Inputs are diag payloads so this runs from a sandbox with no VM access:
``--journal-json``   ``/api/diag/journal?table=trades&limit=1000``
``--telemetry-json`` ``/api/diag/position_telemetry?limit=200``
``--broker-json``    ``/api/diag/ib_open_orders``  (optional; IB accounts only)

WITHOUT ``--broker-json`` every broker path grades ``unknown``. That is the
point: this file will not print ``absent`` for something it never asked about.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

_UNITS = REPO / "src" / "units" / "strategies"
_STRATEGIES = REPO / "config" / "strategies.yaml"
_REGISTRY = REPO / "config" / "lever_reachability.json"

# Four path states. `unknown` is "we could not look" and is never `absent`.
LIVE, ABSENT, UNKNOWN, NA = "live", "absent", "unknown", "not_applicable"

# position_telemetry's upsert reads
#   peak_r = MAX(COALESCE(stored, -1e18), COALESCE(incoming, -1e18))
# so a row whose peak has NEVER been measurable (peak_state `thin_window`)
# stores the SENTINEL, not NULL — and `peak_pct_of_cap` then derives ~-7.6e19.
# Any consumer taking a MIN or a distribution over peak_r gets a value never
# observed. Filed as BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL;
# until that lands, refuse the value rather than render it.
_SENTINEL_ABS = 1e17


def _sane(v):
    """A telemetry float, or None when it is the sentinel / not a number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if abs(f) >= _SENTINEL_ABS else f
PATH_STATES = (LIVE, ABSENT, UNKNOWN, NA)

# Trade-level verdicts. `price_only` is the finding; `no_exit_path` is an alarm;
# `unknown` is a read failure and must never be reported as either of the above.
VERDICTS = ("decision_exit_live", "price_only", "no_exit_path", "unknown")

# Which close reasons are decision-driven. A reason absent from here is a
# price-level path. `trail_decay` is a decision path even though it acts by
# moving a stop: it can SHORTEN a hold on post-entry evidence, which is the
# capacity being audited.
_DECISION_REASONS = {
    "stale_stop", "giveback_stop", "exit_head", "time_decay",
    "vwap_cross", "trail_decay", "regime_flip",
}

# `trail_decay` emits no close verdict (it retightens the trail), so it cannot
# be discovered by scanning for close reasons. Detect it the way
# exit_mechanism_coverage does — by the cfg keys the module reads.
_KEYED_MECHANISMS: Dict[str, Tuple[str, ...]] = {
    "trail_decay": ("trail_decay_tight_mult", "trail_decay_arm_r"),
    "stale_stop": ("stale_exit_bars",),
    "giveback_stop": ("giveback_r", "giveback_min_mfe_r"),
    "exit_head": ("exit_head", "exit_head_threshold"),
}

_CLOSE_A = re.compile(
    r'"action"\s*:\s*"close"\s*,\s*"reason"\s*:\s*"([a-z0-9_]+)"')
_CLOSE_B = re.compile(
    r'"reason"\s*:\s*"([a-z0-9_]+)"\s*,\s*"action"\s*:\s*"close"')


def module_close_reasons(unit_src: str) -> List[str]:
    """Close verdicts this unit module can actually emit."""
    return sorted(set(_CLOSE_A.findall(unit_src)) | set(_CLOSE_B.findall(unit_src)))


def module_reads(unit_src: str, keys: Tuple[str, ...]) -> bool:
    return any(f'"{k}"' in unit_src for k in keys)


# --------------------------------------------------------------------------
# leg -> unit resolution. Reuse exit_mechanism_coverage's two-witness resolver
# rather than writing a second one that is free to disagree with it.
# --------------------------------------------------------------------------
def _resolver():
    try:
        import exit_mechanism_coverage as emc  # type: ignore
        return emc.unit_of, (REPO / "src" / "runtime"
                             / "strategy_signal_builders.py").read_text()
    except Exception:  # noqa: BLE001
        return None, ""


def load_units() -> Dict[str, str]:
    return {p.stem: p.read_text() for p in _UNITS.glob("*.py")}


def load_cfg() -> Dict[str, Any]:
    try:
        import yaml
        cfg = yaml.safe_load(_STRATEGIES.read_text())
        return cfg.get("strategies", cfg) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_reachability() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_REGISTRY.read_text())
    except (OSError, ValueError):
        return {}
    return {lev["strategy"]: lev for lev in data.get("levers", [])
            if lev.get("strategy")}


# --------------------------------------------------------------------------
# Broker-side reality
# --------------------------------------------------------------------------
def broker_index(payload: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    """account_id -> {read_state, by_symbol:{sym:{stop:bool,target:bool}}}.

    Mirrors /api/diag/ib_open_orders's own three-state `read_state`; a
    `could_not_look` account yields UNKNOWN for every trade on it, never ABSENT.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not payload:
        return out
    for acct in payload.get("accounts") or []:
        aid = acct.get("account_id")
        if not aid:
            continue
        state = acct.get("read_state")
        by_sym: Dict[str, Dict[str, bool]] = {}
        for o in acct.get("orders") or []:
            sym = str(o.get("symbol") or "").upper()
            t = str(o.get("order_type") or "").upper()
            d = by_sym.setdefault(sym, {"stop": False, "target": False})
            # Stop family FIRST: "STP LMT" contains "LMT", and filing a
            # stop-limit as a take-profit would MANUFACTURE target coverage.
            if "STP" in t or "TRAIL" in t:
                d["stop"] = True
            elif "LMT" in t:
                d["target"] = True
        out[aid] = {"read_state": state, "by_symbol": by_sym}
    return out


def _broker_paths(trade: dict, bidx: Dict[str, Dict[str, Any]],
                  broker_supplied: bool) -> Tuple[str, str, str]:
    """(stop_state, target_state, basis)."""
    aid = trade.get("account_id")
    if not broker_supplied:
        return UNKNOWN, UNKNOWN, "no_broker_payload"
    acct = bidx.get(aid)
    if acct is None:
        # The payload covered IB accounts; a bybit/alpaca row is simply not in it.
        return UNKNOWN, UNKNOWN, "account_not_in_payload"
    if acct.get("read_state") != "orders_read":
        return UNKNOWN, UNKNOWN, f"read_state:{acct.get('read_state')}"
    sym = str(trade.get("symbol") or "").upper()
    hit = acct["by_symbol"].get(sym)
    if hit is None:
        # A confirmed clean read that lists nothing for this symbol IS evidence.
        return ABSENT, ABSENT, "orders_read:no_leg_for_symbol"
    return (LIVE if hit["stop"] else ABSENT,
            LIVE if hit["target"] else ABSENT, "orders_read")


# --------------------------------------------------------------------------
# Per-trade assessment
# --------------------------------------------------------------------------
def assess_trade(trade: dict, *, units: Dict[str, str], cfg: Dict[str, Any],
                 reach: Dict[str, Dict[str, Any]],
                 telemetry: Dict[str, Dict[str, Any]],
                 bidx: Dict[str, Dict[str, Any]],
                 broker_supplied: bool,
                 unit_of=None, builders_src: str = "") -> Dict[str, Any]:
    strat = trade.get("strategy_name") or trade.get("setup_type") or ""
    tid = str(trade.get("id"))
    row: Dict[str, Any] = {
        "trade_id": tid, "strategy": strat, "symbol": trade.get("symbol"),
        "account_id": trade.get("account_id"),
        "account_class": trade.get("account_class"),
        "opened_at": trade.get("timestamp"),
        "price_paths": {}, "decision_paths": {},
    }

    unit, basis = (unit_of(builders_src, strat) if unit_of else (None, "no_resolver"))
    row["unit"], row["unit_basis"] = unit, basis
    usrc = units.get(unit or "", "")

    # ---- price-level paths ------------------------------------------------
    stop_state, tgt_state, bbasis = _broker_paths(trade, bidx, broker_supplied)
    row["broker_basis"] = bbasis
    row["price_paths"]["broker_stop"] = stop_state
    row["price_paths"]["broker_target"] = tgt_state

    reasons = module_close_reasons(usrc) if usrc else []
    row["module_close_reasons"] = reasons
    has_sl = trade.get("stop_loss") not in (None, "", 0)
    has_tp = trade.get("take_profit_1") not in (None, "", 0)
    for reason, level_present in (("sl_cross", has_sl), ("tp_cross", has_tp),
                                  ("tp2_cross", trade.get("take_profit_2") not in (None, "", 0))):
        if not usrc:
            row["price_paths"][f"monitor_{reason}"] = UNKNOWN
        elif reason not in reasons:
            row["price_paths"][f"monitor_{reason}"] = NA
        else:
            row["price_paths"][f"monitor_{reason}"] = LIVE if level_present else ABSENT

    # ---- decision paths ---------------------------------------------------
    tel = telemetry.get(tid)
    if tel:
        sentinel = any(_sane(tel.get(k)) is None and tel.get(k) is not None
                       for k in ("peak_r", "peak_pct_of_cap"))
        row["telemetry"] = {
            "present": True, "cap_r": _sane(tel.get("cap_r")),
            "peak_r": _sane(tel.get("peak_r")), "open_r": _sane(tel.get("open_r")),
            "rr_from_here": _sane(tel.get("rr_from_here")),
            "pct_of_cap": _sane(tel.get("pct_of_cap")),
            "peak_pct_of_cap": _sane(tel.get("peak_pct_of_cap")),
            "arm_reach": tel.get("arm_reach"),
            "peak_state": tel.get("peak_state"),
            "bars_held": tel.get("bars_held"),
            "bars_since_peak": tel.get("bars_since_peak"),
            "sentinel_peak": sentinel,
        }
    else:
        row["telemetry"] = {"present": False, "sentinel_peak": False}
    lcfg = cfg.get(strat) or {}

    # (a) mechanisms discovered by the cfg keys their module reads
    for mech, keys in _KEYED_MECHANISMS.items():
        if not usrc:
            row["decision_paths"][mech] = {"state": UNKNOWN, "why": "unit_unresolved"}
            continue
        if not module_reads(usrc, keys):
            row["decision_paths"][mech] = {"state": NA, "why": "not_implemented"}
            continue
        if not any(lcfg.get(k) is not None for k in keys):
            row["decision_paths"][mech] = {"state": ABSENT, "why": "undeclared"}
            continue
        verdict = (reach.get(strat) or {}).get("verdict")
        arm = (tel or {}).get("arm_reach")
        if arm == "unreachable" or verdict == "inert":
            state, why = ABSENT, f"declared_but_unreachable(leg={verdict},trade={arm})"
        elif arm == "reachable":
            # THIS trade's own cap clears the arm. The only per-trade yes there is.
            state, why = LIVE, f"declared_and_reachable(leg={verdict},trade={arm})"
        else:
            # No per-trade cap to check it against. A leg-level `reachable` or
            # `vol_conditional` is a statement about the leg's TYPICAL entry
            # risk, not about this fill — and cap_R = 0.099*entry/risk varies
            # per fill, which is exactly why 4163 and 4164 on the same leg
            # disagree. Grading LIVE off the leg verdict would assert a
            # reachability nobody measured.
            state, why = UNKNOWN, (
                f"declared_reachability_unmeasured_for_this_trade"
                f"(leg={verdict},trade={arm})")
        row["decision_paths"][mech] = {"state": state, "why": why}

    # (b) mechanisms that ARE close verdicts (time_decay, vwap_cross, ...)
    for reason in sorted(set(reasons) & _DECISION_REASONS):
        if reason in row["decision_paths"]:
            continue
        row["decision_paths"][reason] = {"state": LIVE, "why": "module_close_verdict"}
    if not usrc:
        row["decision_paths"].setdefault(
            "module_verdicts", {"state": UNKNOWN, "why": "unit_unresolved"})

    row["capital"] = capital_view(row, trade.get("timestamp"))

    # ---- verdict ----------------------------------------------------------
    dstates = [c["state"] for c in row["decision_paths"].values()]
    pstates = list(row["price_paths"].values())
    if LIVE in dstates:
        row["verdict"] = "decision_exit_live"
    elif UNKNOWN in dstates:
        row["verdict"] = "unknown"
    elif LIVE in pstates:
        row["verdict"] = "price_only"
    elif UNKNOWN in pstates:
        row["verdict"] = "unknown"
    else:
        row["verdict"] = "no_exit_path"
    return row


# Why a trade ended up price-only. Distinct causes want distinct remedies:
# a leg that never DECLARED a mechanism its module implements is a config
# change; a family whose module implements nothing is engineering work; a
# declared-but-inert arm is a Tier-3 value decision. Collapsing them into one
# "no decision exit" count is what makes the finding unactionable.
CAUSES = ("all_undeclared", "family_not_implemented",
          "declared_but_unreachable", "mixed", "unattributed")


def price_only_cause(row: Dict[str, Any]) -> str:
    whys = [c.get("why", "") for c in row["decision_paths"].values()]
    if not whys:
        return "unattributed"
    undecl = sum(w == "undeclared" for w in whys)
    notimp = sum(w == "not_implemented" for w in whys)
    unreach = sum(w.startswith("declared_but_unreachable") for w in whys)
    if unreach and not notimp:
        return "declared_but_unreachable"
    if notimp and (unreach or undecl):
        return "mixed"
    if notimp:
        return "family_not_implemented"
    if undecl == len(whys):
        return "all_undeclared"
    return "mixed"


def capital_view(row: Dict[str, Any], opened_at: Optional[str],
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The 'should we keep holding THIS?' block, from telemetry the trade already has.

    Three quantities the exit question turns on, none of which any surface
    computed before:

    * ``r_per_day`` — realised R divided by days of capital locked. The
      comparison the operator's capital-utilisation framing needs: a trade is
      not judged on its R, it is judged on its R against the book's other uses
      of the same capital.
    * ``upside_left_r = cap_r - open_r`` — the structural ceiling is
      ``cap_R = 0.099*entry/risk`` (the venue TP sentinel clamp), so a trade at
      80% of cap has 0.8R of headroom left NO MATTER how long it is held. This
      is the term that makes "hold for the target" quantifiable rather than
      hopeful.
    * ``rr_from_here`` — telemetry's own ``r_to_target / r_to_stop``: upside
      left against give-back at risk. Below 1.0 the trade risks more than it
      stands to make FROM HERE, which is compatible with it having been a good
      trade so far.

    ``stalled_bars`` (bars_since_peak) separates "still working" from "has not
    made a new extreme in weeks" — a distinction invisible in open_r alone.

    Every field is None when its input is missing. Nothing here is a decision.
    """
    t = row.get("telemetry") or {}
    out: Dict[str, Any] = {"days_held": None, "r_per_day": None,
                           "upside_left_r": None, "rr_from_here": t.get("rr_from_here"),
                           "pct_of_cap": t.get("pct_of_cap"),
                           "stalled_bars": t.get("bars_since_peak"),
                           "giveback_from_peak_r": None}
    if not t.get("present"):
        return out
    import datetime as _dt
    days = None
    if opened_at:
        try:
            o = _dt.datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            n = (_dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                 if now_iso else _dt.datetime.now(_dt.timezone.utc))
            days = (n - o).total_seconds() / 86400.0
        except (ValueError, TypeError):
            days = None
    out["days_held"] = round(days, 2) if days else None
    o_r, c_r, p_r = t.get("open_r"), t.get("cap_r"), t.get("peak_r")
    if o_r is not None and days and days > 0:
        out["r_per_day"] = round(o_r / days, 4)
    if o_r is not None and c_r is not None:
        out["upside_left_r"] = round(c_r - o_r, 4)
    if o_r is not None and p_r is not None:
        out["giveback_from_peak_r"] = round(p_r - o_r, 4)
    return out


def _open_rows(payload: Any) -> List[dict]:
    rows = payload if isinstance(payload, list) else (
        payload.get("rows") or payload.get("trades") or [])
    return [r for r in rows
            if str(r.get("status")) == "open" and not r.get("is_backtest")]


def _cause_rollup(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for r in rows:
        if r["verdict"] != "price_only":
            continue
        out.setdefault(price_only_cause(r), []).append(r["trade_id"])
    return out


def _broker_rollup(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many open trades have UNOBSERVABLE broker-bracket state, and why.

    `/api/diag/ib_open_orders` is IB-only, so a bybit or alpaca position's
    resting brackets cannot be read from any diag surface — `not_ib` here is
    not a clean negative, it is a missing read surface.
    """
    out: Dict[str, int] = {}
    for r in rows:
        if r["price_paths"]["broker_stop"] == UNKNOWN:
            out[r["broker_basis"]] = out.get(r["broker_basis"], 0) + 1
    return out


def audit(journal: Any, telemetry_payload: Any,
          broker_payload: Optional[dict]) -> Dict[str, Any]:
    units, cfg, reach = load_units(), load_cfg(), load_reachability()
    unit_of, builders_src = _resolver()
    tel_rows = (telemetry_payload or {}).get("rows") or []
    telemetry = {str(r["trade_id"]): r for r in tel_rows
                 if r.get("trade_id") is not None}
    bidx = broker_index(broker_payload)
    rows = [assess_trade(t, units=units, cfg=cfg, reach=reach,
                         telemetry=telemetry, bidx=bidx,
                         broker_supplied=broker_payload is not None,
                         unit_of=unit_of, builders_src=builders_src)
            for t in _open_rows(journal)]
    by_verdict: Dict[str, int] = {v: 0 for v in VERDICTS}
    for r in rows:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    return {
        "open_trades": len(rows), "rows": rows,
        "verdicts": VERDICTS, "path_states": PATH_STATES,
        "summary": {
            "by_verdict": by_verdict,
            "telemetry_present": sum(1 for r in rows if r["telemetry"]["present"]),
            "sentinel_peak_rows": [r["trade_id"] for r in rows
                                   if r["telemetry"].get("sentinel_peak")],
            "broker_supplied": broker_payload is not None,
            "price_only_trades": [r["trade_id"] for r in rows
                                  if r["verdict"] == "price_only"],
            "no_exit_path_trades": [r["trade_id"] for r in rows
                                    if r["verdict"] == "no_exit_path"],
            "price_only_causes": _cause_rollup(rows),
            "broker_observability": _broker_rollup(rows),
        },
    }


# --------------------------------------------------------------------------
def _self_test() -> int:
    """Planted controls. A probe that cannot find a known positive proves nothing."""
    units = load_units()
    checks = [
        ("positive: trend_donchian emits stale_stop",
         "stale_stop" in module_close_reasons(units.get("trend_donchian", ""))),
        ("negative: htf_pullback_trend_2h emits NO stale_stop",
         "stale_stop" not in module_close_reasons(
             units.get("htf_pullback_trend_2h", ""))),
        ("positive: htf_pullback_trend_2h DOES read trail_decay keys",
         module_reads(units.get("htf_pullback_trend_2h", ""),
                      _KEYED_MECHANISMS["trail_decay"])),
        ("negative: a STP LMT order is filed as a stop, never a target",
         broker_index({"accounts": [{"account_id": "a", "read_state": "orders_read",
                                     "orders": [{"symbol": "X", "order_type": "STP LMT"}]}]}
                      )["a"]["by_symbol"]["X"] == {"stop": True, "target": False}),
        ("negative: no broker payload yields unknown, never absent",
         _broker_paths({"account_id": "a", "symbol": "X"}, {}, False)[:2]
         == (UNKNOWN, UNKNOWN)),
        ("positive: a clean read with no leg for the symbol IS absent",
         _broker_paths({"account_id": "a", "symbol": "X"},
                       {"a": {"read_state": "orders_read", "by_symbol": {}}},
                       True)[:2] == (ABSENT, ABSENT)),
        ("negative: could_not_look yields unknown, never absent",
         _broker_paths({"account_id": "a", "symbol": "X"},
                       {"a": {"read_state": "could_not_look", "by_symbol": {}}},
                       True)[:2] == (UNKNOWN, UNKNOWN)),
    ]
    ok = 0
    for label, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
        ok += bool(passed)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


_G = {LIVE: "LIVE", ABSENT: "—", UNKNOWN: "?", NA: "n/a"}


def _render(res: Dict[str, Any]) -> None:
    s = res["summary"]
    print(f"\nOpen non-backtest trades: {res['open_trades']}   "
          f"telemetry rows matched: {s['telemetry_present']}   "
          f"broker payload: {'yes' if s['broker_supplied'] else 'NO (broker paths unknown)'}")
    print("\nverdicts: " + "  ".join(
        f"{k}={v}" for k, v in s["by_verdict"].items()))
    hdr = (f"\n{'trade':>6} {'strategy':22} {'symbol':10} {'bStop':>5} {'bTgt':>5} "
           f"{'sl':>4} {'tp':>4} {'decision paths live':38} {'peakR':>6} {'%cap':>5} verdict")
    print(hdr)
    print("-" * (len(hdr) + 8))
    for r in sorted(res["rows"], key=lambda x: (x["verdict"] != "no_exit_path",
                                                x["verdict"] != "price_only",
                                                x["strategy"])):
        live = [k for k, c in r["decision_paths"].items() if c["state"] == LIVE]
        unk = [k for k, c in r["decision_paths"].items() if c["state"] == UNKNOWN]
        cell = ",".join(live) if live else ("?" + ",".join(unk) if unk else "NONE")
        t = r["telemetry"]
        pk = f"{t['peak_r']:.2f}" if t.get("peak_r") is not None else "—"
        pc = (f"{t['peak_pct_of_cap']:.0f}%"
              if t.get("peak_pct_of_cap") is not None else "—")
        print(f"{r['trade_id']:>6} {r['strategy'][:22]:22} {str(r['symbol'])[:10]:10} "
              f"{_G[r['price_paths']['broker_stop']]:>5} "
              f"{_G[r['price_paths']['broker_target']]:>5} "
              f"{_G[r['price_paths'].get('monitor_sl_cross', UNKNOWN)]:>4} "
              f"{_G[r['price_paths'].get('monitor_tp_cross', UNKNOWN)]:>4} "
              f"{cell[:38]:38} {pk:>6} {pc:>5} {r['verdict']}")
    if s["price_only_trades"]:
        print(f"\nPRICE-ONLY (no decision-driven exit): "
              f"{len(s['price_only_trades'])} trade(s) -> "
              f"{', '.join(s['price_only_trades'])}")
        print("  These can close ONLY by price reaching a level fixed at entry.")
    ranked = [r for r in res["rows"] if r["capital"].get("r_per_day") is not None]
    if ranked:
        ranked.sort(key=lambda r: -r["capital"]["r_per_day"])
        print("\n  capital utilisation — R earned per day of capital locked, best first.")
        print("  `left` is upside remaining to the structural cap; `rr` below 1.0 means")
        print("  the trade risks more than it stands to make FROM HERE.")
        print(f"\n  {'trade':>6} {'strategy':22} {'days':>5} {'openR':>6} {'R/day':>7} "
              f"{'left':>6} {'rr':>5} {'stall':>5} verdict")
        for r in ranked:
            c = r["capital"]
            f = lambda v, n=2: ("—" if v is None else f"{v:.{n}f}")  # noqa: E731
            print(f"  {r['trade_id']:>6} {r['strategy'][:22]:22} "
                  f"{f(c['days_held'], 1):>5} {f(r['telemetry'].get('open_r')):>6} "
                  f"{f(c['r_per_day'], 3):>7} {f(c['upside_left_r']):>6} "
                  f"{f(c['rr_from_here']):>5} {('—' if c['stalled_bars'] is None else c['stalled_bars']):>5} "
                  f"{r['verdict']}")
    if s["price_only_causes"]:
        print("\n  cause breakdown (distinct causes want distinct remedies):")
        for cause, ids in sorted(s["price_only_causes"].items(),
                                 key=lambda kv: -len(kv[1])):
            print(f"    {cause:26s} {len(ids):>3}  {', '.join(ids)}")
    if s["broker_observability"]:
        print("\n  broker-bracket state UNOBSERVABLE for "
              f"{sum(s['broker_observability'].values())} open trade(s):")
        for basis, n in sorted(s["broker_observability"].items(),
                               key=lambda kv: -kv[1]):
            print(f"    {basis:26s} {n:>3}")
    if s["sentinel_peak_rows"]:
        print(f"\n  telemetry peak_r SENTINEL (-1e18, not a measurement) on "
              f"{len(s['sentinel_peak_rows'])} row(s): "
              f"{', '.join(s['sentinel_peak_rows'])}")
    if s["no_exit_path_trades"]:
        print(f"\nNO EXIT PATH AT ALL: {', '.join(s['no_exit_path_trades'])}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal-json")
    ap.add_argument("--telemetry-json")
    ap.add_argument("--broker-json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fail-on-price-only", action="store_true",
                    help="exit 1 if any open trade has no decision-driven exit")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.journal_json:
        ap.error("--journal-json is required (or --self-test)")

    def _load(p):
        return json.loads(Path(p).read_text()) if p else None
    res = audit(_load(a.journal_json), _load(a.telemetry_json) or {},
                _load(a.broker_json))
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        _render(res)
    if a.fail_on_price_only and (res["summary"]["price_only_trades"]
                                 or res["summary"]["no_exit_path_trades"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
