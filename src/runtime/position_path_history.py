"""Per-bar position PATH history — the append-only twin of ``position_telemetry``.

WHY THIS EXISTS (checklist rows EXIT-PATH-TELEMETRY-LIVE / IN-TRADE-EXIT-MODEL)
------------------------------------------------------------------------------
``position_telemetry`` keeps ONE upserted row per order package: the latest
pass wins, so a live trade's path is overwritten bar by bar and MAE is stored
nowhere. The in-trade exit model — P(SL before TP | trade state at bar t) — is
learned OFFLINE from harness emits; this table is what lets it be PARITY-checked
on live trades (harness state at bar t == live state at bar t, the
exit-refinement skill's parity step) and later served in shadow.

Operator approval, 2026-10-15 ~06:00Z, verbatim choice: "Build it
(Recommended)" — "It only adds recording; trading behaviour doesn't change."

SHAPE
-----
One row per (order_package_id, bar_ts). ``bar_ts`` is the timestamp of the
newest candle in the monitor's frame on that pass. The writer runs on every
exit-loop pass (~30s), so a bar sees many passes: the row is UPSERTED within
its bar and the stored values are the LAST pass observed while that candle was
the newest — i.e. the state at that bar's close, to within one pass. ``passes``
and ``first_seen_at`` say how many passes and from when, so a bar observed once
(a restart, a gap) is distinguishable from one observed throughout.

FOUR CONSTRAINTS (the same ones ``position_telemetry`` carries)
---------------------------------------------------------------
1. **A projection, never a second truth.** Every R quantity is copied from the
   record ``position_telemetry.build_record`` already built on the same pass —
   the same ``since_entry_peak`` and the same ``r_distances``. The only new
   quantities are ``mae_r`` (the since-entry trough, under the SAME anchoring
   guards as the peak), ``sl_r``/``tp_r`` (where the levels sit relative to
   entry) and ``atr``/``adx`` (the shared ``wilder_adx``; ATR prefers the
   value the monitor's own trail is using and says which via ``atr_source``).
2. **Observe-only, never raises, never halts.** Nothing reads this back to
   change an exit. A write failure is contained to that one write, logged at
   WARNING once per process (then DEBUG), and the next pass simply tries again.
3. **No rows after close.** A package whose ``position_telemetry`` row carries
   ``terminal_state='final'`` gets no further history rows, so a late monitor
   pass after the close path ran cannot extend a finished path.
4. **Bounded.** ``RETENTION_DAYS`` of history, pruned at most once an hour per
   process on the write path (``prune_history``).

``peak_r``/``mae_r`` are bar-extreme reconstructions: ESTIMATED, never MEASURED
(``src/runtime/provenance.py``); an intrabar excursion is invisible to them.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "position_path_history"

#: History older than this (by ``written_at``) is deleted. 90 days covers every
#: live hold measured to date several times over, at a bounded table size.
RETENTION_DAYS = 90

#: Prune at most this often per process — the DELETE rides the write path.
_PRUNE_EVERY_S = 3600.0

#: Wilder period for the ATR/ADX columns (the live default everywhere).
_PERIOD = 14

#: How many trailing candles feed ATR/ADX — enough for Wilder to converge,
#: small enough that the computation stays cheap on the exit loop.
_TAIL = 120

#: Where ``atr`` came from. Never collapsed: a computed ATR is not the one the
#: trail used, and "we had no frame" is not an ATR of 0.
ATR_SOURCES = ("monitor", "computed_wilder14", "unavailable")

_last_prune: dict[str, float] = {}
_warned: set = set()


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _r(value: float | None, nd: int = 4) -> float | None:
    return None if value is None else round(value, nd)


def _warn_once(key: str, msg: str) -> None:
    if key in _warned:
        logger.debug(msg, exc_info=True)
        return
    _warned.add(key)
    logger.warning(msg, exc_info=True)


def _bar_ts(frame) -> str | None:
    """ISO-UTC timestamp of the newest candle, or ``None`` when unknowable."""
    if frame is None:
        return None
    try:
        import pandas as pd
        if len(frame) == 0:
            return None
        if "timestamp" in getattr(frame, "columns", []):
            raw = frame["timestamp"].iloc[-1]
        else:
            raw = frame.index[-1]
            if not isinstance(raw, (pd.Timestamp, datetime)):
                return None
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.isoformat()
    except Exception:  # noqa: BLE001 — a frame we cannot key yields no row
        return None


def _mae_r(window, entry: float | None, risk: float | None,
           direction: str) -> float | None:
    """Since-entry adverse excursion in R from bar extremes (<= 0 when adverse).

    Called only when the record's ``peak_state`` is ``measured``, i.e. under
    exactly the anchoring/risk/length guards ``since_entry_peak`` applied, so
    the trough and the peak are measured over the same frame.
    """
    if entry is None or not risk or risk <= 0 or window is None:
        return None
    try:
        if direction == "long":
            trough = float(window["low"].astype(float).to_numpy().min())
            v = (trough - entry) / risk
        else:
            trough = float(window["high"].astype(float).to_numpy().max())
            v = (entry - trough) / risk
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    return v if math.isfinite(v) else None


def _level_r(level: float | None, entry: float | None,
             risk: float | None, is_long: bool) -> float | None:
    """Where a price level sits relative to entry, in R (+ = profit side)."""
    lv = _f(level)
    if lv is None or entry is None or not risk or risk <= 0:
        return None
    return ((lv - entry) if is_long else (entry - lv)) / risk


def _atr_adx(candles_df, monitor_atr: float | None) -> tuple:
    """(atr, atr_source, adx). Never raises."""
    atr = _f(monitor_atr)
    source = "monitor" if atr is not None and atr > 0 else None
    if source is None:
        atr = None
    adx = None
    if candles_df is None:
        return atr, source or "unavailable", None
    try:
        tail = candles_df.tail(_TAIL)
        if len(tail) >= 2 * _PERIOD:
            from src.runtime.regime.detector import wilder_adx
            adx = _f(wilder_adx(tail, _PERIOD).iloc[-1])
            if source is None:
                import pandas as pd
                h, low, c = (tail["high"].astype(float), tail["low"].astype(float),
                             tail["close"].astype(float))
                pc = c.shift(1)
                tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()],
                               axis=1).max(axis=1)
                atr = _f(tr.ewm(alpha=1.0 / _PERIOD, adjust=False).mean().iloc[-1])
                if atr is not None and atr > 0:
                    source = "computed_wilder14"
                else:
                    atr = None
    except Exception:
        logger.debug("position_path_history: atr/adx failed", exc_info=True)
    return atr, source or "unavailable", adx


def build_history_row(record: dict[str, Any], *, window=None, candles_df=None,
                      stop: float | None = None, target: float | None = None,
                      atr: float | None = None,
                      now: datetime | None = None) -> dict[str, Any] | None:
    """The history row for one pass, projected from a telemetry ``record``.

    Returns ``None`` when the pass cannot be attributed to a package AND a bar
    — an unkeyable row is worse than no row. Pure; never raises.
    """
    try:
        if not record or not record.get("order_package_id"):
            return None
        bar_ts = _bar_ts(candles_df) or _bar_ts(window)
        if bar_ts is None:
            return None
        entry = _f(record.get("entry"))
        risk = _f(record.get("risk_per_unit"))
        direction = record.get("direction") or ""
        is_long = direction == "long"
        # collapsed-state: measured — a BINARY gate, "was the peak measured?",
        # which `unanchored` / `thin_window` / `no_risk` all answer the same way:
        # no anchored frame, so no trough either. The reason is NOT lost —
        # `peak_state` is copied verbatim onto the same row, the `is_measured()`
        # shape `position_telemetry.enrich_record` uses for `peak_r`.
        mae = (_mae_r(window, entry, risk, direction)
               if record.get("peak_state") == "measured" else None)
        atr_v, atr_src, adx = _atr_adx(candles_df, atr)
        ts = (now or datetime.now(timezone.utc)).isoformat()
        return {
            "order_package_id": str(record["order_package_id"]),
            "bar_ts": bar_ts,
            "trade_id": record.get("trade_id"),
            "account_id": record.get("account_id"),
            "strategy": record.get("strategy"),
            "symbol": record.get("symbol"),
            "direction": direction or None,
            "entry": record.get("entry"),
            "risk_per_unit": record.get("risk_per_unit"),
            "last_price": record.get("last_price"),
            "open_r": record.get("open_r"),
            "peak_r": record.get("peak_r"),
            "peak_state": record.get("peak_state"),
            "mae_r": _r(mae),
            "giveback_r": record.get("giveback_r"),
            "bars_held": record.get("bars_held"),
            "bars_since_peak": record.get("bars_since_peak"),
            "sl_price": _r(_f(stop), 8),
            "tp_price": _r(_f(target), 8),
            "sl_r": _r(_level_r(stop, entry, risk, is_long)),
            "tp_r": _r(_level_r(target, entry, risk, is_long)),
            "r_to_stop": record.get("r_to_stop"),
            "r_to_target": record.get("r_to_target"),
            "rr_from_here": record.get("rr_from_here"),
            "atr": _r(atr_v, 8),
            "atr_source": atr_src,
            "adx": _r(adx, 2),
            "first_seen_at": ts,
            "written_at": ts,
        }
    except Exception:  # noqa: BLE001 — never raise into the monitor
        _warn_once("build", "position_path_history: row build failed")
        return None


_COLS = ("order_package_id", "bar_ts", "trade_id", "account_id", "strategy",
         "symbol", "direction", "entry", "risk_per_unit", "last_price", "open_r",
         "peak_r", "peak_state", "mae_r", "giveback_r", "bars_held",
         "bars_since_peak", "sl_price", "tp_price", "sl_r", "tp_r", "r_to_stop",
         "r_to_target", "rr_from_here", "atr", "atr_source", "adx",
         "first_seen_at", "written_at")

_UPDATABLE = [c for c in _COLS
              if c not in ("order_package_id", "bar_ts", "first_seen_at")]

_UPSERT = (
    f"INSERT INTO {TABLE} ({', '.join(_COLS)}, passes) "
    f"VALUES ({', '.join(':' + c for c in _COLS)}, 1) "
    f"ON CONFLICT(order_package_id, bar_ts) DO UPDATE SET "
    + ", ".join(
        # COALESCE on the identity columns only: a later pass whose enrichment
        # missed must not wipe an account or trade id already established.
        f"{c}=COALESCE(excluded.{c}, {TABLE}.{c})"
        if c in ("trade_id", "account_id") else f"{c}=excluded.{c}"
        for c in _UPDATABLE)
    + f", passes={TABLE}.passes + 1"
)


def _is_final(conn: sqlite3.Connection, pkg_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT terminal_state FROM position_telemetry "
            "WHERE order_package_id = ?", (pkg_id,)).fetchone()
    except sqlite3.Error:
        return False  # allow-silent: no telemetry table means no stamp to honour
    return bool(row and str(row[0] or "").strip().lower() == "final")


def prune_history(conn: sqlite3.Connection, *, now: datetime | None = None,
                  retention_days: int = RETENTION_DAYS) -> int:
    """Delete rows whose ``written_at`` is older than the retention bound."""
    cutoff = ((now or datetime.now(timezone.utc))
              - timedelta(days=retention_days)).isoformat()
    cur = conn.execute(f"DELETE FROM {TABLE} WHERE written_at < ?", (cutoff,))
    return cur.rowcount or 0


def write_history(row: dict[str, Any] | None,
                  db_path: str | None = None) -> bool:
    """Best-effort upsert of one history row. Returns whether it landed.

    Never raises. Skips (returns False) when the package's telemetry row is
    already stamped ``final`` — no history after close.
    """
    if not row:
        return False
    try:
        from src.utils.paths import trade_journal_db_path
        path = db_path or str(trade_journal_db_path())
        with sqlite3.connect(path, timeout=5.0) as conn:
            if _is_final(conn, row["order_package_id"]):
                return False
            if row.get("account_id") in (None, "") and row.get("trade_id"):
                try:
                    hit = conn.execute("SELECT account_id FROM trades WHERE id = ?",
                                       (int(row["trade_id"]),)).fetchone()
                    if hit and hit[0]:
                        row = dict(row, account_id=hit[0])
                except (sqlite3.Error, TypeError, ValueError):
                    pass  # allow-silent: enrichment only — the row still writes
            conn.execute(_UPSERT, row)
            mono = time.monotonic()
            if mono - _last_prune.get(path, -_PRUNE_EVERY_S - 1) >= _PRUNE_EVERY_S:
                _last_prune[path] = mono
                prune_history(conn)
        return True
    except Exception:  # noqa: BLE001 — never raise into the monitor; next pass retries
        _warn_once("write", "position_path_history: write failed "
                            "(contained; trading unaffected; retried next pass)")
        return False


def read_history(db_path: str | None = None, *,
                 order_package_id: str | None = None,
                 trade_id: str | None = None,
                 strategy: str | None = None,
                 since: str | None = None,
                 limit: int = 2000) -> dict[str, Any]:
    """History rows, filtered, as an envelope (never a bare list).

    With a package or trade filter the rows come back in BAR order (a path);
    otherwise newest-written first. ``present: false`` means the table does not
    exist yet, which is not the same fact as ``count: 0``.
    """
    envelope: dict[str, Any] = {
        "present": False, "count": 0, "packages": 0, "rows": [],
        "retention_days": RETENTION_DAYS, "atr_sources": list(ATR_SOURCES),
        "peak_provenance": "estimated", "error": None,
    }
    try:
        from src.utils.paths import trade_journal_db_path
        path = db_path or str(trade_journal_db_path())
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            where, params = [], []
            for col, val in (("order_package_id", order_package_id),
                             ("trade_id", trade_id), ("strategy", strategy)):
                if val:
                    where.append(f"{col} = ?")
                    params.append(str(val))
            if since:
                where.append("bar_ts >= ?")
                params.append(since)
            sql = f"SELECT * FROM {TABLE}"
            if where:
                sql += " WHERE " + " AND ".join(where)
            path_view = bool(order_package_id or trade_id)
            sql += (" ORDER BY order_package_id, bar_ts ASC" if path_view
                    else " ORDER BY written_at DESC")
            sql += " LIMIT ?"
            params.append(max(1, min(int(limit), 10000)))
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        envelope["error"] = str(exc)
        return envelope
    envelope.update({
        "present": True, "count": len(rows), "rows": rows,
        "packages": len({r["order_package_id"] for r in rows}),
    })
    return envelope
