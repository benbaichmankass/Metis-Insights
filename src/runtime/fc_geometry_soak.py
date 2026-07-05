"""fc-geometry shadow-soak — M19 D1 (the faithful Phase-2 test, observe-only).

**Observe-only — changes nothing the order path does.** Per opening order this
logs, next to the SL/TP bracket **actually placed**, the decision-time
**quantile-forecast snapshot** (the ``fc_*`` row the money-box's
``forecast_live`` reader served for the symbol at that moment). The offline
3-arm backtest that tried to answer "does fc-vol-scaled SL/TP geometry help?"
failed its own reality-calibration anchor by ~0.6R
(``docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md``,
``MB-20260705-FC-SLTP-GEOMETRY``) — live trades close on fees/monitor/flip/
reconciler exits, not clean barriers — so the honest instrument is this live
soak: real orders, real fc, realized outcomes.

Deliberate split of responsibilities:

- **This live writer logs decision-time facts only** — placed geometry + the
  live fc snapshot. It does NOT compute the fc-scaled counterfactual, so the
  scaling parameterization (clamp bounds, median window) is never baked into
  the soak and can be varied at analysis time without a re-soak.
- **Outcome resolution happens trainer-side**
  (``scripts/ml/fc_geometry_resolve.py``): the fc-vol-scaled counterfactual
  barriers are constructed from the LOGGED ``fc_range_rel`` and resolved
  against realized candles, with an explicit **censored** flag whenever a
  counterfactual reaches the max-hold cap or the data edge unresolved — the
  design requirement from the shadow-mode literature (counterfactual exits are
  only partially identified; a truncated counterfactual must never be silently
  scored as resolved). See the D1 row of ROADMAP § M19 "Next research
  directions".

**Nothing reads this back** — no live exit changes. Any eventual fc→geometry
order change is Tier-3 (operator + soak evidence), a deliberate change to the
exit path, not the flip of a dormant switch — so there is **no ``*_ENABLED``
gate** here (Prime Directive; same posture as ``exit_ladder_soak``).

Mirrors ``src/runtime/exit_ladder_soak.py``: a pure ``build_fc_geometry_record``
(never raises → ``None`` on an un-loggable order) plus a best-effort append-only
writer to ``runtime_logs/fc_geometry_soak.jsonl`` and a read envelope for the
Tier-1 endpoint (``/api/bot/fc-geometry/soak``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "fc_geometry_soak.jsonl"


def build_fc_geometry_record(
    *,
    venue: str,
    strategy: str,
    symbol: str,
    direction: str,
    entry: Any,
    sl: Any,
    tp: Any,
    qty: Any,
    account_id: str = "",
    account_class: str = "",
    timeframe: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build the observe-only soak record: placed geometry + live fc snapshot.

    ``qty`` is the real sized order qty. The fc snapshot is read through
    ``forecast_live.compute_live_forecast_row`` WITHOUT the timeframe parity
    guard — the forecast side-stream runs on its own (15m) cadence regardless
    of the order's timeframe, exactly like the offline as-of join the backtest
    used; the resolver re-joins the trainer's fc side-stream by timestamp as
    the parity cross-check. A missing fc row is still logged
    (``fc_present:false``) so the soak's coverage denominator is honest.

    Returns a JSON-serialisable record, or ``None`` when the placed geometry
    itself is unusable (no entry/sl/tp/qty). Pure w.r.t. the order path;
    **never raises.**
    """
    try:
        e = float(entry or 0.0)
        s = float(sl or 0.0)
        t = float(tp or 0.0)
        q = float(qty or 0.0)
        if e <= 0 or s <= 0 or t <= 0 or q <= 0:
            return None

        fc_row: Optional[Dict[str, float]] = None
        try:
            from src.runtime.forecast_live import compute_live_forecast_row

            fc_row = compute_live_forecast_row(str(symbol))
        except Exception:  # noqa: BLE001 — fc read must never block the record
            fc_row = None

        placed: Dict[str, Any] = {"entry": e, "sl": s, "tp": t, "qty": q}
        if isinstance(extra, dict):
            placed.update(extra)
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "venue": str(venue or ""),
            "account_id": str(account_id or ""),
            "account_class": str(account_class or ""),
            "strategy": str(strategy or ""),
            "symbol": str(symbol or ""),
            "direction": str(direction or ""),
            "timeframe": str(timeframe or ""),
            "placed": placed,
            "fc_present": fc_row is not None,
            "fc_row": fc_row,
            "fc_source": "forecast_live",
        }
    except Exception:  # noqa: BLE001 — observe-only soak must never crash the path
        return None


def record_fc_geometry_soak(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Build + append the soak record (best-effort). Returns the record or ``None``.

    Never raises — a soak-log write failure must never lose the order. Accepts
    the same keyword args as :func:`build_fc_geometry_record`.
    """
    record = build_fc_geometry_record(**kwargs)
    if record is None:
        return None
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / SOAK_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        logger.debug(
            "fc_geometry_soak(observe) account=%s symbol=%s fc_present=%s (unchanged)",
            record.get("account_id"), record.get("symbol"), record.get("fc_present"),
        )
    except OSError as exc:
        logger.warning("record_fc_geometry_soak: could not write soak log: %s", exc)
    return record


def soak_log_path():
    """Resolve the soak-log path under the canonical runtime-logs dir."""
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SOAK_LOG_NAME


def read_soak_records(
    *,
    limit: int = 100,
    symbol: Optional[str] = None,
    account_id: Optional[str] = None,
    fc_only: bool = False,
) -> Dict[str, Any]:
    """Read newest-first soak records + a small aggregate summary.

    Pure read path backing ``/api/bot/fc-geometry/soak``. Filters (optional):
    ``symbol``, ``account_id``, and ``fc_only`` (rows where a live fc snapshot
    was present — the rows the resolver can actually score). ``limit`` caps the
    returned rows after filtering. **Never raises** — returns a well-formed
    envelope (``present:false`` before the writer's first row, ``error`` on a
    read failure).

    ``summary`` aggregates over all rows scanned: per-symbol counts and the
    fc-coverage split — the headline "is the soak accruing, and what fraction
    of orders had a live forecast at decision time?".
    """
    path = soak_log_path()
    if not path.exists():
        return {"present": False, "log_path": str(path), "count": 0,
                "records": [], "summary": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("read_soak_records: could not read %s — %s", path, exc)
        return {"present": True, "log_path": str(path), "count": 0,
                "records": [], "summary": {}, "error": str(exc)}

    s_filter = str(symbol) if symbol else None
    a_filter = str(account_id) if account_id else None

    by_symbol: Dict[str, int] = {}
    fc_present = 0
    total = 0
    records: List[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if s_filter is not None and str(rec.get("symbol", "")) != s_filter:
            continue
        if a_filter is not None and str(rec.get("account_id", "")) != a_filter:
            continue
        has_fc = bool(rec.get("fc_present"))
        if fc_only and not has_fc:
            continue
        total += 1
        by_symbol[str(rec.get("symbol", ""))] = by_symbol.get(str(rec.get("symbol", "")), 0) + 1
        if has_fc:
            fc_present += 1
        if len(records) < limit:
            records.append(rec)

    return {
        "present": True,
        "log_path": str(path),
        "count": len(records),
        "records": records,
        "summary": {
            "total_scanned": total,
            "by_symbol": by_symbol,
            "fc_present": fc_present,
            "fc_coverage_pct": round(100.0 * fc_present / total, 1) if total else 0.0,
        },
    }
