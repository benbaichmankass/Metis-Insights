"""M28 P3 — the macro/value thesis sleeve tick (isolated, observe-only).

The thesis-generation *scanner* (design §P3, M22 pairs-sleeve pattern): a
slow-cadence pass that reads the point-in-time valuation snapshots (P1),
reconstructs each symbol's strongest value read, forms a weeks-horizon
:class:`~.thesis.TradeThesis` via the S1 rule-based former (P3 §5 S1), and logs
the would-be theses to a soak — **observe-only**. It places **nothing**: the
defined-risk options executor is P5 (not yet wired), so there is no order path
here regardless of the ``execution`` gate.

Two halves, mirroring ``pairs_executor``:

- :func:`form_tick_theses` — the **pure** decision core (no I/O, no clock): given
  the snapshot rows + the sleeve config + an injected ``now``/``id_prefix``, it
  returns the draft theses this scan would form. Fully unit-testable.
- :func:`run_macro_thesis_tick` — the thin I/O wrapper called once per trader
  tick from ``src/main.py``: cadence-gates, reads the valuation store, forms the
  theses, and appends them to the soak (+ persists the draft book to the thesis
  store). **Best-effort — never raises, never blocks a trade, never places an
  order.**
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable, Mapping, Optional

from .thesis import TradeThesis
from .thesis_engine import form_theses_from_reads, value_conviction
from .valuation import ValueRead

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "macro_thesis_soak.jsonl"
TICK_STATE_NAME = "macro_thesis_tick.json"

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "config", "macro_theses.yaml"
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def load_sleeve_config(path: Optional[str] = None) -> dict:
    """Load ``config/macro_theses.yaml`` → the ``sleeve`` block. Fail-permissive
    → a safe default (``execution: shadow``) on any error."""
    default = {"execution": "shadow", "cadence_seconds": 3600, "min_conviction": 0.4,
               "account": "alpaca_options_paper", "express_as": "debit_vertical",
               "universe": []}
    try:
        import yaml
        with open(path or _DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        sleeve = (data or {}).get("sleeve") if isinstance(data, dict) else None
        if not isinstance(sleeve, Mapping):
            return default
        out = dict(default)
        out.update({k: v for k, v in sleeve.items() if v is not None})
        return out
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# pure core
# ---------------------------------------------------------------------------


def _valueread_from_snapshot(row: Mapping[str, Any]) -> ValueRead:
    """Reconstruct a :class:`ValueRead` from a stored ``valuation_snapshots`` row
    (the feed persisted every field; see ``valuation_feed._read_to_row``)."""
    n = row.get("n_history")
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return ValueRead(
        metric=str(row.get("metric", "")),
        value=row.get("value"),
        percentile=row.get("percentile"),
        z_score=row.get("z_score"),
        cheap_score=row.get("cheap_score"),
        label=str(row.get("label", "unknown")),
        n=n,
        higher_is_cheaper=bool(row.get("higher_is_cheaper", True)),
        note=str(row.get("note", "")),
    )


def _strongest_read_by_symbol(rows: Iterable[Mapping[str, Any]]) -> dict[str, ValueRead]:
    """One :class:`ValueRead` per symbol — the highest-conviction metric governs
    (``|cheap_score-0.5|``). A symbol whose only reads are score-less is dropped."""
    best: dict[str, tuple[float, ValueRead]] = {}
    for row in rows or []:
        symbol = row.get("symbol")
        if not symbol:
            continue
        read = _valueread_from_snapshot(row)
        conv = value_conviction(read)
        if conv is None:
            continue
        prev = best.get(symbol)
        if prev is None or conv > prev[0]:
            best[symbol] = (conv, read)
    return {sym: rd for sym, (_c, rd) in best.items()}


def form_tick_theses(
    snapshot_rows: Iterable[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    now_iso: str,
    id_prefix: str,
) -> list[TradeThesis]:
    """PURE: the draft theses this scan would form from the snapshot rows.

    One thesis per symbol with a directional, ``>= min_conviction`` value read
    (the strongest metric governs). A ``universe`` allowlist in ``cfg`` (non-empty)
    restricts the symbols. No I/O, no clock — ``now_iso``/``id_prefix`` injected so
    a scan replays identically. Sorted by descending conviction."""
    reads = _strongest_read_by_symbol(snapshot_rows)
    universe = [str(s) for s in (cfg.get("universe") or [])]
    if universe:
        reads = {s: r for s, r in reads.items() if s in set(universe)}
    return form_theses_from_reads(
        reads,
        id_prefix=id_prefix,
        created_at=now_iso,
        min_conviction=float(cfg.get("min_conviction", 0.0) or 0.0),
        express_as=str(cfg.get("express_as", "debit_vertical")),
        account=cfg.get("account", "alpaca_options_paper"),
    )


def _thesis_soak_row(thesis: TradeThesis, *, execution_mode: str, at: str) -> dict:
    """One observe-only soak row for a would-be thesis (the P3 record)."""
    val = thesis.valuation or {}
    return {
        "event": "would_form",
        "thesis_id": thesis.thesis_id,
        "symbol": (thesis.instrument or {}).get("symbol"),
        "direction": thesis.direction,
        "conviction": thesis.thesis_conviction,
        "metric": val.get("metric"),
        "label": val.get("label"),
        "cheap_score": val.get("cheap_score"),
        "account": thesis.account,
        "express_as": (thesis.instrument or {}).get("express_as"),
        "execution_mode": execution_mode,
        "placed": False,     # P3 is observe-only — the executor is P5
        "at": at,
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _runtime_path(name: str, path: Optional[Any]):
    if path is not None:
        from pathlib import Path
        return Path(path)
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / name


def write_thesis_soak(rows: Iterable[dict], *, path: Optional[Any] = None) -> int:
    """Append observe-only soak rows to ``runtime_logs/macro_thesis_soak.jsonl``."""
    p = _runtime_path(SOAK_LOG_NAME, path)
    written = 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows or []:
                try:
                    fh.write(json.dumps(row, default=str) + "\n")
                    written += 1
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("thesis_tick: soak append failed (%s)", exc)
    return written


def read_thesis_soak(*, path: Optional[Any] = None, limit: Optional[int] = None) -> list[dict]:
    """Newest-first tail of the observe-only thesis soak."""
    p = _runtime_path(SOAK_LOG_NAME, path)
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    out.reverse()
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def _should_run(now_epoch: float, cadence_seconds: float, *, state_path) -> bool:
    """Wall-clock cadence gate: True when ``cadence_seconds`` have elapsed since the
    last recorded run (or on the first run). Fail-open (unreadable state → run)."""
    if cadence_seconds <= 0:
        return True
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            last = float(json.load(fh).get("last_run_epoch", 0.0))
    except (OSError, ValueError, TypeError):
        return True
    return (now_epoch - last) >= cadence_seconds


def _record_run(now_epoch: float, *, state_path) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump({"last_run_epoch": now_epoch}, fh)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# I/O wrapper — called once per trader tick from src/main.py
# ---------------------------------------------------------------------------


def run_macro_thesis_tick(settings: Any = None, *, config_path: Optional[str] = None) -> Optional[dict]:
    """Once-per-tick observe-only scan (design §P3). Best-effort — never raises,
    never blocks a trade, **never places an order** (the executor is P5).

    Cadence-gated; on a due tick it reads the latest valuation snapshots, forms
    the draft theses (S1 former), appends them to the soak, and persists the draft
    book to the thesis store. Returns a small summary dict (or ``None`` when it
    didn't run this tick) — for callers/tests; the trader ignores it."""
    try:
        import time
        from .valuation_store import read_latest_snapshots
        from .thesis_store import write_theses

        cfg = load_sleeve_config(config_path)
        cadence = float(cfg.get("cadence_seconds", 3600) or 0)
        state_path = _runtime_path(TICK_STATE_NAME, None)
        now_epoch = time.time()
        if not _should_run(now_epoch, cadence, state_path=state_path):
            return None
        _record_run(now_epoch, state_path=state_path)

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
        id_prefix = time.strftime("%Y%m%d", time.gmtime(now_epoch))
        execution_mode = str(cfg.get("execution", "shadow")).strip().lower()

        snapshots = read_latest_snapshots()  # dict[(symbol,metric), row]
        theses = form_tick_theses(
            snapshots.values(), cfg=cfg, now_iso=now_iso, id_prefix=id_prefix
        )
        if theses:
            write_theses(theses)  # persist the current draft book (bounded: 1 id/symbol/day)
            write_thesis_soak(
                (_thesis_soak_row(t, execution_mode=execution_mode, at=now_iso) for t in theses)
            )
        logger.info("macro_thesis_tick: formed %d observe-only thesis(es) [%s]",
                    len(theses), execution_mode)
        return {"formed": len(theses), "execution_mode": execution_mode, "at": now_iso}
    except Exception:  # noqa: BLE001 — best-effort, must never break the trader tick
        logger.exception("macro_thesis_tick failed")
        return None
