import sqlite3
from datetime import datetime, timezone

from src.runtime.signal_notifications import ensure_signals_table, insert_signal
from src.utils.paths import data_dir


def _db_path() -> str:
    return str(data_dir() / "trades.db")


def write_signal(symbol, signal_type, direction=None, price=None, timeframe=None, reason=None, metadata="{}"):
    conn = sqlite3.connect(_db_path())
    ensure_signals_table(conn)
    insert_signal(
        conn=conn,
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        signal_type=signal_type,
        direction=direction,
        price=price,
        timeframe=timeframe,
        reason=reason,
        metadata=metadata,
    )
    conn.close()


def _write_ict_signals_from_meta(signal: dict, settings: dict) -> None:
    """Write individual ICT detections (FVGs, order blocks) even when no
    trade is taken — extracted from pipeline.py (PR-9 / D1)."""
    if not isinstance(signal, dict):
        return

    meta = signal.get("meta") or {}
    symbol = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
    timeframe = settings.get("TIMEFRAME", "15m")

    fvgs = meta.get("fvgs") or []
    for fvg in fvgs:
        if not isinstance(fvg, dict):
            continue
        fvg_type = fvg.get("type", "unknown")
        gap_low = fvg.get("gap_low")
        gap_high = fvg.get("gap_high")
        price = None
        if gap_low is not None and gap_high is not None:
            try:
                price = (float(gap_low) + float(gap_high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"fvg_{fvg_type}",
            direction=fvg_type,
            price=price,
            timeframe=timeframe,
            reason="ICT FVG detected",
            metadata=str(fvg),
        )

    order_blocks = meta.get("order_blocks") or meta.get("obs") or []
    for ob in order_blocks:
        if not isinstance(ob, dict):
            continue
        ob_type = ob.get("type", "unknown")
        low = ob.get("low")
        high = ob.get("high")
        price = None
        if low is not None and high is not None:
            try:
                price = (float(low) + float(high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"ob_{ob_type}",
            direction=ob_type,
            price=price,
            timeframe=timeframe,
            reason="ICT order block detected",
            metadata=str(ob),
        )
