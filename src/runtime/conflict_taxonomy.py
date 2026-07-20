"""M26 P1 — TF-ratio conflict taxonomy soak (observe-only).

**Observe-only — changes nothing the order path does.** When the intent
layer's ``hold`` flip policy suppresses an opposing signal
(``flip_suppressed_hold_policy`` — the position is kept for the owner's
monitor()/SL/TP exit), this soak classifies the conflict by the **clock
ratio** between the held strategy's timeframe and the opposing signal's
timeframe, and appends one JSONL row to
``runtime_logs/conflict_taxonomy_soak.jsonl``.

The taxonomy is EMPIRICAL, not speculative — M26 P0
(``docs/research/M26-P0-conflict-bleed-findings-2026-07-19.md``, full-coverage
rerun over 121 measured trade-conflict pairs):

- **cross-clock (ratio ≥ 4×)** conflicts are benign to hold through
  (tail_held +$4,694 over 81 pairs) — a fast counter-signal against a slow
  position is mostly noise at the slow clock → class ``coexist``;
- **same/near-clock (ratio < 4×)** conflicts lose money BOTH ways (held
  −$2,982 AND flip −$7,126 over 38 pairs; close beats hold 65.8%) — the real
  transition warning → class ``transition_vote``.

This log is P2's live feed (the transition score weights same-clock
opposition clusters) and the evidence trail for the P3 policy arms
(transition-triggered exit-tighten via the M20 levers — Tier-3, operator- and
backtest-gated). Mirrors the conviction-sizing / allocator / exit-ladder
soaks: pure builder (never raises → ``None``) + best-effort append-only
writer + ``read_soak_records`` for a future read surface. **Nothing reads it
back** — the hold decision is unchanged, so there is no ``*_ENABLED`` gate
(Prime Directive: observe-only writers ship baseline).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "conflict_taxonomy_soak.jsonl"

# P0-empirical coexistence threshold: clock ratio >= 4x -> cross-clock
# (`coexist`); < 4x -> same/near-clock (`transition_vote`). Matches the
# stratification the P0 miner measured (scripts/research/
# m26_p0_conflict_bleed.py) so live rows and the offline evidence share one
# definition.
COEXIST_TF_RATIO: float = 4.0

# Conflict classes.
CLASS_COEXIST = "coexist"
CLASS_TRANSITION_VOTE = "transition_vote"
CLASS_UNKNOWN = "unknown_tf"

_TF_MINUTES: Dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "45m": 45, "1h": 60, "2h": 120, "3h": 180, "4h": 240, "6h": 360,
    "8h": 480, "12h": 720, "1d": 1440, "d": 1440, "1w": 10080,
}

# strategies.yaml timeframe map cache: {"mtime": float|None, "map": {...}}.
_tf_cache: Dict[str, Any] = {"mtime": None, "map": {}}


def timeframe_minutes(tf: Any) -> Optional[int]:
    """Map a timeframe string (``"5m"``/``"1h"``/``"1d"``…) to minutes.

    Pure; ``None`` for anything unrecognised (never raises).
    """
    try:
        return _TF_MINUTES.get(str(tf or "").strip().lower())
    except Exception:  # noqa: BLE001
        return None


def _strategy_tf_map() -> Dict[str, int]:
    """strategy name -> timeframe minutes from ``config/strategies.yaml``.

    mtime-cached; best-effort — any read/parse failure returns the last
    good map (or ``{}``), never raises. Reads the raw YAML directly because
    ``strategy_registry.load_strategies`` deliberately omits ``timeframe``.
    """
    try:
        import yaml

        from src.utils.paths import repo_root

        path = os.path.join(repo_root(), "config", "strategies.yaml")
        mtime = os.path.getmtime(path)
        if _tf_cache["mtime"] == mtime and _tf_cache["map"]:
            return _tf_cache["map"]
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        out: Dict[str, int] = {}
        for name, cfg in (data.get("strategies") or {}).items():
            if not isinstance(cfg, dict):
                continue
            minutes = timeframe_minutes(cfg.get("timeframe"))
            if minutes:
                out[str(name)] = minutes
        _tf_cache["mtime"] = mtime
        _tf_cache["map"] = out
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("conflict_taxonomy: strategy TF map unavailable: %s", exc)
        return _tf_cache["map"] or {}


def classify_tf_ratio(
    held_tf_min: Optional[int], opposing_tf_min: Optional[int],
) -> Dict[str, Any]:
    """Classify a conflict by held-vs-opposing clock ratio.

    Returns ``{"tf_ratio": float|None, "conflict_class": str}`` where the
    class is ``coexist`` (ratio ≥ ``COEXIST_TF_RATIO``), ``transition_vote``
    (< ratio), or ``unknown_tf`` (either timeframe unresolvable). Pure.
    """
    try:
        if held_tf_min and opposing_tf_min:
            hi = float(max(held_tf_min, opposing_tf_min))
            lo = float(min(held_tf_min, opposing_tf_min))
            ratio = hi / max(1.0, lo)
            cls = (
                CLASS_COEXIST if ratio >= COEXIST_TF_RATIO
                else CLASS_TRANSITION_VOTE
            )
            return {"tf_ratio": round(ratio, 4), "conflict_class": cls}
    except Exception:  # noqa: BLE001
        pass
    return {"tf_ratio": None, "conflict_class": CLASS_UNKNOWN}


def _held_open_trade(
    account_id: str, symbol: str, *, db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Most-recent open trade's ``{strategy, direction, qty}`` for
    ``(account, symbol)`` — the HELD side of the conflict.

    Read-only, best-effort: journal miss / read failure -> ``None`` (the row
    still logs with ``held_strategy: null``); never raises.
    """
    try:
        from src.utils.paths import trade_journal_db_path

        path = db_path or trade_journal_db_path()
        if not os.path.exists(path):
            return None
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT strategy_name, direction, position_size FROM trades "
                "WHERE account_id = ? AND symbol = ? AND status = 'open' "
                "  AND COALESCE(is_backtest, 0) = 0 "
                "ORDER BY id DESC LIMIT 1",
                (account_id, symbol),
            ).fetchone()
        if not row:
            return None
        return {"strategy": row[0], "direction": row[1], "qty": row[2]}
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "conflict_taxonomy: held-trade read failed for %s/%s: %s",
            account_id, symbol, exc,
        )
        return None


def build_conflict_record(
    *,
    account_id: str,
    symbol: str,
    opposing_strategy: Optional[str],
    opposing_side: Optional[str],
    opposing_confidence: Optional[float] = None,
    current_signed_qty: Optional[float] = None,
    suppression_reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build one taxonomy row for a hold-policy flip suppression.

    Never raises — anything un-derivable is logged as ``None`` fields (an
    honest partial row beats a dropped observation); only a total failure
    returns ``None``.
    """
    try:
        tf_map = _strategy_tf_map()
        held = _held_open_trade(account_id, symbol, db_path=db_path) or {}
        held_strategy = held.get("strategy")
        held_tf = tf_map.get(str(held_strategy)) if held_strategy else None
        opp_tf = tf_map.get(str(opposing_strategy)) if opposing_strategy else None
        cls = classify_tf_ratio(held_tf, opp_tf)
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id,
            "symbol": symbol,
            "held_strategy": held_strategy,
            "held_direction": held.get("direction"),
            "held_qty": held.get("qty"),
            "held_tf_min": held_tf,
            "opposing_strategy": opposing_strategy,
            "opposing_side": opposing_side,
            "opposing_confidence": opposing_confidence,
            "opposing_tf_min": opp_tf,
            "current_signed_qty": current_signed_qty,
            "tf_ratio": cls["tf_ratio"],
            "conflict_class": cls["conflict_class"],
            "coexist_threshold": COEXIST_TF_RATIO,
            "suppression_reason": suppression_reason,
        }
    except Exception:  # noqa: BLE001 — observe-only; never break a tick
        logger.debug("build_conflict_record: skipped (un-derivable)")
        return None


def record_conflict(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Build + append the taxonomy row (best-effort). Returns it or ``None``.

    Never raises — a soak-log write failure must never lose/strand a signal.
    Accepts the same keyword args as :func:`build_conflict_record`.
    """
    record = build_conflict_record(**kwargs)
    if record is None:
        return None
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / SOAK_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        logger.debug(
            "conflict_taxonomy(observe) %s/%s held=%s opp=%s ratio=%s class=%s",
            record.get("account_id"), record.get("symbol"),
            record.get("held_strategy"), record.get("opposing_strategy"),
            record.get("tf_ratio"), record.get("conflict_class"),
        )
    except OSError as exc:
        logger.warning("record_conflict: could not write soak log: %s", exc)
    return record


def soak_log_path():
    """Resolve the soak-log path under the canonical runtime-logs dir."""
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SOAK_LOG_NAME


def read_soak_records(limit: int = 200) -> Dict[str, Any]:
    """Newest-first tail of the taxonomy soak log (for a read surface).

    Same envelope shape as the sibling soaks: ``{present, log_path, count,
    records, summary}``. Best-effort — unreadable/absent log -> ``present:
    False``, never raises.
    """
    path = soak_log_path()
    envelope: Dict[str, Any] = {
        "present": False, "log_path": str(path), "count": 0,
        "records": [], "summary": {},
    }
    try:
        if not path.exists():
            return envelope
        records = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        records.reverse()
        by_class: Dict[str, int] = {}
        for r in records:
            key = str(r.get("conflict_class") or CLASS_UNKNOWN)
            by_class[key] = by_class.get(key, 0) + 1
        envelope.update({
            "present": True,
            "count": len(records),
            "records": records[: max(0, int(limit))],
            "summary": {"total_scanned": len(records), "by_class": by_class},
        })
        return envelope
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_soak_records(conflict_taxonomy) failed: %s", exc)
        return envelope
