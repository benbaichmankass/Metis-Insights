"""M20-X — live vol-conditional trailing-stop lever (trend/pullback family
monitors).

Harness reference: the ``--trail-vol-*`` lever in
``scripts/backtest_trend.py`` / ``scripts/backtest_pullback.py``
(design: ``docs/research/M20X-vol-conditional-trail-DESIGN.md``) — the
effective chandelier trail mult TIGHTENS on any managed bar whose trailing
ATR percentile sits in a gated tail:

* **cold tail** — ``trail_vol_below_pctl`` > 0 and the current closed bar's
  ATR percentile < that bound (the low-vol tail; ETH's PASS cell).
* **hot tail** — ``trail_vol_above_pctl`` > 0 and the percentile > that bound.

The percentile is the RANK of the current bar's ATR within the trailing
``vol_pctl_window`` (default 200) bars, ``pct=True`` — byte-identical to the
harness's ``atr.rolling(window, min_periods=window).rank(pct=True).iloc[i]``.
The ATR series is the SAME SMA-of-TR the live unit's ``_atr`` computes
(``min_periods=1``), so live == train.

Contract (identical to the stale/giveback/trail-decay levers):

* **Declared** — ``trail_vol_tight_mult`` > 0 AND (``trail_vol_below_pctl`` > 0
  OR ``trail_vol_above_pctl`` > 0) in the package meta (threaded from strategy
  YAML by ``order_package``) or live cfg ⇒ :func:`resolve_vol_trail_mult`
  returns ``min(base_mult, tight_mult)`` on a firing bar (the STOP ratchet in
  the caller never loosens regardless), and writes ONE observe-only row to
  ``exit_lever_soak.jsonl`` (``lever="vol_trail"``, ``applied=True``) so the
  paper test has a queryable record of every real fire.
* **Undeclared** ⇒ the base mult is returned unchanged (byte-identical monitor
  behaviour); no annotate row (the fleet sweep already produced the offline
  verdicts — no per-leg soak needed here).
* **Window unfilled** (fewer than ``vol_pctl_window`` bars, or a NaN in the
  trailing window) ⇒ base mult (fail-permissive), matching the harness's
  ``min_periods=window`` NaN → never-fire.
* Fail-safe on every missing input; **never raises** into the monitor.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW = 200
_DEFAULT_ATR_PERIOD = 14


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def resolve_vol_trail_mult(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    candles_df: Any,  # pandas DataFrame of closed OHLC bars
    base_mult: float,
    direction: str,
    open_pkg: Optional[Dict[str, Any]] = None,
) -> float:
    """Return the EFFECTIVE trail mult for this closed bar (base or tightened).

    ``candles_df`` is the frame the monitor already holds, WHICH MAY END ON A
    STILL-FORMING BAR (the caller's fetch does not guarantee closed-only).
    This function drops it itself (``src.runtime.closed_bars.drop_forming_bar``,
    keyed on ``timeframe`` from ``meta``/``cfg_dict``) before computing
    anything, so the percentile is evaluated at the last CLOSED row —
    matching the harness, which only ever sees closed historical bars.
    Composes with :func:`src.runtime.trail_decay.resolve_trail_mult` via
    ``min`` — the caller passes the possibly-decay-tightened mult as
    ``base_mult`` and the tighter of the two wins, mirroring the harness
    ``_tm = min(_tm, tight)``.
    """
    try:
        def _pick(key: str) -> Any:
            mv = meta.get(key)
            return mv if mv is not None else cfg_dict.get(key)

        tight = _f(_pick("trail_vol_tight_mult"))
        above = _f(_pick("trail_vol_above_pctl")) or 0.0
        below = _f(_pick("trail_vol_below_pctl")) or 0.0
        declared = tight is not None and tight > 0 and (above > 0.0 or below > 0.0)
        if not declared:
            return base_mult

        # CLOSED BARS ONLY (train/live parity — the CA-B01 class,
        # docs/claude/work/ audit 2026-09-27, src/runtime/closed_bars.py).
        # `candles_df` as received here is whatever the caller's ``fetch_candles``
        # returned, whose LAST row is the still-forming bar (unfixed call site;
        # PI-20260929-EXITOPS-0005 files the sibling gap in the M21 entry
        # vol-skip gate, which reads this same shape). The harness's ATR
        # percentile is computed over ONLY closed historical bars — a forming
        # bar's partial high/low/close would distort the ATR value the
        # percentile ranks, which is exactly the skew #6207 / CA-B01 name.
        # Trim before ANYTHING is computed off `candles_df` so the window-size
        # check below also reflects the trimmed frame.
        try:
            from src.runtime.closed_bars import drop_forming_bar

            timeframe = str(_pick("timeframe") or "")
            if timeframe:
                candles_df = drop_forming_bar(candles_df, timeframe)
        except Exception:  # noqa: BLE001 — trimming must never break the lever
            pass

        try:
            win = int(_pick("vol_pctl_window") or _DEFAULT_WINDOW)
        except (TypeError, ValueError):
            win = _DEFAULT_WINDOW
        if win <= 0:
            return base_mult
        if candles_df is None or len(candles_df) < win:
            # Fail-permissive (matches the harness's min_periods=window NaN),
            # but LOGGED rather than a silent no-op (PI-20260929-VOLSKIP-0002):
            # a declared-but-permanently-inert lever is indistinguishable from
            # a shipped-and-working one to anyone not reading the logs, and
            # that gap is exactly what let this go unnoticed once already
            # (the caller's fetch limit was sized to the window with zero
            # margin for the forming-bar drop above). A single, genuinely
            # transient short fetch is expected and not alarming; a lever
            # that logs this on every tick is the signal something is wrong
            # with the caller's candle supply, not with this function.
            n = 0 if candles_df is None else len(candles_df)
            logger.info(
                "trail_vol: window unfilled (have %d closed bars, need %d) "
                "-- lever declared but cannot score this tick, base mult kept",
                n, win,
            )
            return base_mult

        try:
            period = int(_pick("atr_period") or _DEFAULT_ATR_PERIOD)
        except (TypeError, ValueError):
            period = _DEFAULT_ATR_PERIOD

        # Reuse the SAME ATR + trailing-percentile helpers the already-live
        # M21 vol-at-entry GATE ships in the unit (`_trailing_atr_pctl` =
        # `rolling(win, min_periods=win).rank(pct=True).iloc[idx]`), so the
        # exit lever and the entry gate can never drift. Lazy import keeps the
        # unit↔this-module dependency acyclic (the unit imports this only
        # inside monitor(); this imports the unit only inside this call).
        from src.units.strategies.trend_donchian import _atr, _trailing_atr_pctl

        atr = _atr(candles_df, period)
        vp = _trailing_atr_pctl(atr, len(atr) - 1, win)
        if vp is None:  # window unfilled / NaN — fail-permissive (never fires)
            return base_mult
        fired = ((above > 0.0 and vp > above)
                 or (below > 0.0 and vp < below))
        if not fired:
            return base_mult

        eff = min(float(base_mult), float(tight))
        # Observe-only paper-test evidence row (declared + fired): the
        # queryable record of every real trail tightening on the ETH leg.
        pkg = open_pkg or {}
        try:
            from src.runtime.exit_lever_soak import record_exit_lever_annotation

            record_exit_lever_annotation(
                lever="vol_trail",
                strategy=str(meta.get("strategy_label")
                             or pkg.get("strategy_name") or "unknown"),
                symbol=str(pkg.get("symbol") or ""),
                direction=direction,
                order_package_id=pkg.get("order_package_id"),
                params={"trail_vol_below_pctl": below,
                        "trail_vol_above_pctl": above,
                        "trail_vol_tight_mult": float(tight),
                        "vol_pctl_window": win,
                        "base_trail_mult": float(base_mult)},
                state={"atr_pctl": round(vp, 4),
                       "eff_mult": round(eff, 4),
                       "applied": True},
            )
        except Exception:  # noqa: BLE001 — soak logging must never affect the path
            pass
        return eff
    except Exception:  # noqa: BLE001 — the monitor must never feel this
        logger.debug("trail_vol: resolve failed", exc_info=True)
        return base_mult
