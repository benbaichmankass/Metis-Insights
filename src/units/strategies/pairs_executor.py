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
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from src.units.strategies import pairs_engine as pe
from src.units.strategies import pairs_sizing as psz

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


def _pair_is_open(pair: Dict[str, Any], account_id: str, db_path: Optional[str]) -> bool:
    """True when BOTH legs of the pair currently hold an open trade (the pair is
    on). Uses the journal open-truth (has_open_trade_for_strategy)."""
    from src.runtime.positions import has_open_trade_for_strategy
    strat_a, strat_b = _leg_strats(pair)
    return (has_open_trade_for_strategy(account_id, str(pair["symbol_a"]), strat_a, db_path=db_path)
            and has_open_trade_for_strategy(account_id, str(pair["symbol_b"]), strat_b, db_path=db_path))


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


def _open_pkg_meta(strategy: str, account_id: str, db_path: str) -> Optional[Dict[str, Any]]:
    """Read the newest order_packages.meta for a leg strategy (the durable spread
    bookkeeping stamped at open). Read-only; None on any failure."""
    try:
        if not os.path.exists(db_path):
            return None
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT meta FROM order_packages WHERE strategy_name = ? "
                "AND account_id = ? ORDER BY id DESC LIMIT 1",
                (strategy, account_id),
            ).fetchone()
        if not row or not row[0]:
            return None
        meta = json.loads(row[0])
        return meta if isinstance(meta, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("pairs: _open_pkg_meta read failed (%s): %s", strategy, exc)
        return None


def _reconstruct_open_state(pair: Dict[str, Any], account_id: str,
                            db_path: str) -> Optional[pe.OpenPair]:
    """Rebuild the pair's OpenPair (direction / entry_spread / stop_spread /
    bars_held) from the journal-durable order_packages.meta stamped at open.
    Returns None when the bookkeeping can't be read (caller then skips the pair
    this tick — the per-leg backstop SL/TP still protects; never blind-closes)."""
    strat_a, _ = _leg_strats(pair)
    meta = _open_pkg_meta(strat_a, account_id, db_path)
    if not meta:
        return None
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
        return pe.OpenPair(direction=pd, entry_spread=entry_spread,
                           stop_spread=stop_spread, bars_held=bars_held)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pairs: open-state reconstruct failed (%s): %s",
                     pair.get("name"), exc)
        return None


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
            res = close_open_position(client, account_cfg, symbol=symbol,
                                      side=direction, qty=qty)
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
            db.update_trade(row["id"], {
                "status": "closed", "exit_price": float(last_px),
                "exit_reason": f"pairs_{outcome}", "closed_at": now_iso,
                "pnl": pnl, "pnl_percent": pnl_pct,
            })
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

            # One decision per closed bar per pair (dedup on both legs' latest ts).
            bar_key = f"{ts_a}|{ts_b}"
            if decision_bars.get(name) == bar_key:
                continue
            decision_bars[name] = bar_key
            decision_bars_dirty = True

            is_open = _pair_is_open(pair, account_id, db_path)
            open_state = _reconstruct_open_state(pair, account_id, db_path) if is_open else None
            if is_open and open_state is None:
                # Legs are open but the durable bookkeeping is unreadable — do
                # NOT blind-open or blind-close; the per-leg backstop protects.
                rec = build_pairs_soak_record(
                    event="skip_state_unreadable", pair=_pair_label(
                        str(pair["symbol_a"]), str(pair["symbol_b"])),
                    symbol_a=str(pair["symbol_a"]), symbol_b=str(pair["symbol_b"]),
                    account_id=account_id, execution_mode=execution)
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
