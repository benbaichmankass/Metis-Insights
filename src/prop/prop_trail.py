"""Prop trail step: the leg's declared trailing stop, applied by amending the
resting SL on the venue (PROP-TRAIL, operator decision 2026-10-01 "Build the
trail instead").

Why this exists. A prop ticket is a static SL+TP bracket; the Stage-0 evidence
the prop legs were admitted on (``scripts/backtest_trend.py``) manages every
trade with a chandelier trail (and, where declared, trail-decay and a
stale-exit). Without the trail the live book trades a different exit than the
one that was measured.

What one step does (:func:`run_trail_step`), after the executor cycle so it
never delays a ticket:

1. For every terminal position that maps to an open journal row on an
   executor-enabled venue symbol, find its ticket (entry, SL, signal time,
   strategy) and the leg's declared levers in ``config/strategies.yaml``.
2. Replay the harness's own trail over the CLOSED bars since the signal bar
   (:func:`plan_trail`): ``ext`` = since-entry extreme, ``trail = max(trail,
   ext - effective_trail_mult(...) x atr)`` with ``atr = |entry - sl| /
   atr_stop_mult`` (exactly the frozen entry ATR the strategy priced its stop
   from) and the ONE trail-lever rule ``src.research.trail_levers``.
3. If the replayed stop TIGHTENS the resting SL by at least one price step and
   still sits on the safe side of the current price, amend the SL through the
   adapter's ``modify_bracket`` (TP is not touched), then re-read and confirm.
   A stop is never loosened, never removed, and no close control is clicked.

ROLLOUT (manager 2026-10-02 23:30Z, DIALOG-MEASURE #15693/#15705). Every
armed step goes through ``modify_bracket(..., rollout=ModifyRollout(...))`` on
the account's ONE modify-rollout latch (``<state dir>/modify_rollout.json``,
the same file the executor's containment uses). The guard admits only a
single SL-only tighten of at most ``ROLLOUT_MAX_TIGHTEN_FRACTION`` of the
stop's distance from the venue price, so a replayed target further than that
is STEPPED toward (never past it), and after any armed step -- verified or
not -- the latch stays set and the trail PAUSES for every ticket until a
person reviews it and runs ``executor-clear-rollout``. One watched step per
clear is the first-week rollout; the trail never clears the latch itself.

Levers this step cannot apply by an SL amend are never approximated:

* a CLOSE-type lever (``stale_exit_bars``, ``giveback_*``) exits at a bar
  close in the harness. A resting stop cannot reproduce "exit now at this
  price" — it only fires when price later trades through it — so the step
  raises one alert per ticket when the lever WOULD fire and does nothing else.
* an SL-shaping lever it does not model (``trail_vol_*``, ``be_floor_r``)
  would make the replayed stop differ from the harness's, so the leg is
  skipped with a logged reason rather than trailed with the wrong stop.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import pandas as pd

from src.prop.platform.base import Position
from src.prop.platform.dxtrade import ROLLOUT_MAX_TIGHTEN_FRACTION, ModifyRollout
from src.prop.prop_executor import CycleResult, ExecutorConfig, _dir, _f, _parse_ts, _report
from src.research.trail_levers import effective_trail_mult

_REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_PATH = _REPO_ROOT / "config" / "strategies.yaml"
STATE_FILE = "trail_state.json"

_TF_MINUTES = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
# Close-type levers: alert when they would fire, never act (module docstring).
_CLOSE_LEVERS = ("stale_exit_bars", "giveback_min_mfe_r")
# SL-shaping levers the replay does not model: the leg is skipped.
_UNMODELLED_SL_LEVERS = ("trail_vol_tight_mult", "be_floor_r")


@dataclass
class TrailPlan:
    """What the replay says about one open position."""
    action: str                        # "tighten" | "hold" | "skip"
    why: str
    sl: Optional[float] = None         # the new stop when action == "tighten"
    replay_sl: Optional[float] = None  # the harness stop after the last closed bar
    bars: int = 0                      # closed bars managed since the signal bar
    close_lever: Optional[str] = None  # a close-type lever that WOULD fire now
    detail: Dict[str, Any] = field(default_factory=dict)


def _num(cfg: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    v = _f(cfg.get(key))
    return default if v is None else v


def _closed_bars(candles: pd.DataFrame, start: datetime, tf_min: int, now: datetime) -> pd.DataFrame:
    """Rows whose bar OPENED at/after ``start`` and has CLOSED by ``now``."""
    ts = candles["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        ts = pd.to_datetime(ts, unit="ms", utc=True)
    else:
        ts = pd.to_datetime(ts, utc=True)
    close_t = ts + pd.Timedelta(minutes=tf_min)
    keep = (ts >= pd.Timestamp(start)) & (close_t <= pd.Timestamp(now))
    return candles.loc[keep.values].reset_index(drop=True)


def _floor_tf(t: datetime, tf_min: int) -> datetime:
    epoch = int(t.timestamp()) // 60
    return datetime.fromtimestamp((epoch - epoch % tf_min) * 60, tz=timezone.utc)


def plan_trail(*, leg: Mapping[str, Any], direction: str, entry: float, initial_sl: float,
               signal_time: datetime, resting_sl: Optional[float], candles: Optional[pd.DataFrame],
               now: datetime, price_step: Optional[float] = None) -> TrailPlan:
    """Replay ``scripts/backtest_trend.py``'s trail for one open trade.

    ``entry`` / ``initial_sl`` are the TICKET's (the strategy's decision-bar
    close and its ATR stop); the harness's ``entry``/``sl`` are the same two
    numbers. The managed bars start at the bar after the signal bar, as the
    harness's ``range(entry_i + 1, ...)`` does. Pure: no I/O."""
    if direction not in ("long", "short"):
        return TrailPlan("skip", f"direction {direction!r} not long/short")
    unmodelled = [k for k in _UNMODELLED_SL_LEVERS if _num(leg, k) > 0]
    if unmodelled:
        return TrailPlan("skip", f"leg declares {unmodelled}: not modelled by the prop trail, "
                                 "so a replayed stop would differ from the evidence")
    trail_mult, stop_mult = _f(leg.get("trail_mult")), _f(leg.get("atr_stop_mult"))
    if not trail_mult or not stop_mult or trail_mult <= 0 or stop_mult <= 0:
        return TrailPlan("skip", "leg declares no trail_mult/atr_stop_mult")
    risk = abs(entry - initial_sl)
    if risk <= 0:
        return TrailPlan("skip", "ticket entry == sl")
    if resting_sl is None:
        return TrailPlan("skip", "no resting SL read on the terminal (the executor's containment owns that)")
    tf_min = _TF_MINUTES.get(str(leg.get("timeframe") or "1h"))
    if tf_min is None:
        return TrailPlan("skip", f"timeframe {leg.get('timeframe')!r} unknown")
    if candles is None or len(candles) == 0 or "timestamp" not in candles:
        return TrailPlan("skip", "no candles")
    atr = risk / stop_mult
    # Harness parity (scripts/backtest_trend.py: entry on bar i, trail from
    # entry_i+1). ``signal_time`` is the wall clock when the ticket was built
    # (breakout_executor). A ``decision_bar: forming`` leg (the default; both
    # breakout legs) fires INSIDE the bar that broke the channel, so that bar
    # is harness bar i and is NOT managed: start one bar later. A
    # ``decision_bar: closed`` leg fires just after bar i closed, so the bar
    # holding signal_time already is i+1 (manager re-review of #15316).
    start = _floor_tf(signal_time, tf_min)
    if str(leg.get("decision_bar") or "forming").lower() != "closed":
        start = start + timedelta(minutes=tf_min)
    bars = _closed_bars(candles, start, tf_min, now)
    long_ = direction == "long"

    decay_tight = _num(leg, "trail_decay_tight_mult")
    decay_on = decay_tight > 0.0
    decay_arm_r = _num(leg, "trail_decay_arm_r")
    decay_stall = int(_num(leg, "trail_decay_stall_bars"))

    ext, trail, peak_j, mfe = entry, initial_sl, -1, 0.0
    for j in range(len(bars)):
        bh, bl = float(bars["high"].iloc[j]), float(bars["low"].iloc[j])
        if long_:
            if bh > ext:
                peak_j = j
            ext = max(ext, bh)
            tm = effective_trail_mult(trail_mult, (ext - entry) / risk, j - peak_j, decay_on,
                                      decay_arm_r, decay_stall, decay_tight, False, None, j, 0.0, 0.0, 0.0)
            trail = max(trail, ext - tm * atr)
            mfe = max(mfe, (ext - entry) / risk)
        else:
            if bl < ext:
                peak_j = j
            ext = min(ext, bl)
            tm = effective_trail_mult(trail_mult, (entry - ext) / risk, j - peak_j, decay_on,
                                      decay_arm_r, decay_stall, decay_tight, False, None, j, 0.0, 0.0, 0.0)
            trail = min(trail, ext + tm * atr)
            mfe = max(mfe, (entry - ext) / risk)

    detail = {"atr": atr, "ext": ext, "mfe_r": round(mfe, 4), "trail_mult_last": None if not len(bars) else tm}
    close_lever = None
    n = len(bars)
    if n:
        last_close = float(bars["close"].iloc[-1])
        open_r = (last_close - entry) / risk if long_ else (entry - last_close) / risk
        stale_n = _f(leg.get("stale_exit_bars"))
        # harness: (j - entry_i) >= stale_exit_bars with j = entry_i + n
        if stale_n and n >= stale_n and open_r < _num(leg, "stale_exit_below_r"):
            close_lever = "stale_exit_bars"
        gb_mfe = _num(leg, "giveback_min_mfe_r")
        if gb_mfe > 0 and mfe >= gb_mfe and (mfe - open_r) >= _num(leg, "giveback_r"):
            close_lever = close_lever or "giveback_min_mfe_r"
        detail["open_r"] = round(open_r, 4)

    plan = TrailPlan("hold", "", replay_sl=trail, bars=n, close_lever=close_lever, detail=detail)
    if n == 0:
        plan.why = "no closed bar since the signal bar"
        return plan
    step = price_step if price_step and price_step > 0 else None
    # Round toward the LOOSE side of the harness value (never past it toward
    # price): the venue cannot hold a sub-step price.
    cand = trail
    if step:
        cand = (math.floor(trail / step + 1e-9) * step) if long_ else (math.ceil(trail / step - 1e-9) * step)
        cand = round(cand, 10)
    min_move = step or 1e-9
    tighter = (cand >= resting_sl + min_move) if long_ else (cand <= resting_sl - min_move)
    if not tighter:
        plan.why = "replayed stop does not tighten the resting SL"
        return plan
    # Never an instant stop-out: the new stop must sit on the safe side of the
    # latest price (the last row, forming bar included), as monitor() requires.
    px = float(candles["close"].iloc[-1])
    if (long_ and cand >= px) or (not long_ and cand <= px):
        plan.why = f"replayed stop {cand} is through the current price {px}: not amended"
        return plan
    plan.action, plan.sl, plan.why = "tighten", cand, "replayed trail tightens the resting SL"
    return plan


def _load_legs(path: Path = STRATEGIES_PATH) -> Dict[str, Dict[str, Any]]:
    import yaml

    doc = yaml.safe_load(path.read_text()) or {}
    doc = doc.get("strategies", doc)
    return {k: v for k, v in doc.items() if isinstance(v, dict)}


def _load_state(state_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((state_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state_dir: Path, st: Mapping[str, Any]) -> None:
    p = state_dir / STATE_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, sort_keys=True))
    tmp.replace(p)


MAX_ATTEMPTS_PER_TARGET = 2
# Every armed edit walk on one ticket, confirmed or not (a 1h leg ratchets at
# most once a bar, so this is hours of trailing, not a retry budget).
MAX_ARMED_ATTEMPTS_PER_TICKET = 24
# The new stop must clear the venue quote by this many entry-ATRs.
QUOTE_BUFFER_ATR = 0.1
# The rollout guard bounds one step to ROLLOUT_MAX_TIGHTEN_FRACTION of the
# stop's distance from the venue price; the trail aims inside it by this
# factor so a tick of quote movement between its read and the guard's does
# not turn a bounded step into a refusal.
ROLLOUT_STEP_MARGIN = 0.9
ROLLOUT_FILE = "modify_rollout.json"


def bounded_rollout_sl(side: str, resting_sl: float, target_sl: float, price: float,
                       step: Optional[float]) -> Optional[float]:
    """Pure: the stop for ONE rollout step from ``resting_sl`` toward
    ``target_sl``, moving at most ``ROLLOUT_STEP_MARGIN x
    ROLLOUT_MAX_TIGHTEN_FRACTION`` of the distance to ``price`` (bid for a
    long, ask for a short), rounded to the price step toward the LOOSE side.
    None when that is less than one step of tightening."""
    long_ = side == "long"
    cap = ROLLOUT_STEP_MARGIN * ROLLOUT_MAX_TIGHTEN_FRACTION * abs(price - resting_sl)
    sl = min(target_sl, resting_sl + cap) if long_ else max(target_sl, resting_sl - cap)
    if step and step > 0:
        sl = (math.floor(sl / step + 1e-9) * step) if long_ else (math.ceil(sl / step - 1e-9) * step)
        sl = round(sl, 10)
    min_move = step if step and step > 0 else 1e-9
    tighter = (sl >= resting_sl + min_move) if long_ else (sl <= resting_sl - min_move)
    return sl if tighter else None


def run_trail_step(*, adapter: Any, page: Any, api: Any, cfg: ExecutorConfig, mode: str,
                   state_dir: Path, candles_fn: Callable[[str, str], Optional[pd.DataFrame]],
                   res: Optional[CycleResult] = None, now: Optional[datetime] = None,
                   legs: Optional[Mapping[str, Mapping[str, Any]]] = None) -> CycleResult:
    """One trail pass over the account's open positions. ``live`` amends;
    ``read_only`` walks to the edit control and stops (``arm=False``); ``off``
    does nothing. Never raises past a per-position failure."""
    res = res or CycleResult(mode=mode)
    if mode == "off":
        return res
    live = mode == "live"
    post = api.post_report if live else None
    now = now or datetime.now(timezone.utc)
    legs = legs if legs is not None else _load_legs()
    enabled = {s.upper() for s in (cfg.enabled_venue_symbols or [])}
    venue_to_bot = {str(v.get("venue") or "").upper(): b for b, v in cfg.symbols.items()}

    try:
        positions = adapter.read_positions(page)
        journal = api.open_fills(cfg.account_id)
        tickets = {t.get("ticket_id"): t for t in api.all_tickets(cfg.account_id) if t.get("ticket_id")}
    except Exception as exc:
        res.alerts.append(f"trail: read failed ({type(exc).__name__}); no trail this tick")
        return res
    st = _load_state(state_dir)
    rollout = ModifyRollout(Path(state_dir) / ROLLOUT_FILE)
    jr = {(str(j.get("symbol") or "").upper(), _dir(j.get("direction"))): j for j in journal}

    def _one(p: Position) -> None:
        venue = p.symbol.upper()
        if venue not in enabled:
            return
        bot_sym = venue_to_bot.get(venue)
        j = jr.get((str(bot_sym or "").upper(), p.side))
        if j is None:
            res.log("trail_skip", venue=venue, why="no open journal row (orphan handling is the reconcile's)")
            return
        tid = j.get("ticket_id")
        t = tickets.get(tid) or {}
        leg = legs.get(str(t.get("strategy") or ""))
        entry, isl, sig_t = _f(t.get("entry")), _f(t.get("sl")), _parse_ts(t.get("signal_time"))
        if not leg or entry is None or isl is None or sig_t is None:
            res.log("trail_skip", ticket_id=tid, venue=venue,
                    why="ticket or leg not found (strategy/entry/sl/signal_time)")
            return
        try:
            candles = candles_fn(bot_sym, str(leg.get("timeframe") or "1h"))
        except Exception as exc:  # noqa: BLE001
            candles = None
            res.log("trail_skip", ticket_id=tid, why=f"candles failed ({type(exc).__name__})")
        plan = plan_trail(leg=leg, direction=p.side or "", entry=entry, initial_sl=isl,
                          signal_time=sig_t, resting_sl=p.stop_loss, candles=candles, now=now,
                          price_step=_f((cfg.symbols.get(bot_sym) or {}).get("price_step")))
        tst = st.setdefault(str(tid), {})
        res.log("trail_plan", ticket_id=tid, venue=venue, action=plan.action, why=plan.why,
                resting_sl=p.stop_loss, new_sl=plan.sl, replay_sl=plan.replay_sl, bars=plan.bars,
                close_lever=plan.close_lever, **plan.detail)
        if plan.close_lever and not tst.get("close_lever_alerted"):
            res.alerts.append(
                f"{tid}: {venue} declared close lever `{plan.close_lever}` WOULD exit now "
                f"(open_r={plan.detail.get('open_r')}); a resting-SL amend cannot reproduce a "
                "bar-close exit, so the prop trail does NOT apply it — operator decides")
            tst["close_lever_alerted"] = True
        if plan.action != "tighten" or tst.get("locked"):
            return
        # Through-price guard on the VENUE's own quote (the plan only saw the
        # Bybit feed): a long stop must sit a buffer under the bid, a short
        # one over the ask. No quote = could not look = no amend.
        q = adapter.read_quote(page, venue) or {}
        bid, ask = _f(q.get("bid")), _f(q.get("ask"))
        step = _f((cfg.symbols.get(bot_sym) or {}).get("price_step"))
        buf = max(2 * (step or 0.0), QUOTE_BUFFER_ATR * float(plan.detail.get("atr") or 0.0))
        if (p.side == "long" and (bid is None or plan.sl > bid - buf)) or \
                (p.side == "short" and (ask is None or plan.sl < ask + buf)):
            res.log("trail_skip", ticket_id=tid, why="venue quote missing or within the buffer of the new stop",
                    bid=bid, ask=ask, buffer=buf, new_sl=plan.sl)
            return
        target = plan.sl
        if live:
            # ROLLOUT: one watched step per reviewed clear (module docstring).
            blocked = rollout.blocked()
            if blocked:
                res.log("trail_skip", ticket_id=tid, why="rollout latch set: " + blocked, new_sl=plan.sl)
                if not tst.get("rollout_paused_alerted"):
                    res.alerts.append(f"{tid}: trail PAUSED at SL {p.stop_loss} (target {plan.sl}): one watched "
                                      "modify step per reviewed clear; review it, then executor-clear-rollout")
                    tst["rollout_paused_alerted"] = True
                return
            tst.pop("rollout_paused_alerted", None)
            px = bid if p.side == "long" else ask
            target = bounded_rollout_sl(p.side, float(p.stop_loss), plan.sl, float(px), step)
            if target is None:
                res.log("trail_skip", ticket_id=tid, why="the bounded rollout step is under one price step",
                        new_sl=plan.sl, bid=bid, ask=ask)
                return
            if target != plan.sl:
                res.log("trail_bounded", ticket_id=tid, target=plan.sl, step_sl=target,
                        fraction=ROLLOUT_STEP_MARGIN * ROLLOUT_MAX_TIGHTEN_FRACTION)
        if int(tst.get("armed_attempts") or 0) >= MAX_ARMED_ATTEMPTS_PER_TICKET:
            res.log("trail_skip", ticket_id=tid, why=f"{MAX_ARMED_ATTEMPTS_PER_TICKET} armed attempts used for this ticket")
            return
        tries = tst.get("tries") if tst.get("target") == target else 0
        if (tries or 0) >= MAX_ATTEMPTS_PER_TARGET:
            res.log("trail_skip", ticket_id=tid, why=f"amend to {target} already tried {tries}x; alerted")
            return
        r = adapter.modify_bracket(page, p, target, None, arm=live, rollout=rollout)
        res.log("trail_amend", ticket_id=tid, venue=venue, sl=target, result=r)
        if not live:
            return
        if isinstance(r, dict) and not r.get("clicked"):
            # Refused before any click (e.g. the edit dialog is unmeasured):
            # nothing changed on the venue. One alert per ticket, no count.
            if not tst.get("refused_alerted"):
                res.alerts.append(f"{tid}: trail amend to {target} refused by the adapter ({r.get('why')})")
                tst["refused_alerted"] = True
            return
        tst.update(target=target, tries=(tries or 0) + 1,
                   armed_attempts=int(tst.get("armed_attempts") or 0) + 1)
        if isinstance(r, dict) and r.get("rollout") == "verify_failed":
            # The guard's own next-read check failed and its latch halts every
            # further modify: a "restore" would be a second blind modify the
            # rollout forbids. Lock this ticket and leave it to the reviewer.
            tst["locked"] = True
            res.alerts.append(f"{tid}: trail step to {target}: {r.get('why')}; trail locked for this ticket, "
                              "the reviewer decides (containment acts only if the SL or TP goes missing)")
            return
        confirmed = _confirm(adapter, page, p, target, step)
        if confirmed is None and _loosened(adapter, page, p):
            # A stop that came back LOOSER than before is NOT restored: the
            # step just used the account's single rollout modify (its latch
            # refuses a second one, and a move back to a looser SL is not a
            # tighten). The trail locks for this ticket; containment and the
            # reviewer decide (manager review of #15316, 2026-10-03).
            tst["locked"] = True
            res.alerts.append(f"{tid}: SL LOOSENED after the trail amend to {target} (was {p.stop_loss}); "
                              "NOT restored — the rollout latch forbids a second modify; the reviewer decides "
                              "(containment acts only if the SL or TP goes missing); trail locked")
            return
        if confirmed is None:
            res.alerts.append(f"{tid}: trail amend to {target} not confirmed on re-read (result: {r.get('why') if isinstance(r, dict) else r})")
            return
        if confirmed.take_profit is None and p.take_profit is not None:
            res.alerts.append(f"{tid}: TP missing after the trail amend — the executor's journal reconcile "
                                  "reads it as NAKED and contains it (one repair, else close at market) once "
                                  "two consecutive reads agree")
        tst.update(applied_sl=target, tries=0)
        _report(res, post, {"kind": "amend", "account_id": cfg.account_id, "ticket_id": tid,
                            "symbol": bot_sym, "direction": p.side, "sl": confirmed.stop_loss,
                            "tp": confirmed.take_profit,
                            "reason": f"trail: {leg.get('timeframe')} chandelier replay "
                                      f"(bars={plan.bars}, mfe_r={plan.detail.get('mfe_r')}"
                                      + (f", rollout step toward {plan.sl}" if target != plan.sl else "") + ")",
                            "source": "prop_trail"})
    for p in positions:
        try:
            _one(p)
        except Exception as exc:  # noqa: BLE001 — a trail failure must never reach the tick
            # An escaped exception would count toward the executor's
            # TRIP_CONSECUTIVE_ERRORS and halt new entries on the live book.
            res.alerts.append(f"trail: {p.symbol} step failed ({type(exc).__name__}); skipped this tick")

    # Local file only (no API write), so read_only keeps it too: one close-lever
    # alert per ticket, and rows for closed positions are dropped.
    open_ids = {str(j.get("ticket_id")) for j in journal}
    try:
        _save_state(state_dir, {k: v for k, v in st.items() if k in open_ids})
    except OSError as exc:
        res.alerts.append(f"trail: state not saved ({type(exc).__name__})")
    return res


def _confirm(adapter: Any, page: Any, p: Position, sl: float, step: Optional[float]) -> Optional[Position]:
    """The position re-read with its SL at ``sl`` (within half a step), or None."""
    tol = (step or 0.0) / 2 + 1e-9
    try:
        for q in adapter.read_positions(page):
            if q.symbol.upper() == p.symbol.upper() and q.side == p.side and q.stop_loss is not None \
                    and abs(q.stop_loss - sl) <= max(tol, abs(sl) * 1e-7):
                return q
    except Exception:  # noqa: BLE001 — an unread confirm is reported as not confirmed
        return None
    return None


def _loosened(adapter: Any, page: Any, p: Position) -> bool:
    """True when the re-read SL is looser than ``p.stop_loss``. A missing SL
    cell or an unreadable re-read is not proof of either and returns False (the
    caller alerts that the amend did not confirm)."""
    try:
        for q in adapter.read_positions(page):
            if q.symbol.upper() == p.symbol.upper() and q.side == p.side:
                if q.stop_loss is None:
                    # An SL cell that did not parse is UNKNOWN, not loosened:
                    # "restoring" on a misread would be a second blind click.
                    return False
                return q.stop_loss < p.stop_loss if p.side == "long" else q.stop_loss > p.stop_loss
    except Exception:  # noqa: BLE001
        return False
    return False


def default_candles_fn(limit: int = 300) -> Callable[[str, str], Optional[pd.DataFrame]]:
    """Fresh (uncached) candles from the SAME feed the signal was priced on."""
    from src.runtime.market_data import connector_for_symbol, fetch_candles

    def fn(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        return fetch_candles(symbol, timeframe, limit=limit, bypass_cache=True,
                             exchange_client=connector_for_symbol(symbol, {}))
    return fn


__all__ = ["TrailPlan", "plan_trail", "run_trail_step", "default_candles_fn", "bounded_rollout_sl",
           "MAX_ATTEMPTS_PER_TARGET"]
