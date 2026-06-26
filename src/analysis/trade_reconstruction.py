"""Reconstruct the would-be SL/TP outcome of a broker-truncated trade.

Bucket C (``paper_record_classifier``): a full position the broker / reconciler /
stuck-watchdog closed mid-flight (or one still open at the window edge). The live
exit is an artifact, but the *decision* is gradeable — we have entry + SL + TP, so
we can replay the candles from entry forward and ask: **which level did price touch
first?** That recovers a ``reconstructed_win`` / ``reconstructed_loss`` /
``open_at_window_end`` label even though we never saw a clean exit.

The first-touch core (``first_touch_outcome``) is **pure and stdlib-only** — it
operates on a list of ``(high, low)`` bars, so it is fully testable without pandas
or network. ``reconstruct_record`` is the thin adapter that fetches candles via the
bot's own canonical fetcher (``src.runtime.market_data.fetch_candles``) and feeds
the bars in; it is the only part that needs pandas / an exchange connector and is
import-lazy so this module loads cleanly in a sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ReconOutcome:
    """Result of a first-touch reconstruction."""
    outcome: str                 # 'tp' | 'sl' | 'open'
    label: str                   # 'reconstructed_win' | 'reconstructed_loss' | 'open_at_window_end'
    bars_to_resolution: Optional[int]   # index of the resolving bar (0-based), None if open
    ambiguous: bool              # a single bar straddled BOTH levels
    r_multiple: Optional[float]  # realized R if resolved (tp=+reward/risk, sl=-1.0)
    reason: str


def _norm_direction(direction: Any) -> Optional[str]:
    d = str(direction or "").strip().lower()
    if d in ("long", "buy"):
        return "long"
    if d in ("short", "sell"):
        return "short"
    return None


def first_touch_outcome(
    direction: Any,
    entry: float,
    sl: float,
    tp: float,
    bars: Sequence[Tuple[float, float]],
    *,
    pessimistic: bool = True,
) -> ReconOutcome:
    """Walk *bars* (each ``(high, low)``, chronological, AFTER entry) and return
    whether price hit TP or SL first.

    Long:  TP when ``high >= tp``;  SL when ``low <= sl``.
    Short: TP when ``low <= tp``;   SL when ``high >= sl``.

    When a single bar straddles BOTH levels the outcome is intrabar-ambiguous
    (we don't have sub-bar ticks). ``pessimistic=True`` (default) resolves it to
    SL — the conservative assumption for grading. The resolving bar is flagged
    ``ambiguous=True`` either way so a reviewer can see how many reconstructions
    leaned on the tie-break.

    Returns ``outcome='open'`` (``open_at_window_end``) when no bar touches either
    level within the supplied window.
    """
    d = _norm_direction(direction)
    if d is None:
        return ReconOutcome("open", "open_at_window_end", None, False, None,
                            f"unrecognised direction {direction!r}")
    try:
        entry, sl, tp = float(entry), float(sl), float(tp)
    except (TypeError, ValueError):
        return ReconOutcome("open", "open_at_window_end", None, False, None,
                            "non-numeric entry/sl/tp")

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    r_mult = (reward / risk) if risk > 0 else None

    for i, bar in enumerate(bars):
        try:
            high, low = float(bar[0]), float(bar[1])
        except (TypeError, ValueError, IndexError):
            continue
        if d == "long":
            hit_tp = high >= tp
            hit_sl = low <= sl
        else:
            hit_tp = low <= tp
            hit_sl = high >= sl
        if hit_tp and hit_sl:
            if pessimistic:
                return ReconOutcome("sl", "reconstructed_loss", i, True, -1.0,
                                    "intrabar straddle — pessimistic SL-first")
            return ReconOutcome("tp", "reconstructed_win", i, True, r_mult,
                                "intrabar straddle — optimistic TP-first")
        if hit_tp:
            return ReconOutcome("tp", "reconstructed_win", i, False, r_mult,
                                "TP touched first")
        if hit_sl:
            return ReconOutcome("sl", "reconstructed_loss", i, False, -1.0,
                                "SL touched first")
    return ReconOutcome("open", "open_at_window_end", None, False, None,
                        "neither level touched within the candle window")


# --- candle adapter (import-lazy; needs pandas + a connector) --------------


def _bars_from_candles(candles: Any, after_ts: Optional[float]) -> List[Tuple[float, float]]:
    """Convert a fetch_candles DataFrame (or list of dicts) to ``(high, low)``
    bars chronologically AFTER ``after_ts`` (epoch seconds). Pure-ish — accepts
    either a pandas DataFrame or an iterable of mappings so tests need no pandas.
    """
    rows: List[Tuple[float, float, float]] = []  # (ts, high, low)
    # pandas DataFrame path
    if hasattr(candles, "iterrows"):
        for _, r in candles.iterrows():
            ts = r.get("timestamp") if hasattr(r, "get") else r["timestamp"]
            rows.append((float(ts), float(r["high"]), float(r["low"])))
    else:
        for r in (candles or []):
            ts = r.get("timestamp", r.get("time"))
            rows.append((float(ts), float(r["high"]), float(r["low"])))
    rows.sort(key=lambda x: x[0])
    # Normalise epoch-ms → epoch-s if needed (a 13-digit ts).
    def _norm(ts: float) -> float:
        return ts / 1000.0 if ts > 1e12 else ts
    out: List[Tuple[float, float]] = []
    for ts, high, low in rows:
        if after_ts is None or _norm(ts) > _norm(after_ts):
            out.append((high, low))
    return out


def reconstruct_record(
    rec: Dict[str, Any],
    *,
    fetch_fn: Any = None,
    timeframe: str = "15m",
    limit: int = 500,
    settings: Optional[Dict[str, Any]] = None,
    pessimistic: bool = True,
) -> Optional[ReconOutcome]:
    """Reconstruct one Bucket-C record's outcome from candles.

    ``fetch_fn(symbol, timeframe, limit)`` returns OHLCV (a fetch_candles-shaped
    DataFrame or a list of ``{timestamp/time, high, low}`` dicts). Defaults to the
    bot's ``src.runtime.market_data.fetch_candles`` (import-lazy, needs pandas +
    a connector). Returns ``None`` when the record lacks entry/sl/tp/timestamp or
    candles are unavailable — the caller keeps the record as un-reconstructed.
    """
    entry = rec.get("entry_price")
    sl = rec.get("stop_loss")
    tp = rec.get("take_profit_1") if rec.get("take_profit_1") is not None else rec.get("take_profit")
    symbol = rec.get("symbol")
    if entry is None or sl is None or tp is None or not symbol:
        return None

    entry_ts = _parse_ts(rec.get("created_at") or rec.get("timestamp"))

    _fetch = fetch_fn
    if _fetch is None:
        try:
            from src.runtime.market_data import fetch_candles

            def _fetch(sym, tf, lim):  # noqa: ANN001
                return fetch_candles(sym, tf, settings=settings or {}, limit=lim)
        except Exception:  # noqa: BLE001
            return None

    try:
        candles = _fetch(symbol, timeframe, limit)
    except Exception:  # noqa: BLE001
        return None
    if candles is None:
        return None

    bars = _bars_from_candles(candles, entry_ts)
    if not bars:
        return None
    return first_touch_outcome(
        rec.get("direction"), entry, sl, tp, bars, pessimistic=pessimistic,
    )


def _parse_ts(value: Any) -> Optional[float]:
    """Best-effort epoch-seconds from an ISO string or epoch (s/ms) number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    s = str(value).strip()
    if not s:
        return None
    # Pure-numeric string → epoch
    try:
        v = float(s)
        return v / 1000.0 if v > 1e12 else v
    except ValueError:
        pass
    try:
        from datetime import datetime
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).timestamp()
    except Exception:  # noqa: BLE001
        return None
