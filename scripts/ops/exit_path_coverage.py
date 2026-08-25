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
        --telemetry-json pt.json [--broker-json ib_open_orders.json] \
        [--bybit-broker-json bybit_open_orders.json] [--json]
    python3 scripts/ops/exit_path_coverage.py --self-test

Inputs are diag payloads so this runs from a sandbox with no VM access:
``--journal-json``   ``/api/diag/journal?table=trades&limit=1000``
``--telemetry-json`` ``/api/diag/position_telemetry?limit=200``
``--broker-json``       ``/api/diag/ib_open_orders``     (optional; IB accounts)
``--bybit-broker-json`` ``/api/diag/bybit_open_orders``  (optional; Bybit accounts)

WITHOUT a broker payload every broker path grades ``unknown``. That is the
point: this file will not print ``absent`` for something it never asked about.
The two payloads are INDEPENDENT: supplying one grades that venue and leaves
the other ``unknown``, which is the honest reading and not a partial failure.
Alpaca still has no such route, so an alpaca row grades ``unknown`` no matter
what is supplied -- see BL-20260818-NO-BRACKET-READ-SURFACE-FOR-BYBIT-OR-ALPACA.
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


# A lever whose body lives in the SHARED module is still a capability of every
# unit that imports it. Detecting capability by grepping the unit's own source
# for a close-reason literal was correct while every lever was inline, and
# became a FALSE NEGATIVE the moment `stale_stop` and `giveback_stop` moved to
# `src/runtime/exit_levers.py` — the audit would have reported the donchian
# family, which trades real money, as having LOST two mechanisms it still runs.
# The self-test's planted positive control is what caught that; without it the
# refactor would have silently degraded this file's every verdict.
_SHARED_VERDICTS = {
    "stale_stop_verdict": "stale_stop",
    "giveback_verdict": "giveback_stop",
}


def _imported_shared_reasons(unit_src: str) -> List[str]:
    """Close reasons this unit gains by importing the shared lever module.

    Matched on the imported SYMBOL, not on the module name alone: importing
    `exit_levers` for `since_entry` is not the same capability as importing
    `stale_stop_verdict`, and treating them alike would over-report.
    """
    return [reason for sym, reason in _SHARED_VERDICTS.items()
            if re.search(rf"\b{sym}\b", unit_src)]


def module_close_reasons(unit_src: str) -> List[str]:
    """Close verdicts this unit module can actually emit — inline OR shared."""
    return sorted(set(_CLOSE_A.findall(unit_src))
                  | set(_CLOSE_B.findall(unit_src))
                  | set(_imported_shared_reasons(unit_src)))


def module_reads(unit_src: str, keys: Tuple[str, ...]) -> bool:
    """Does this unit consult these cfg keys — directly, or via a shared lever?

    The shared verdict functions read the keys themselves, so a unit that calls
    one reads them transitively. Without this the keyed-mechanism detection
    below would grade an extracted lever `not_implemented`.
    """
    if any(f'"{k}"' in unit_src for k in keys):
        return True
    try:
        shared = (REPO / "src" / "runtime" / "exit_levers.py").read_text()
    except OSError:
        return False
    for sym, _reason in _SHARED_VERDICTS.items():
        if re.search(rf"\b{sym}\b", unit_src) and any(f'"{k}"' in shared
                                                       for k in keys):
            return True
    return False


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


#: read_state values meaning "this payload has nothing to say about this
#: account" -- NOT a failure, and NOT a reading. They must never displace a real
#: read of the same account from the other venue's payload, which is the one way
#: merging two broker payloads can silently destroy evidence.
_NOT_APPLICABLE_READ_STATES = frozenset({"not_ib", "not_bybit"})


def bybit_broker_index(payload: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    """Same shape as :func:`broker_index`, from ``/api/diag/bybit_open_orders``.

    BOTH COLLECTIONS ARE THE PROTECTION, and reading one is reading half. Under
    ``BYBIT_TPSL_MODE=full`` there is no resting order at all -- the stop lives
    on the POSITION row as ``stop_loss``/``take_profit``. An indexer that read
    only ``orders`` would report zero legs for a correctly-protected position
    and this audit would grade it ``absent``: the inverse of the finding, and
    worse, because it manufactures an alarm rather than missing one.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not payload:
        return out
    for acct in payload.get("accounts") or []:
        aid = acct.get("account_id")
        if not aid:
            continue
        by_sym: Dict[str, Dict[str, bool]] = {}
        result = acct.get("result") or {}
        # Full mode: protection on the position row. A symbol with a position
        # and no levels is recorded with both False -- that is a measured
        # "unprotected", distinct from the symbol simply not appearing.
        for pos in result.get("positions") or []:
            sym = str(pos.get("symbol") or "").upper()
            if not sym:
                continue
            d = by_sym.setdefault(sym, {"stop": False, "target": False})
            if pos.get("stop_loss") is not None:
                d["stop"] = True
            if pos.get("take_profit") is not None:
                d["target"] = True
        # Partial mode: qty-scoped resting legs.
        for o in result.get("orders") or []:
            sym = str(o.get("symbol") or "").upper()
            if not sym:
                continue
            d = by_sym.setdefault(sym, {"stop": False, "target": False})
            side = _bybit_leg_side(o)
            if side:
                d[side] = True
        out[aid] = {"read_state": acct.get("read_state"), "by_symbol": by_sym}
    return out


def _bybit_leg_side(order: dict) -> Optional[str]:
    """``"stop"`` / ``"target"`` / ``None`` for one resting Bybit order.

    ``stopOrderType`` is the venue's OWN classification, so it is read first and
    the shape of the order is only a fallback. TAKEPROFIT is tested before the
    stop family deliberately rather than incidentally: it is the IB ``"STP LMT"
    contains "LMT"`` lesson in the other direction, and stating the order here
    means a later edit cannot reverse it without noticing.

    ``None`` is a real answer -- an order we cannot classify contributes to
    NEITHER side, because crediting it to one would manufacture coverage.
    """
    sot = str(order.get("stop_order_type") or "").upper().replace("_", "")
    if "TAKEPROFIT" in sot:
        return "target"
    if "STOP" in sot:            # StopLoss, PartialStopLoss, TrailingStop, Stop
        return "stop"
    # A resting reduce-only LIMIT under the plain "Order" filter is a take
    # profit; it carries no stopOrderType and is invisible to the StopOrder
    # filter entirely.
    if (str(order.get("order_type") or "").upper() == "LIMIT"
            and order.get("reduce_only")):
        return "target"
    return None


def merge_broker_index(*indexes: Dict[str, Dict[str, Any]]
                       ) -> Dict[str, Dict[str, Any]]:
    """Combine per-venue broker indexes without letting a non-read overwrite a read.

    An IB account appears in the Bybit payload as ``not_bybit`` and vice versa.
    A naive ``dict.update`` would let that sentinel replace the venue's own
    ``orders_read`` entry and turn a graded account back into ``unknown``, which
    is the quiet direction of failure. A genuine conflict -- the same account
    read by BOTH payloads -- is left at whichever entry is already present and
    is NOT silently resolved; that state means the payloads disagree about what
    venue an account is, which is a finding rather than a merge question.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for idx in indexes:
        for aid, entry in (idx or {}).items():
            if entry.get("read_state") in _NOT_APPLICABLE_READ_STATES:
                continue
            out.setdefault(aid, entry)
    return out


def _broker_paths(trade: dict, bidx: Dict[str, Dict[str, Any]],
                  broker_supplied: bool) -> Tuple[str, str, str]:
    """(stop_state, target_state, basis)."""
    aid = trade.get("account_id")
    if not broker_supplied:
        return UNKNOWN, UNKNOWN, "no_broker_payload"
    acct = bidx.get(aid)
    if acct is None:
        # No supplied payload carried this account -- an alpaca row always,
        # and an IB/Bybit row when that venue's payload was not supplied.
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

    A basis here is NOT a clean negative -- it says the state was never read.

    ⚠️ THIS DOCSTRING PREVIOUSLY ASSERTED that a bybit position's resting
    brackets "cannot be read from any diag surface". That became FALSE on
    2026-08-22 when `/api/diag/bybit_open_orders` shipped, and this tool went on
    grading every bybit row unreadable for three days because the CONSUMER was
    never told -- the route existed and the one audit that reports the gap it
    closes still reported the gap. Supply `--bybit-broker-json` and those rows
    grade. ALPACA genuinely has no such route (its only order-state accessor is
    the reducing boolean `AlpacaClient.has_protective_orders`), so an alpaca row
    here IS still a missing read surface.
    """
    out: Dict[str, int] = {}
    for r in rows:
        if r["price_paths"]["broker_stop"] == UNKNOWN:
            out[r["broker_basis"]] = out.get(r["broker_basis"], 0) + 1
    return out


def audit(journal: Any, telemetry_payload: Any,
          broker_payload: Optional[dict],
          bybit_broker_payload: Optional[dict] = None) -> Dict[str, Any]:
    units, cfg, reach = load_units(), load_cfg(), load_reachability()
    unit_of, builders_src = _resolver()
    tel_rows = (telemetry_payload or {}).get("rows") or []
    telemetry = {str(r["trade_id"]): r for r in tel_rows
                 if r.get("trade_id") is not None}
    bidx = merge_broker_index(broker_index(broker_payload),
                              bybit_broker_index(bybit_broker_payload))
    # `broker_supplied` stays a single flag deliberately: it gates whether ANY
    # broker payload was offered at all, and an account absent from the ones
    # that were offered already grades `account_not_in_payload`, which names
    # the reason. Splitting it per venue would let a row read "supplied" for a
    # venue whose payload was never passed.
    supplied = broker_payload is not None or bybit_broker_payload is not None
    rows = [assess_trade(t, units=units, cfg=cfg, reach=reach,
                         telemetry=telemetry, bidx=bidx,
                         broker_supplied=supplied,
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
            "broker_supplied": supplied,
            "broker_payloads": {"ib": broker_payload is not None,
                                "bybit": bybit_broker_payload is not None},
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
        # Was a negative ("pullback emits NO stale_stop") until 2026-08-18,
        # when the lever was extracted to src/runtime/exit_levers.py and BOTH
        # families gained it. Flipped to a positive rather than deleted,
        # because it is now the control that the EXTRACTION is detected at all
        # — the body no longer appears in either unit's own source.
        ("positive: pullback gained stale_stop via the shared lever module",
         "stale_stop" in module_close_reasons(
             units.get("htf_pullback_trend_2h", ""))),
        # The replacement NEGATIVE, so the detector is not just answering yes.
        # exit_head is deliberately NOT given to this family: it needs an
        # advisory-stage trained head and the pullback family has none, so
        # shipping the plumbing would be a capability that can never fire.
        ("negative: htf_pullback_trend_2h still has NO exit_head",
         "exit_head" not in module_close_reasons(
             units.get("htf_pullback_trend_2h", ""))),
        ("negative: a unit importing only since_entry gains no close reason",
         module_close_reasons(
             "from src.runtime.exit_levers import since_entry") == []),
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
        # --- bybit half (BL-20260818): Full mode has NO resting order, so an
        # orders-only indexer would grade a protected position `absent`.
        ("positive: bybit FULL-mode position-level levels ARE protection",
         bybit_broker_index({"accounts": [{"account_id": "b",
             "read_state": "orders_read",
             "result": {"positions": [{"symbol": "SOLUSDT", "stop_loss": 1.0,
                                       "take_profit": 2.0}], "orders": []}}]}
         )["b"]["by_symbol"]["SOLUSDT"] == {"stop": True, "target": True}),
        ("positive: a bybit position with NO levels is measured unprotected, "
         "not merely absent from the index",
         bybit_broker_index({"accounts": [{"account_id": "b",
             "read_state": "orders_read",
             "result": {"positions": [{"symbol": "SOLUSDT", "stop_loss": None,
                                       "take_profit": None}], "orders": []}}]}
         )["b"]["by_symbol"]["SOLUSDT"] == {"stop": False, "target": False}),
        ("negative: PartialTakeProfit is a target, never a stop "
         "(it contains no 'STOP' -- but the order of the tests is stated, "
         "not incidental)",
         _bybit_leg_side({"stop_order_type": "PartialTakeProfit"}) == "target"),
        ("positive: PartialStopLoss and TrailingStop are both stops",
         _bybit_leg_side({"stop_order_type": "PartialStopLoss"}) == "stop"
         and _bybit_leg_side({"stop_order_type": "TrailingStop"}) == "stop"),
        ("positive: a reduce-only LIMIT with no stopOrderType is a target "
         "(invisible to the StopOrder filter entirely)",
         _bybit_leg_side({"order_type": "Limit", "reduce_only": True})
         == "target"),
        ("negative: an unclassifiable leg credits NEITHER side",
         _bybit_leg_side({"order_type": "Limit", "reduce_only": False})
         is None),
        # --- merge: the one way combining two payloads destroys evidence.
        ("negative: a not_bybit sentinel cannot overwrite a real IB read",
         merge_broker_index(
             {"ib_paper": {"read_state": "orders_read",
                           "by_symbol": {"MGC": {"stop": True, "target": True}}}},
             {"ib_paper": {"read_state": "not_bybit", "by_symbol": {}}},
         )["ib_paper"]["read_state"] == "orders_read"),
        ("negative: a not_ib sentinel cannot overwrite a real bybit read",
         merge_broker_index(
             {"bybit_2": {"read_state": "not_ib", "by_symbol": {}}},
             {"bybit_2": {"read_state": "orders_read", "by_symbol": {}}},
         )["bybit_2"]["read_state"] == "orders_read"),
        ("positive: a bybit account graded by the bybit payload is no longer "
         "account_not_in_payload",
         _broker_paths(
             {"account_id": "bybit_2", "symbol": "SOLUSDT"},
             merge_broker_index(
                 {"bybit_2": {"read_state": "not_ib", "by_symbol": {}}},
                 bybit_broker_index({"accounts": [{"account_id": "bybit_2",
                     "read_state": "orders_read",
                     "result": {"positions": [{"symbol": "SOLUSDT",
                                               "stop_loss": 1.0,
                                               "take_profit": None}],
                                "orders": []}}]})),
             True) == (LIVE, ABSENT, "orders_read")),
        ("negative: supplying only the bybit payload leaves an IB row unknown, "
         "never absent",
         _broker_paths({"account_id": "ib_paper", "symbol": "MGC"},
                       bybit_broker_index({"accounts": [{"account_id": "bybit_2",
                           "read_state": "orders_read", "result": {}}]}),
                       True)[:2] == (UNKNOWN, UNKNOWN)),
    ]
    ok = 0
    for label, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
        ok += bool(passed)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


_G = {LIVE: "LIVE", ABSENT: "—", UNKNOWN: "?", NA: "n/a"}


def _payloads_label(summary: Dict[str, Any]) -> str:
    """Name WHICH venue payloads were supplied, never just "yes".

    A bare "yes" over an IB-only payload is what let every bybit row read as
    unobservable while looking like the broker side had been checked.
    """
    have = summary.get("broker_payloads") or {}
    got = [k for k, v in sorted(have.items()) if v]
    if not got:
        return "NONE (every broker path unknown)"
    missing = [k for k, v in sorted(have.items()) if not v]
    tail = f"; {'/'.join(missing)} NOT supplied" if missing else ""
    return "/".join(got) + tail


def _render(res: Dict[str, Any]) -> None:
    s = res["summary"]
    print(f"\nOpen non-backtest trades: {res['open_trades']}   "
          f"telemetry rows matched: {s['telemetry_present']}   "
          f"broker payloads: {_payloads_label(s)}")
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
    ap.add_argument("--broker-json", help="/api/diag/ib_open_orders payload")
    ap.add_argument("--bybit-broker-json",
                    help="/api/diag/bybit_open_orders payload")
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
                _load(a.broker_json), _load(a.bybit_broker_json))
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
