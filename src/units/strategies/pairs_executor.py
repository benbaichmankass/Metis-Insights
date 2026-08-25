"""Isolated 2-leg executor for the market-neutral pairs sleeve (M22 D2).

The pairs sleeve does NOT fit the single-symbol intent model (a pair is two
simultaneous opposite legs). Following the prop-bridge pattern, it runs as its own
once-per-tick hook (``run_pairs_tick``), never through ``multi_account_execute``.

This module is split into:
  * a PURE decision core (``decide_pair``) — given the two legs' candles, the
    pair's current open-state, the set of leg-symbols already held by other open
    pairs (the disjoint-legs concurrency gate), and the execution mode, it returns
    a ``PairDecision`` (event + intended 2-leg orders + soak fields). Fully
    unit-tested, no I/O.
  * a thin live I/O layer (``run_pairs_tick`` + ``_place_pair`` / ``_close_pair``)
    that reconstructs open-state from the journal, fetches candles, calls
    ``decide_pair``, and — only for an ``execution: live`` pair on a real account —
    places/closes the legs atomically (leg-imbalance unwind on partial failure),
    journals both legs linked by a shared ``pairs_group_id``, and writes the soak.

``monitor()`` returns ``None`` by design: the executor owns the joint spread-exit,
so the per-package order-monitor must NOT independently close a pairs leg. Each
leg still carries a wide catastrophe-backstop SL/TP on the exchange.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.provenance import UNMEASURED_MARKER as _UNMEASURED_MARKER
from src.units.strategies import pairs_engine as pe
from src.units.strategies import pairs_sizing as psz
from src.utils.json_notes import dump_capped
from src.utils.json_notes import load_notes as _decode_notes

logger = logging.getLogger(__name__)

# Timeframe → bar-length in seconds (for the bars-held / max-hold timeout).
_TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                      "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


@dataclass(frozen=True)
class LegOrder:
    symbol: str
    direction: str          # "long" | "short"
    qty: float
    entry_ref: float        # latest close (market entry reference)
    sl: float
    tp: float


@dataclass
class PairDecision:
    event: str                              # skip_flat|skip_concurrency|skip_size|open|hold|close|shadow_*
    pair: str
    soak: Dict[str, Any] = field(default_factory=dict)
    legs: List[LegOrder] = field(default_factory=list)   # intended orders for an OPEN
    close: bool = False                                   # True → close the open pair


def _pair_label(a: str, b: str) -> str:
    return f"{a}/{b}"


def decide_pair(params: pe.PairParams, close_a: Sequence[float], close_b: Sequence[float],
                *, open_state: Optional[pe.OpenPair], held_symbols: set,
                risk_budget_usd: float, correlation_open: int,
                execution_mode: str = "live", corr_factor: float = 0.5,
                backstop_mult: float = 3.0,
                min_leg_notional_usd: float = 10.0) -> PairDecision:
    """PURE decision for one pair this tick. No I/O. `execution_mode` 'shadow'
    downgrades an would-be open/close to a shadow_* soak event with the legs still
    computed (observe-only). Returns a PairDecision."""
    label = _pair_label(params.symbol_a, params.symbol_b)
    base = {"symbol_a": params.symbol_a, "symbol_b": params.symbol_b,
            "execution_mode": execution_mode}

    # --- IN A POSITION: check exit ---
    if open_state is not None:
        ex = pe.exit_signal(close_a, close_b, params, open_state)
        if ex is None:
            return PairDecision("hold", label, soak={**base, "bars_held": open_state.bars_held})
        ev = "shadow_close" if execution_mode == "shadow" else "close"
        return PairDecision(ev, label, close=(execution_mode != "shadow"),
                            soak={**base, "outcome": ex.get("outcome"),
                                  "exit_spread": ex.get("exit_spread"),
                                  "bars_held": open_state.bars_held})

    # --- FLAT: check entry ---
    sig = pe.entry_signal(close_a, close_b, params)
    if sig is None:
        return PairDecision("skip_flat", label, soak={**base})
    # disjoint-legs concurrency gate
    if params.symbol_a in held_symbols or params.symbol_b in held_symbols:
        return PairDecision("skip_concurrency", label,
                            soak={**base, "z": sig["z"], "direction": sig["direction"],
                                  "held": sorted(held_symbols)})
    # size (with the correlation haircut for already-open correlated pairs)
    haircut = psz.correlation_haircut(correlation_open, factor=corr_factor)
    budget = float(risk_budget_usd) * haircut
    price_a, price_b = float(close_a[-1]), float(close_b[-1])
    sizing = psz.pair_notionals(budget, sig["risk"], sig["beta"], price_a, price_b)
    # Skip when a leg can't be sized to a placeable order: qty must be positive
    # AND each leg's $ notional must clear the exchange minimum (rounding a
    # sub-min leg up would break the market-neutral hedge ratio — the qty=0 /
    # sub-min refusals seen live, BL-20260716-PAIRS-EXEC). A large risk_spread
    # (unstable rolling beta) shrinks the notional; this skips rather than
    # placing a broken order.
    min_notional = float(min_leg_notional_usd)
    if (sizing["qty_a"] <= 0 or sizing["qty_b"] <= 0
            or sizing["notional_a_usd"] < min_notional
            or sizing["notional_b_usd"] < min_notional):
        return PairDecision("skip_size", label,
                            soak={**base, "z": sig["z"], "risk": sig["risk"],
                                  "budget_usd": round(budget, 2), "haircut": haircut,
                                  "notional_a_usd": round(sizing["notional_a_usd"], 2),
                                  "notional_b_usd": round(sizing["notional_b_usd"], 2),
                                  "min_leg_notional_usd": min_notional})
    legdirs = pe.leg_directions(sig["direction"])
    sl_a, tp_a = psz.leg_protective_levels(legdirs["a"], price_a, sig["risk"], backstop_mult)
    sl_b, tp_b = psz.leg_protective_levels(legdirs["b"], price_b, sig["risk"], backstop_mult)
    legs = [
        LegOrder(params.symbol_a, legdirs["a"], round(sizing["qty_a"], 8), price_a, sl_a, tp_a),
        LegOrder(params.symbol_b, legdirs["b"], round(sizing["qty_b"], 8), price_b, sl_b, tp_b),
    ]
    ev = "shadow_open" if execution_mode == "shadow" else "open"
    soak = {**base, "z": sig["z"], "direction": sig["direction"], "beta": sig["beta"],
            "risk": sig["risk"], "entry_spread": sig["entry_spread"], "stop_spread": sig["stop_spread"],
            "budget_usd": round(budget, 2), "haircut": haircut, "correlation_open": correlation_open,
            "pairs_group_id": f"pair-{uuid.uuid4().hex[:12]}",
            "legs": [leg.__dict__ for leg in legs]}
    return PairDecision(ev, label, legs=legs, soak=soak)


def monitor(cfg, candles_df, open_pkg):  # noqa: ANN001
    """The executor owns the joint spread-exit; the per-package order-monitor must
    NOT independently close a pairs leg. Always None (the wide per-leg backstop
    SL/TP on the exchange remains the last-resort net)."""
    return None


# =====================================================================
# LIVE I/O LAYER  —  run_pairs_tick + placement/close/reconstruction.
# Called once per trader tick from src/main.py (best-effort, never raises).
# `execution: shadow` (the sanctioned strategy-level gate) → compute + soak,
# place NOTHING. `execution: live` → place the two legs on the account.
# =====================================================================

_PAIRS_CONFIG_PATH = os.environ.get("PAIRS_CONFIG_PATH") or "config/pairs.yaml"


def _bar_seconds(timeframe: str) -> int:
    return _TIMEFRAME_SECONDS.get(str(timeframe or "1h").strip().lower(), 3600)


def _params_from_cfg(pair: Dict[str, Any]) -> pe.PairParams:
    """Build a PairParams from one config entry (defaults match the validated
    backtest params: lookback 15, entry_z 2.0, exit_z 0.5, stop_z 2.0,
    max_hold_bars 20, rolling hedge-beta)."""
    return pe.PairParams(
        symbol_a=str(pair["symbol_a"]),
        symbol_b=str(pair["symbol_b"]),
        lookback=int(pair.get("lookback", 15)),
        entry_z=float(pair.get("entry_z", 2.0)),
        exit_z=float(pair.get("exit_z", 0.5)),
        stop_z=float(pair.get("stop_z", 2.0)),
        max_hold_bars=int(pair.get("max_hold_bars", 20)),
        hedge_beta=str(pair.get("hedge_beta", "rolling")),
    )


def _load_pairs_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load config/pairs.yaml → {account_id, pairs_risk_fraction,
    correlation_haircut_factor, backstop_mult, min_leg_notional_usd, pairs:[...]}.
    Returns an empty dict (a no-op tick) when the file is absent or unparseable —
    the sleeve is inert until it's authored. Note: the per-pair risk budget is
    NOT in this file — it's derived at tick time from the account's live balance ×
    risk_pct × pairs_risk_fraction (no hardcoded dollar basis)."""
    p = path or _PAIRS_CONFIG_PATH
    if not os.path.exists(p):
        return {}
    try:
        import yaml
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:  # noqa: BLE001 — inert on any config error
        logger.warning("pairs: config load failed (%s): %s", p, exc)
        return {}


def _leg_strats(pair: Dict[str, Any]) -> tuple:
    """(strategy_a, strategy_b) journal names for the two legs of a pair."""
    name = str(pair.get("name") or f"pairs_{pair['symbol_a']}_{pair['symbol_b']}".lower())
    return (f"{name}_a", f"{name}_b")


def _pair_leg_state(pair: Dict[str, Any], account_id: str,
                    db_path: Optional[str]) -> str:
    """``open`` (both legs) / ``flat`` (neither) / ``half_open`` (exactly one).

    THREE states, because the third one is real and was being read as ``flat``.

    ``_close_pair`` is best-effort PER LEG and deliberately leaves a leg that
    failed to flatten open ("the monitor/backstop retries"). The predicate that
    replaced this one asked only *are BOTH legs open?* — so a pair with leg A
    closed and leg B stranded answered **False**, i.e. indistinguishable from a
    pair that was never opened. The tick then:

      * built ``open_state = None``, so the decision saw no position,
      * and was free to emit a fresh ``open`` and place BOTH legs again —

    stacking a second journal row on the stranded leg's symbol while the venue,
    under one-way netting, still holds ONE position. That is the divergence
    shape, and nothing else owns the cleanup: the netting-attribution reconciler
    skips pairs rows by design (``_is_pairs_sleeve_row`` -> ``skipped_pairs``),
    precisely because this executor is supposed to own its own legs
    (``BL-20260808-PAIRS-DIVERGENCE-UNOWNED``).

    A half-open pair is also not merely a bookkeeping wart: a lone leg is a
    NAKED DIRECTIONAL position in a sleeve whose entire premise is market
    neutrality — the same exposure ``_legs_below_min_qty`` refuses to create at
    open time, arrived at from the other end.
    """
    from src.runtime.positions import has_open_trade_for_strategy
    strat_a, strat_b = _leg_strats(pair)
    a = has_open_trade_for_strategy(
        account_id, str(pair["symbol_a"]), strat_a, db_path=db_path)
    b = has_open_trade_for_strategy(
        account_id, str(pair["symbol_b"]), strat_b, db_path=db_path)
    if a and b:
        return "open"
    if a or b:
        return "half_open"
    return "flat"


def _pair_is_open(pair: Dict[str, Any], account_id: str, db_path: Optional[str]) -> bool:
    """True when BOTH legs of the pair currently hold an open trade (the pair is
    on). Uses the journal open-truth (has_open_trade_for_strategy).

    Retained for the concurrency helpers, which genuinely want "is this pair
    ON". Anything DECIDING what to do must use ``_pair_leg_state`` instead —
    this boolean cannot express the half-open case and reports it as False.
    """
    return _pair_leg_state(pair, account_id, db_path) == "open"


_STATE_ALERT_COOLDOWN_S = 3600.0
_state_alert_last: Dict[str, float] = {}


def _half_open_should_report(pair_label: str, *, cleaned: bool) -> bool:
    """Own the half-open reporting cadence in ONE place, for BOTH the alert and
    the soak row, so the alarm and the durable log can never disagree about what
    was reported.

    ⚠️ **THE TWO OUTCOMES ARE RATE-LIMITED DIFFERENTLY, DELIBERATELY.**

    ``cleaned=True`` is an EDGE: the strand existed and this tick resolved it, so
    the condition is gone and reporting is self-limiting. It is never suppressed
    — suppressing it would hide a genuinely NEW strand that happened to land
    inside a cooldown window. It also CLEARS the level cooldown, so the next
    unresolved strand alerts immediately instead of inheriting the suppression
    of the one just fixed.

    ``cleaned=False`` is a LEVEL: the leg is still standing, and since the
    safety check moved above the once-per-bar decision dedup (2026-08-21) this
    branch runs on EVERY tick — roughly 20x per 1h bar. An un-cooled-down
    CRITICAL there is the desensitized alarm CLAUDE.md names as a P1 in its own
    right, and an un-cooled-down soak row would inflate the ``by_event`` counts
    a reviewer reads off ``/api/bot/pairs/soak`` by the same factor. It is
    reported at most once per ``_STATE_ALERT_COOLDOWN_S`` per pair.

    The cooldown is in-process: a restart re-reports once, the fail-safe
    direction. Same reasoning and same store as ``_alert_state_unreadable``.

    This is a SEPARATE function from the alert on purpose. Folding the decision
    into ``_alert_half_open_pair`` and having the caller branch on its return
    would make the durable soak row a casualty of an alerting failure — the log
    matters more than the ping, not less.
    """
    import time as _t
    key = f"{pair_label}|half_open_unresolved"
    if cleaned:
        _state_alert_last.pop(key, None)
        return True
    now = _t.monotonic()
    last = _state_alert_last.get(key)
    if last is not None and (now - last) < _STATE_ALERT_COOLDOWN_S:
        return False
    _state_alert_last[key] = now
    return True


def _alert_half_open_pair(pair_label: str, account_id: str, *,
                          stranded: Sequence[str], cleaned: bool) -> None:
    """Surface a half-open pair loudly. The close-side sibling of
    ``_alert_partial_placement``: a lone leg is un-hedged directional exposure
    in a market-neutral sleeve. WARN when this tick flattened it, CRITICAL when
    it is still standing. Never raises.

    Reporting CADENCE is not decided here — see ``_half_open_should_report``,
    which the caller consults for both this alert and the soak row.
    """
    try:
        from src.runtime.outcomes import Level, report
        if cleaned:
            report("pairs_half_open", "cleaned", level=Level.WARN,
                   reason=(f"pairs {pair_label}: one leg was stranded open on {account_id} "
                           f"({', '.join(stranded)}) after a partial close; flattened this tick"),
                   pair=pair_label, account_id=account_id, stranded_legs=list(stranded))
        else:
            report("pairs_half_open", "unresolved", level=Level.CRITICAL,
                   reason=(f"pairs {pair_label}: one leg is stranded OPEN on {account_id} "
                           f"({', '.join(stranded)}) — un-hedged directional exposure in a "
                           f"market-neutral sleeve, and the cleanup close did not confirm. "
                           f"The pair is BLOCKED from re-opening until it is flat"),
                   pair=pair_label, account_id=account_id, stranded_legs=list(stranded))
    except Exception as exc:  # noqa: BLE001 — an alert must never break the tick
        logger.error("pairs: half-open alert failed for %s: %s", pair_label, exc)


def _alert_state_unreadable(pair_label: str, account_id: str, *,
                            state_read: str) -> None:
    """Surface a pair whose open-state could not be used.

    **Why this exists.** Skipping the tick is the correct action — the sleeve
    must never blind-open or blind-close — but doing it SILENTLY is how
    BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED survived 2,471 decisions: the
    condition was recorded 958 times into a soak log nobody read, while every
    close-side rule the sleeve owns went unevaluated and its legs aged to 595
    bars against a declared 20-bar limit.

    **Why it is rate-limited.** This branch can fire on every tick of a
    long-lived pair. An alert that fires every tick is the desensitized alarm
    CLAUDE.md names as a P1 in its own right, so it is emitted at most once per
    ``_STATE_ALERT_COOLDOWN_S`` per (pair, reason). The cooldown is in-process:
    a restart re-alerts once, which is the fail-safe direction.

    ``state_read`` is carried through, not collapsed — ``error`` (we could not
    look) and ``absent`` (we looked; open legs carry no package) are different
    faults with different fixes.
    """
    try:
        import time as _t
        key = f"{pair_label}|{state_read}"
        now = _t.monotonic()
        last = _state_alert_last.get(key)
        if last is not None and (now - last) < _STATE_ALERT_COOLDOWN_S:
            return
        _state_alert_last[key] = now
        from src.runtime.outcomes import Level, report
        if state_read == "absent":
            reason = (f"pairs {pair_label}: both legs read OPEN on {account_id} but no "
                      f"order package carries the spread bookkeeping. The pair cannot be "
                      f"evaluated for exit_z / stop_z / max_hold_bars and will be skipped "
                      f"every tick until this is resolved")
        else:
            reason = (f"pairs {pair_label}: the open-state read FAILED on {account_id} — "
                      f"the spread bookkeeping could not be read at all. Every close-side "
                      f"rule (max_hold_bars, exit_z, stop_z) is evaluated off this state, "
                      f"so while it persists the pair CANNOT be closed by the executor")
        report("pairs_state_unreadable", state_read, level=Level.CRITICAL,
               reason=reason, pair=pair_label, account_id=account_id,
               state_read=state_read)
    except Exception as exc:  # noqa: BLE001 — an alert must never break the tick
        logger.error("pairs: state-unreadable alert failed for %s: %s", pair_label, exc)


def _held_leg_symbols(pairs: Sequence[Dict[str, Any]], account_id: str,
                      db_path: Optional[str], *, exclude_name: str) -> set:
    """Set of leg-symbols currently held by OTHER open pairs (the disjoint-legs
    concurrency gate's input). Excludes the pair named `exclude_name`."""
    held: set = set()
    for p in pairs:
        if str(p.get("name")) == exclude_name:
            continue
        if _pair_is_open(p, account_id, db_path):
            held.add(str(p["symbol_a"]))
            held.add(str(p["symbol_b"]))
    return held


def _count_correlated_open(pair: Dict[str, Any], pairs: Sequence[Dict[str, Any]],
                           account_id: str, db_path: Optional[str]) -> int:
    """How many OTHER open pairs share a leg symbol with `pair` (the correlation
    haircut's input)."""
    my_syms = {str(pair["symbol_a"]), str(pair["symbol_b"])}
    n = 0
    for p in pairs:
        if str(p.get("name")) == str(pair.get("name")):
            continue
        if not _pair_is_open(p, account_id, db_path):
            continue
        if {str(p["symbol_a"]), str(p["symbol_b"])} & my_syms:
            n += 1
    return n


def _open_pkg_meta(strategy: str, account_id: str,
                   db_path: str) -> tuple:
    """Read the newest ``order_packages.meta`` for a leg strategy (the durable
    spread bookkeeping stamped at open).

    Returns a THREE-STATE ``(status, meta)`` — never a bare ``None``:

      * ``("found", {...})``  — the package is there and parsed.
      * ``("absent", None)``  — we looked and there is no such package. For a
        pair whose legs read OPEN this is a genuine anomaly (a leg trade with
        no order package), NOT a read failure.
      * ``("error", None)``   — **we could not look.** A missing DB file, a bad
        schema, unparseable JSON.

    Collapsing those last two is what hid
    BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED
    for 2,471 decisions: this function used to return ``None`` for
    both, the caller skipped the tick either way, and the *reason* it could
    never read an open pair was a query against **two columns that do not
    exist**. `order_packages` has no `account_id` and no `id` (its PK is the
    TEXT `order_package_id`, which is not a rowid alias), so the SELECT raised
    `OperationalError: no such column: account_id`, the broad `except` swallowed
    it at DEBUG, and every open pair read as "unreadable" forever. 29 pairs were
    opened and **zero** were ever closed by the executor.

    **`account_id` is deliberately NOT a SQL predicate** — the column does not
    exist, and it must not be re-added on the assumption that it does. The
    sleeve is single-account by construction (`config/pairs.yaml::account_id` is
    one top-level scalar) and leg strategy names (`pairs_bnb_btc_a`) are unique
    per pair, so `strategy_name` already scopes this correctly. The parameter is
    kept for the caller's signature and for the log line; a genuinely
    multi-account sleeve must add the column and join deliberately, not by
    re-introducing this predicate.

    Read-only. Never raises.
    """
    try:
        if not os.path.exists(db_path):
            return ("error", None)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT meta FROM order_packages WHERE strategy_name = ? "
                "ORDER BY datetime(created_at) DESC, rowid DESC LIMIT 1",
                (strategy,),
            ).fetchone()
        if not row or not row[0]:
            return ("absent", None)
        meta = json.loads(row[0])
        return ("found", meta) if isinstance(meta, dict) else ("error", None)
    except Exception as exc:  # noqa: BLE001
        # ERROR, not "absent" — and loud. A read failure here silently disables
        # the sleeve's entire close path (max_hold_bars, exit_z, stop_z are all
        # evaluated off this state), so it must never sit at DEBUG again.
        logger.error("pairs: _open_pkg_meta read FAILED for %s (account=%s): %s",
                     strategy, account_id, exc)
        return ("error", None)


def _reconstruct_open_state(pair: Dict[str, Any], account_id: str,
                            db_path: str) -> tuple:
    """Rebuild the pair's ``OpenPair`` (direction / entry_spread / stop_spread /
    bars_held) from the journal-durable ``order_packages.meta`` stamped at open.

    Returns the same THREE-STATE ``(status, open_pair)`` as ``_open_pkg_meta``:
    ``("found", OpenPair)`` · ``("absent", None)`` (legs open, no package —
    an anomaly) · ``("error", None)`` (**we could not look**). The caller must
    branch on the status: on anything but ``found`` it skips the pair this tick
    (the per-leg backstop SL/TP still protects; never blind-opens or
    blind-closes) — but an ``error`` is ALERTED, because a persistent one
    disables the sleeve's whole close path.
    """
    strat_a, _ = _leg_strats(pair)
    status, meta = _open_pkg_meta(strat_a, account_id, db_path)
    if status != "found" or not meta:
        return (status, None)
    try:
        pd = str(meta["pair_direction"])
        entry_spread = float(meta["entry_spread"])
        stop_spread = float(meta["stop_spread"])
        opened_at = str(meta["opened_at_utc"])
        bar_seconds = int(meta.get("bar_seconds") or 3600)
        opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
        held_s = (datetime.now(timezone.utc) - opened_dt).total_seconds()
        bars_held = max(0, int(held_s // max(1, bar_seconds)))
        return ("found", pe.OpenPair(direction=pd, entry_spread=entry_spread,
                                     stop_spread=stop_spread, bars_held=bars_held))
    except Exception as exc:  # noqa: BLE001
        # The package EXISTS but its bookkeeping is malformed/incomplete — we
        # looked and could not use what we found. That is an error, not
        # "absent", and it is logged loudly for the same reason as above.
        logger.error("pairs: open-state reconstruct FAILED for %s: %s",
                     pair.get("name"), exc)
        return ("error", None)


def _fetch_leg(symbol: str, timeframe: str, limit: int,
               settings: Optional[Dict[str, Any]]) -> Optional[tuple]:
    """Fetch a leg via the canonical signal-builder path (BTCUSDT→Bybit, etc.).
    Returns (closes:list[float], last_bar_ts:str) or None on any failure."""
    try:
        from src.runtime.market_data import fetch_candles
        df = fetch_candles(symbol, timeframe, settings=settings, limit=limit)
        if df is None or len(df) == 0 or "close" not in df:
            return None
        closes = [float(x) for x in df["close"].tolist()]
        last_ts = str(df["timestamp"].iloc[-1]) if "timestamp" in df else str(len(closes))
        return closes, last_ts
    except Exception as exc:  # noqa: BLE001
        logger.debug("pairs: candle fetch failed (%s %s): %s", symbol, timeframe, exc)
        return None


_DECISION_BARS_NAME = "pairs_decision_bars.json"


def _decision_bars_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / _DECISION_BARS_NAME


def _load_decision_bars() -> Dict[str, str]:
    try:
        p = _decision_bars_path()
        if not p.exists():
            return {}
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_decision_bars(state: Dict[str, str]) -> None:
    try:
        p = _decision_bars_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _place_pair(client: Any, account_cfg: dict, pair: Dict[str, Any],
                decision: "PairDecision", timeframe: str) -> Dict[str, Any]:
    """Place the two legs on the account, journalled + linked by a shared
    pairs_group_id. Atomic-ish: if leg B fails to place, leg A is immediately
    flattened (the leg-imbalance unwind) so the account never carries a naked
    single leg. Returns {placed:bool, trade_ids:[...], error:str|None}."""
    from src.core.coordinator import OrderPackage, _log_new_order_package
    from src.units.accounts.execute import execute_pkg

    account_id = str(account_cfg.get("account_id") or "unknown")
    strat_a, strat_b = _leg_strats(pair)
    gid = decision.soak.get("pairs_group_id") or f"pair-{uuid.uuid4().hex[:12]}"
    opened_at = datetime.now(timezone.utc).isoformat()
    bar_seconds = _bar_seconds(timeframe)
    # Durable spread bookkeeping — stamped into BOTH legs' order_packages.meta
    # so open-state can be reconstructed after a restart (journal-primary; no
    # sidecar to desync). pair_direction is the SPREAD verdict (long/short_spread).
    common_meta = {
        "pairs_group_id": gid, "pair": decision.pair,
        "pair_direction": decision.soak.get("direction"),
        "entry_spread": decision.soak.get("entry_spread"),
        "stop_spread": decision.soak.get("stop_spread"),
        "opened_at_utc": opened_at, "bar_seconds": bar_seconds,
        "signal_logic": f"pairs {decision.pair} {decision.soak.get('direction')} "
                        f"z={decision.soak.get('z')} beta={decision.soak.get('beta')}",
        "timeframe": timeframe,
    }
    legs = decision.legs
    strat_by_leg = {legs[0].symbol: strat_a, legs[1].symbol: strat_b}
    trade_ids: List[str] = []
    placed_symbols: List[tuple] = []   # (symbol, direction, qty) for unwind
    for i, leg in enumerate(legs):
        pkg = OrderPackage(
            strategy=strat_by_leg[leg.symbol], symbol=leg.symbol,
            direction=leg.direction, entry=leg.entry_ref, sl=leg.sl, tp=leg.tp,
            confidence=float(decision.soak.get("z") or 0.0),
            meta={**common_meta, "leg": ("a" if i == 0 else "b")},
        )
        try:
            _log_new_order_package(pkg)   # persists meta, stamps meta.order_package_id
            # qty_override = the β-hedged pair qty. WITHOUT this, execute_pkg
            # re-sizes the leg from the account risk_pct + the pkg SL distance
            # and gets qty=0 (the live open_failed, BL-20260716-PAIRS-EXEC); the
            # pair hedge REQUIRES the exact per-leg qtys decide_pair computed.
            tid = execute_pkg(pkg, account_cfg, exchange_client=client,
                              qty_override=leg.qty)
            trade_ids.append(tid)
            placed_symbols.append((leg.symbol, leg.direction, leg.qty))
        except Exception as exc:  # noqa: BLE001
            logger.error("pairs: leg %s placement failed for %s: %s",
                         leg.symbol, decision.pair, exc)
            # LEG-IMBALANCE UNWIND: flatten anything already placed so we never
            # leave a naked single leg on the account. The unwind now REPORTS which
            # legs failed to flatten (best-effort close returns ok:False, not raise)
            # so a genuinely-naked leg is escalated loudly, not silently swallowed.
            naked = _unwind_legs(client, account_cfg, placed_symbols)
            _alert_partial_placement(decision.pair, account_id, placed_symbols,
                                     failed_leg=leg.symbol, err=str(exc), naked=naked)
            return {"placed": False, "trade_ids": trade_ids,
                    "error": f"leg {leg.symbol}: {exc}", "naked_legs": naked}
    logger.info("pairs: opened %s (%s) group=%s account=%s trade_ids=%s",
                decision.pair, decision.soak.get("direction"), gid, account_id, trade_ids)
    return {"placed": True, "trade_ids": trade_ids, "error": None}


def _unwind_legs(client: Any, account_cfg: dict, placed: Sequence[tuple]) -> List[Dict[str, Any]]:
    """Flatten already-placed legs after a partial-placement failure and RETURN the
    legs that did NOT confirm flat (still naked).

    ``close_open_position`` is best-effort: it returns ``{"ok": False, "error": …}``
    on failure rather than raising (BL-20260716-PAIRS-MINQTY — the earlier version
    only caught exceptions, so an ``ok:False`` close was silently logged as
    "unwound" while the leg stayed open — the naked BNB leg incident). We now check
    the result and surface every leg that isn't confirmed flat so the caller can
    alert loudly. The exchange-side backstop SL/TP remains the last-resort net."""
    from src.units.accounts.execute import close_open_position
    naked: List[Dict[str, Any]] = []
    for symbol, direction, qty in placed:
        entry = {"symbol": symbol, "direction": direction, "qty": float(qty)}
        try:
            res = close_open_position(client, account_cfg, symbol=symbol,
                                      side=direction, qty=float(qty))
        except Exception as exc:  # noqa: BLE001
            logger.error("pairs: leg-imbalance unwind RAISED for %s: %s (backstop SL/TP remains)",
                         symbol, exc)
            naked.append({**entry, "error": str(exc)})
            continue
        if isinstance(res, dict) and res.get("ok"):
            logger.warning("pairs: unwound leg %s (%s qty=%s) after partial-placement failure",
                           symbol, direction, qty)
        else:
            err = res.get("error") if isinstance(res, dict) else "no result"
            logger.error("pairs: leg-imbalance unwind did NOT confirm flat for %s (%s qty=%s): %s "
                         "(backstop SL/TP remains — NAKED LEG)", symbol, direction, qty, err)
            naked.append({**entry, "error": err})
    return naked


def _alert_partial_placement(pair: str, account_id: str, placed: Sequence[tuple], *,
                             failed_leg: str, err: str, naked: List[Dict[str, Any]]) -> None:
    """Surface a half-placement loudly. A CLEAN unwind is a WARNING (a rare
    transient the system self-corrected); a leg left NAKED after the unwind is a
    CRITICAL operator alert (real directional exposure, protected only by the
    exchange bracket, needs a manual flatten). Never raises."""
    try:
        from src.runtime.outcomes import Level, report
        if naked:
            report("pairs_naked_leg", "unresolved", level=Level.CRITICAL,
                   reason=(f"pairs {pair}: leg {failed_leg} failed AND the unwind left "
                           f"{len(naked)} naked leg(s) on {account_id} — un-hedged directional "
                           f"exposure protected only by the exchange bracket; needs a manual flatten"),
                   pair=pair, account_id=account_id, failed_leg=failed_leg,
                   naked_legs=naked, place_error=err[:300])
        else:
            report("pairs_partial_placement", "unwound", level=Level.WARN,
                   reason=(f"pairs {pair}: leg {failed_leg} failed after {len(placed)} leg(s) "
                           f"placed on {account_id}; the placed leg(s) were unwound cleanly"),
                   pair=pair, account_id=account_id, failed_leg=failed_leg, place_error=err[:300])
    except Exception as exc:  # noqa: BLE001 — an alert must never break the tick
        logger.error("pairs: partial-placement alert failed for %s: %s", pair, exc)


# collapsed-state: unknown — FALSE POSITIVE against `position_telemetry.finality_source`,
# not a suppression of a real finding. This module has ZERO references to
# `position_telemetry`, `finality_source`, `stamped`, `derived_join` or `not_final`
# (verified by grep, and re-checkable the same way). The only `"unknown"` literals here
# are the account-id fallback `str(account_cfg.get("account_id") or "unknown")` at the
# two placement/close sites — a label for an unnamed account, unrelated to whether a
# telemetry row is terminal. The finding PRE-DATES the cascade fix below: it reproduces
# on the unmodified file (`git stash` + re-run), and surfaced only because touching this
# file pulled it into the guard's diff scope.

def _cascade_close_pair_package(db: Any, trade_id: Any, close_reason: str) -> bool:
    """Close the ``order_packages`` row linked to a pairs leg we just closed.

    **Why this exists.** ``_close_pair`` writes ``status='closed'`` straight to the
    trade row, so it never routes through ``order_monitor._close_trade_from_order_status``
    — the *only* place the package cascade runs. Every pairs leg closed here therefore
    left its package row open until the second-line-of-defence sweep
    (``_sweep_stuck_linked_packages``) found it and stamped the generic
    ``close_reason='stuck_cascade_recovered'``, which is a **bookkeeping repair, not an
    exit** (``BL-20260822-PACKAGE-CLOSE-REASON-IS-NOT-THE-EXIT-RECORD``).

    Measured on the live journal 2026-08-22, newest 500 closed rows
    (2026-07-15 → 2026-08-22), and the two arms reconcile **exactly**:

    ==========================================================  ====
    pairs legs closed via the monitor (cascades today)            57
    packages carrying a real reason (``reconciler_filled``)       57
    pairs legs closed via ``_close_pair`` / ``intent_reduce``    120
    packages left for the sweep (109 swept + 11 not yet)         120
    ==========================================================  ====

    ``pairs_revert`` / ``pairs_stop`` / ``pairs_half_open_cleanup`` appear on **99
    trade rows and ZERO package rows** — the exit reason existed and reached no
    package. That is the defect this closes: the package now carries the reason the
    trade actually exited for.

    ⚠️ **This is bookkeeping ONLY — it places, modifies and cancels nothing.** It is
    the same write the sweep already performs, made timely and honestly-reasoned. The
    one route by which package state feeds back into ordering is the strategy-monocle
    gate, and that gate **cannot see a pairs leg**: it is consulted only at
    ``pipeline.py``'s signal-dispatch site, and pairs legs are not in the strategy
    roster — the isolated ``run_pairs_tick`` hook owns them
    (``BL-20260822-PAIRS-PACKAGES-CLOSED-BY-THE-STUCK-CASCADE-SWEEP``).

    ⚠️ **Failure is swallowed HERE, deliberately, and never reaches the caller.** The
    surrounding ``except`` in ``_close_pair`` sets ``closed_ok = False``, which the
    tick reads as *the leg did not flatten*. A package-bookkeeping failure must never
    produce that verdict — the broker close already succeeded, and reporting it as a
    failed flatten would strand a flat leg as "still open". Same guard shape as
    ``_record_trade_cost_estimate`` and the ``terminal_state`` stamp. The sweep
    remains the backstop for anything missed here, exactly as before.

    Returns True when a package row was updated; False on any miss or failure.
    """
    try:
        from src.runtime.order_monitor import _cascade_close_linked_package
        return bool(_cascade_close_linked_package(
            db, trade_id,
            close_reason=close_reason,
            caller="pairs_executor._close_pair",
        ))
    except Exception as exc:  # noqa: BLE001 — bookkeeping only; never fail the close
        logger.warning(
            "pairs: package cascade failed for trade_id=%s (%s) — leg IS closed; "
            "the stuck-cascade sweep remains the backstop: %s",
            trade_id, close_reason, exc,
        )
        return False


def _close_pair(client: Any, account_cfg: dict, pair: Dict[str, Any],
                outcome: str, close_a: float, close_b: float) -> Dict[str, Any]:
    """Flatten BOTH legs of an open pair and mark their trade rows closed with a
    local-compute PnL. Returns {closed:bool, ...}. Best-effort per leg — a leg
    that fails to flatten leaves its row open (the monitor/backstop retries)."""
    from src.units.accounts.execute import close_open_position
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    account_id = str(account_cfg.get("account_id") or "unknown")
    strat_a, strat_b = _leg_strats(pair)
    db = Database(db_path=trade_journal_db_path())
    now_iso = datetime.now(timezone.utc).isoformat()
    closed_ok = True
    for strat, symbol, last_px in ((strat_a, str(pair["symbol_a"]), close_a),
                                   (strat_b, str(pair["symbol_b"]), close_b)):
        try:
            rows = db.get_trades(filters={"status": "open", "strategy_name": strat,
                                          "account_id": account_id}, limit=1)
            if not rows:
                continue
            row = rows[0]
            direction = str(row.get("direction") or "").lower()
            qty = float(row.get("position_size") or 0.0)
            entry = float(row.get("entry_price") or 0.0)
            # BL-20260721-BYBIT2-XRP-TPSL-LEGCAP: pass this leg's OWN tracked
            # Bybit Partial-tpsl order ids so `close_open_position` cancels them
            # after a confirmed close. Without them the venue keeps the legs of a
            # closed trade resting forever, and on a netted one-way book they
            # accumulate against the SURVIVING position.
            #
            # MEASURED 2026-08-25 on bybit_1/ETHUSDT: 12 legs still resting for
            # SIX closed pairs rows (5003, 4998, 4974, 4937, 4932, 4909 — all
            # pairs_revert / pairs_stop / pairs_half_open_cleanup), against a
            # 5.59 position carrying 9.33 of stop = 167%. Zero of the resting
            # legs were orphans: every one mapped to a journal row, so this is
            # the whole cause and not a contributing one.
            #
            # The row is ALREADY in scope here — `direction`, `position_size` and
            # `entry_price` are read off it three lines up — so this was never a
            # missing-context problem. `close_open_position` treats a cancel
            # failure as logged-not-fatal (the position IS flat either way), so
            # passing them cannot turn a good close into a failed one.
            res = close_open_position(client, account_cfg, symbol=symbol,
                                      side=direction, qty=qty,
                                      sl_order_id=row.get("sl_order_id"),
                                      tp_order_id=row.get("tp_order_id"))
            if not res.get("ok"):
                logger.warning("pairs: leg close not confirmed %s (%s): %s — row left open",
                               symbol, strat, res.get("error"))
                closed_ok = False
                continue
            # local-compute realised PnL (paper venue; broker-truth sweep may
            # refine bybit later). long: (exit-entry)*qty ; short: (entry-exit)*qty
            sign = 1.0 if direction == "long" else -1.0
            pnl = round(sign * (float(last_px) - entry) * qty, 6) if entry > 0 else None
            pnl_pct = (round(sign * (float(last_px) - entry) / entry * 100.0, 4)
                       if entry > 0 else None)
            # ── M39(B): stamp provenance on this DECIDED close ──────────
            # Backlog row on ONE line so the id resolves (a wrapped id reads as
            # a dangling reference and `check_backlog_refs` refuses it):
            # BL-20260824-THE-DECIDED-EXIT-PATH-IS-THE-UNMEASURED-ONE
            #
            # This write persisted status/exit_price/exit_reason/closed_at/pnl
            # and NO provenance key, so every pairs close classified UNVERIFIED
            # -- "we don't know" -- while carrying a real price and a real pnl.
            # Measured on the live journal 2026-08-25 over the newest 500
            # trades: of 107 DECIDED closes, 82 were unverified and **all 82
            # were pairs**, i.e. after the M39(A) monitor fix this site was
            # 100% of the remaining decided-provenance gap. Every non-pairs
            # decided path measured 0% unverified.
            #
            # THE STAMP GOES AT THIS ONE SITE, NOT PER EXIT REASON. Track B was
            # scoped as "pairs_revert / pairs_stop", but the live journal also
            # carries `pairs_timeout` (n=2) -- a fix written against the two
            # named reasons would have left a third silently unstamped, which
            # is the residual that survives a fix and reads as fixed. Stamping
            # where the row is written covers every present and future
            # `pairs_*` outcome by construction.
            #
            # WHY `candle_at_close` AND NOT A FILL. `last_px` is `closes_a[-1]`
            # / `closes_b[-1]` -- the close of the bar the exit decision was
            # made on. `close_open_position` is called for the flatten but only
            # its `ok` flag is read; NO fill price is ever read back. So this
            # is a bar close, not broker truth, and `candle_at_close` is
            # already the ESTIMATED source for exactly that (`exit_anchor`).
            # Stamping a MEASURED source here would be the provenance lie that
            # demoted `recorded_exit_price` on 2026-08-24 -- verified, not
            # assumed: `classify_pnl` returns ESTIMATED for this pair of keys
            # and would return MEASURED for `exchange_fill`.
            #
            # THREE-STATE, per `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
            # states": a close whose price is not a usable number declares
            # `UNMEASURED_MARKER` rather than claiming a bar close it never
            # had. `pnl_source` is written ONLY when a pnl exists -- claiming
            # `local_compute` over a NULL pnl would describe arithmetic that
            # never ran.
            #
            # NEVER OVERWRITES a more specific existing stamp -- the sibling
            # rule `_apply_update` and `_sweep_local_pnl_for_unpriced` both
            # carry, after an unconditional overwrite laundered a projection
            # over a broker source.
            close_updates: Dict[str, Any] = {
                "status": "closed", "exit_price": float(last_px),
                "exit_reason": f"pairs_{outcome}", "closed_at": now_iso,
                "pnl": pnl, "pnl_percent": pnl_pct,
            }
            _notes = _decode_notes(row.get("notes"))
            _px_ok = math.isfinite(float(last_px)) if last_px is not None else False
            if not _notes.get("exit_price_source"):
                _notes["exit_price_source"] = (
                    "candle_at_close" if _px_ok else _UNMEASURED_MARKER)
            if pnl is not None and not _notes.get("pnl_source"):
                _notes["pnl_source"] = "local_compute"
            close_updates["notes"] = dump_capped(_notes, 2000)
            db.update_trade(row["id"], close_updates)
            # Package bookkeeping — guarded HERE as well as inside the helper, so
            # the isolation property is STRUCTURAL rather than conventional: it must
            # not depend on a future edit keeping the helper's own guard intact. A
            # raise reaching the enclosing `except` would set `closed_ok = False`,
            # reporting a leg that IS flat at the broker as a failed flatten.
            try:
                _cascade_close_pair_package(db, row["id"], f"pairs_{outcome}")
            except Exception:  # noqa: BLE001 — bookkeeping never fails a close
                logger.warning("pairs: package cascade raised for trade_id=%s — "
                               "leg IS closed; sweep remains the backstop", row["id"])
        except Exception as exc:  # noqa: BLE001
            logger.error("pairs: leg close failed %s (%s): %s", symbol, strat, exc)
            closed_ok = False
    logger.info("pairs: closed %s (%s) ok=%s", _pair_label(str(pair["symbol_a"]),
                str(pair["symbol_b"])), outcome, closed_ok)
    return {"closed": closed_ok, "outcome": outcome}


def _legs_below_min_qty(client: Any, account_cfg: dict,
                        legs: Sequence[LegOrder]) -> List[Dict[str, Any]]:
    """Return the legs whose sized qty floors BELOW the venue's minimum lot — the
    pre-placement half-placement guard (BL-20260716-PAIRS-MINQTY).

    A market-neutral pair must place BOTH legs or NEITHER: if one leg can't clear
    the exchange minimum, placing the other leaves a naked directional orphan (the
    low-beta BTC-quote failure — the tiny BTC leg floored to 0.00037 < 0.001 min,
    was refused, and the BNB leg was left open). Each leg is checked through the
    SAME seam the submit pre-flight uses (``qty_legalize.legalize_qty``,
    ``prefer_live=True``) so the verdict matches what the exchange would do.
    Fail-open: an unknown lot / resolution error passes the leg (never blocks a
    placeable pair on a lookup miss — the submit pre-flight stays the backstop).
    Returns ``[]`` when both legs clear."""
    blocked: List[Dict[str, Any]] = []
    try:
        from src.units.accounts.qty_legalize import legalize_qty
    except Exception:  # noqa: BLE001
        return blocked
    for leg in legs:
        try:
            lz = legalize_qty(float(leg.qty), account_cfg=account_cfg,
                              symbol=leg.symbol, client=client, prefer_live=True)
        except Exception:  # noqa: BLE001 — never block the tick on a lookup
            continue
        if not lz.ok:
            blocked.append({"symbol": leg.symbol, "qty": round(float(leg.qty), 8),
                            "venue_min": lz.venue_min})
    return blocked


def run_pairs_tick(settings: Optional[Dict[str, Any]] = None) -> None:
    """Once-per-tick hook for the market-neutral pairs sleeve. Best-effort — any
    error is logged and swallowed so the sleeve can never stall the trader loop.

    For each configured pair: fetch both legs' candles → reconstruct open-state
    from the journal → decide_pair → place / close / hold → write the soak row.
    A pair with `execution: shadow` computes the would-be decision and logs the
    soak but places NOTHING (the sanctioned observe-only gate)."""
    try:
        from src.runtime.pairs_soak import build_pairs_soak_record, record_pairs_soak
    except Exception:  # noqa: BLE001
        return
    cfg = _load_pairs_config()
    pairs = cfg.get("pairs") or []
    if not pairs:
        return

    default_account = str(cfg.get("account_id") or "bybit_1")
    # Per-pair risk budget is DERIVED from the account's canonical risk basis
    # (live balance × risk_pct), NOT a hardcoded dollar literal — the same basis
    # RiskManager.position_size uses for every other strategy (CLAUDE.md:
    # "sizing is the per-account RiskManager's job; account basis × …").
    # `pairs_risk_fraction` optionally scales the sleeve below the flat account
    # basis (default 1.0 = the full per-trade risk basis).
    pairs_risk_fraction = float(cfg.get("pairs_risk_fraction", 1.0))
    corr_factor = float(cfg.get("correlation_haircut_factor", 0.5))
    backstop_mult = float(cfg.get("backstop_mult", 3.0))
    min_leg_notional_usd = float(cfg.get("min_leg_notional_usd", 10.0))

    try:
        from src.config.accounts_loader import load_accounts_dict
        from src.utils.paths import trade_journal_db_path
        accounts = load_accounts_dict()
        db_path = trade_journal_db_path()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairs: account/db resolve failed: %s", exc)
        return

    # One decision per CLOSED bar per pair (backtest fidelity): the trader ticks
    # ~every 15 min but the pairs are 1h, so the same closed bar is seen ~4×.
    # Dedup on the latest bar timestamp so we decide/act exactly once per bar,
    # mirroring the backtest's one-pass-per-bar loop.
    decision_bars = _load_decision_bars()
    decision_bars_dirty = False

    # Build one live client per referenced account (lazy; only when a live pair
    # needs it). Shadow-only configs never touch an exchange socket.
    clients: Dict[str, Any] = {}

    def _client_for(account_id: str) -> Any:
        if account_id in clients:
            return clients[account_id]
        acct = dict(accounts.get(account_id) or {})
        acct.setdefault("account_id", account_id)
        try:
            from src.units.accounts.clients import bybit_client_for
            clients[account_id] = bybit_client_for(acct) if str(
                acct.get("exchange") or "").lower() == "bybit" else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("pairs: client build failed for %s: %s", account_id, exc)
            clients[account_id] = None
        return clients[account_id]

    # Per-account risk budget = live balance × risk_pct × pairs_risk_fraction,
    # cached per tick. The canonical basis (execute._fetch_balance is the same
    # balance read RiskManager uses; risk_pct comes from the account's `risk`
    # block). Requires a read client even in shadow so the would-be budget is
    # faithful (a read, never an order). None when the basis is unavailable →
    # the pair skips (never sizes off a guessed/hardcoded number).
    budgets: Dict[str, Optional[float]] = {}

    def _budget_for(account_id: str, acct_cfg: dict) -> Optional[float]:
        if account_id in budgets:
            return budgets[account_id]
        val: Optional[float] = None
        try:
            client = _client_for(account_id)
            if client is not None:
                from src.units.accounts.execute import _fetch_balance
                balance = float(_fetch_balance(client, acct_cfg))
                risk_pct = float((acct_cfg.get("risk") or {}).get("risk_pct", 0.01))
                if balance > 0 and risk_pct > 0:
                    val = balance * risk_pct * pairs_risk_fraction
        except Exception as exc:  # noqa: BLE001
            logger.warning("pairs: risk-budget derive failed for %s: %s", account_id, exc)
        budgets[account_id] = val
        return val

    for pair in pairs:
        try:
            name = str(pair.get("name") or "")
            account_id = str(pair.get("account_id") or default_account)
            acct_cfg = dict(accounts.get(account_id) or {})
            acct_cfg.setdefault("account_id", account_id)
            timeframe = str(pair.get("timeframe", "1h"))
            execution = str(pair.get("execution", "shadow")).strip().lower()
            params = _params_from_cfg(pair)
            limit = max(60, params.lookback + 40)
            leg_a = _fetch_leg(str(pair["symbol_a"]), timeframe, limit, settings)
            leg_b = _fetch_leg(str(pair["symbol_b"]), timeframe, limit, settings)
            if leg_a is None or leg_b is None:
                continue
            closes_a, ts_a = leg_a
            closes_b, ts_b = leg_b
            n = min(len(closes_a), len(closes_b))
            closes_a, closes_b = closes_a[-n:], closes_b[-n:]

            bar_key = f"{ts_a}|{ts_b}"

            # ── SAFETY CHECK — EVERY TICK, ABOVE THE ONCE-PER-BAR DEDUP ─────
            # ⚠️ THE ORDER OF THESE TWO BLOCKS IS LOAD-BEARING. Do not move the
            # leg-state read back below the `decision_bars` dedup.
            #
            # The dedup exists for BACKTEST FIDELITY: the trader ticks every
            # ~3 min while the pairs are 1h, so the same closed bar is seen ~20x
            # and the DECISION must be taken once, mirroring the harness's
            # one-pass-per-bar loop. That reasoning applies to deciding. It does
            # not apply to noticing that a leg is standing naked.
            #
            # It used to sit above this check, so a strand was not even LOOKED
            # AT until the next bar. Measured 2026-08-21 on the live journal,
            # the gap between the reconciler closing one leg and the cleanup
            # flattening the other was 62, 63, 64 and 6 minutes across the
            # recorded instances — i.e. a naked directional position in a
            # market-neutral sleeve stood for up to a full hour, while this
            # module's own alert graded that condition CRITICAL
            # (BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN).
            #
            # A safety check must not inherit a decision cadence. The dedup now
            # gates only the decision half, below.
            leg_state = _pair_leg_state(pair, account_id, db_path)
            if leg_state == "half_open":
                # NOT flat. Exactly one leg is open — a naked directional
                # position in a market-neutral sleeve, and re-opening here would
                # stack a second journal row on the stranded leg's symbol over a
                # single netted exchange position (the divergence shape,
                # BL-20260808-PAIRS-DIVERGENCE-UNOWNED). Flatten it, say so, and
                # place NOTHING this bar.
                #
                # Consuming the bar here preserves the pre-existing "place
                # nothing this bar" semantic: the cleanup may resolve the strand
                # mid-bar, and without this the pair would become eligible to
                # open on the very bar it was stranded on.
                decision_bars[name] = bar_key
                decision_bars_dirty = True
                strat_a, strat_b = _leg_strats(pair)
                from src.runtime.positions import has_open_trade_for_strategy
                stranded = [
                    sym for sym, strat in (
                        (str(pair["symbol_a"]), strat_a),
                        (str(pair["symbol_b"]), strat_b),
                    )
                    if has_open_trade_for_strategy(
                        account_id, sym, strat, db_path=db_path)
                ]
                cleaned = False
                if execution == "live":
                    # _close_pair skips a leg with no open row, so on a
                    # half-open pair it closes exactly the stranded one.
                    cleaned = bool(_close_pair(
                        _client_for(account_id), acct_cfg, pair,
                        "half_open_cleanup", closes_a[-1], closes_b[-1],
                    ).get("closed"))
                _label = _pair_label(str(pair["symbol_a"]), str(pair["symbol_b"]))
                # ONE cadence decision, used for the alert AND the soak row, so
                # the alarm and the durable log always agree about what was
                # reported. The cleanup above is NOT gated by it — a safety
                # action retries every tick; only the reporting is cooled down.
                if _half_open_should_report(_label, cleaned=cleaned):
                    _alert_half_open_pair(_label, account_id,
                                          stranded=stranded, cleaned=cleaned)
                    rec = build_pairs_soak_record(
                        event="half_open", pair=_pair_label(
                            str(pair["symbol_a"]), str(pair["symbol_b"])),
                        symbol_a=str(pair["symbol_a"]), symbol_b=str(pair["symbol_b"]),
                        account_id=account_id, execution_mode=execution,
                        stranded_legs=stranded, cleanup_confirmed=cleaned)
                    record_pairs_soak(rec)
                continue

            # ── DECISION HALF — once per closed bar, as before ──────────────
            if decision_bars.get(name) == bar_key:
                continue
            decision_bars[name] = bar_key
            decision_bars_dirty = True

            is_open = leg_state == "open"
            open_state = None
            state_read = "found"
            if is_open:
                state_read, open_state = _reconstruct_open_state(
                    pair, account_id, db_path)
            if is_open and open_state is None:
                # Legs are open but the durable bookkeeping is unusable — do
                # NOT blind-open or blind-close; the per-leg backstop protects.
                #
                # `state_read` distinguishes the two reasons, which the soak
                # previously collapsed into one event: "error" = we could not
                # look; "absent" = we looked and there is no package for open
                # legs (itself an anomaly). Skipping is right for both; staying
                # QUIET about it is not. This branch ran 958 times (38.8% of
                # every decision ever logged) while the sleeve opened 29 pairs
                # and closed ZERO, because every close-side rule
                # (max_hold_bars, exit_z, stop_z) is evaluated off this state.
                # BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED.
                _alert_state_unreadable(
                    _pair_label(str(pair["symbol_a"]), str(pair["symbol_b"])),
                    account_id, state_read=state_read)
                rec = build_pairs_soak_record(
                    event="skip_state_unreadable", pair=_pair_label(
                        str(pair["symbol_a"]), str(pair["symbol_b"])),
                    symbol_a=str(pair["symbol_a"]), symbol_b=str(pair["symbol_b"]),
                    account_id=account_id, execution_mode=execution,
                    state_read=state_read)
                record_pairs_soak(rec)
                continue

            held = _held_leg_symbols(pairs, account_id, db_path, exclude_name=name)
            corr_open = _count_correlated_open(pair, pairs, account_id, db_path)

            # Derive the risk budget from the account's canonical basis. If it's
            # unavailable (no client / balance read failed), skip — never size
            # off a fallback constant.
            risk_budget = _budget_for(account_id, acct_cfg)
            if risk_budget is None or risk_budget <= 0:
                rec = build_pairs_soak_record(
                    event="skip_no_risk_basis", pair=_pair_label(
                        str(pair["symbol_a"]), str(pair["symbol_b"])),
                    symbol_a=str(pair["symbol_a"]), symbol_b=str(pair["symbol_b"]),
                    account_id=account_id, execution_mode=execution)
                record_pairs_soak(rec)
                continue

            decision = decide_pair(
                params, closes_a, closes_b, open_state=open_state, held_symbols=held,
                risk_budget_usd=risk_budget, correlation_open=corr_open,
                execution_mode=execution, corr_factor=corr_factor,
                backstop_mult=backstop_mult, min_leg_notional_usd=min_leg_notional_usd)

            # PRE-PLACEMENT min-qty gate (BL-20260716-PAIRS-MINQTY): a
            # market-neutral pair must place BOTH legs or NEITHER. If a sized leg
            # floors below the venue minimum lot, refuse the WHOLE pair here
            # (skip_size, place nothing) rather than half-place leg A and orphan a
            # naked directional leg. Applies to any computed-legs decision
            # (open / shadow_open); mirrors the submit pre-flight seam so live and
            # shadow agree on feasibility.
            if decision.legs:
                min_qty_blocked = _legs_below_min_qty(
                    _client_for(account_id), acct_cfg, decision.legs)
                if min_qty_blocked:
                    decision.event = "skip_size"
                    decision.legs = []
                    decision.close = False
                    decision.soak["min_qty_block"] = min_qty_blocked

            # --- act on the decision (only `live` execution places/closes) ---
            place_result: Dict[str, Any] = {}
            if decision.event == "open" and execution == "live":
                client = _client_for(account_id)
                place_result = _place_pair(client, acct_cfg, pair, decision, timeframe)
                if not place_result.get("placed"):
                    decision.event = "open_failed"
            elif decision.close and execution == "live":
                client = _client_for(account_id)
                _close_pair(client, acct_cfg, pair, decision.soak.get("outcome") or "exit",
                            closes_a[-1], closes_b[-1])

            rec = build_pairs_soak_record(
                event=decision.event,
                pair=decision.pair,
                symbol_a=str(pair["symbol_a"]), symbol_b=str(pair["symbol_b"]),
                account_id=account_id,
                **{k: v for k, v in decision.soak.items()
                   if k not in ("symbol_a", "symbol_b")},
                trade_ids=place_result.get("trade_ids") if place_result else None,
                place_error=place_result.get("error") if place_result else None,
            )
            record_pairs_soak(rec)
        except Exception as exc:  # noqa: BLE001 — one pair's failure never stops the rest
            logger.exception("pairs: tick failed for %s: %s", pair.get("name"), exc)

    if decision_bars_dirty:
        _save_decision_bars(decision_bars)
