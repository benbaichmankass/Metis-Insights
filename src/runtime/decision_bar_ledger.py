"""Restart-safe decision bars — which bar each leg last decided on, on disk.

WHY THIS EXISTS (RESTART-SAFE, PI-20261003-OJTPWGCC-0001)
---------------------------------------------------------
The live trader restarts on every ``main`` merge that touches runtime code
(``ict-git-sync`` → ``scripts/deploy_pull_restart.sh``): 112 process starts in
2026-09-30T15:58Z..2026-10-03T06:00Z, every one of them a git-sync deploy
(MEASURED: the ``/api/bot/exit-interval/soak`` ``restart_gap.gaps`` process
boundaries matched against the ``ict-git-sync`` journal, read 2026-10-03).

The ``decision_bar: closed`` gate kept "this leg already decided on bar X" in a
process-local dict, and decided by WALL CLOCK whether a closed bar was still
actionable (the first 360 s after its close). Two failures followed:

* a restart inside a leg's window, before its first evaluation, could SKIP a
  real entry: the new process came up after 360 s and the bar was simply lost;
* a restart after an in-window evaluation printed a FALSE "never evaluated"
  warning, because the new process could not know the old one had decided
  (2026-09-30: the 16:00Z bar was evaluated at 20:02:07Z, the trader restarted
  at 20:06:39Z, and the new process warned at 20:07:10Z — ``ict-trader-live``
  journal, read 2026-10-03).

This module replaces the dict with a small JSON ledger in
``runtime_state_dir()`` (atomic tmp + ``os.replace``, the same pattern as
``alert_cooldown``), so the decision is driven by the CANDLE HISTORY and the
ledger, not by "what minute is it in this process":

* a closed bar is evaluated if, and only if, the ledger has not disposed of it
  AND it is still inside the staleness bound below;
* it is recorded BEFORE the strategy runs (at-most-once: a crash mid-evaluation
  loses that bar rather than evaluating it twice);
* a bar that is too old is recorded as ``stale_skipped`` with its reason, so it
  is never evaluated later either.

WHY A FILE AND NOT A JOURNAL TABLE
----------------------------------
This is control state (one row per leg, overwritten), not a record of what the
system produced — the evaluations themselves already land in
``signal_audit`` / ``order_packages``. ``pairs_executor`` keeps the equivalent
state for the pairs sleeve in ``pairs_decision_bars.json`` the same way.

THE STALENESS BOUND, AND WHY THESE NUMBERS
------------------------------------------
A caught-up decision is entered at today's price, not at the bar close the
strategy read. Two bounds, both checked:

1. TIME — ``catchup_bound_seconds``: ``tf / 16`` (4h → 900 s, 1h → 225 s,
   15m → 56 s), never below a closed leg's own ``decision_bar_fresh_seconds``
   window (default 360 s) and never a full bar. Price diffuses roughly as
   ``ATR(tf) · sqrt(t / tf)``, so ``t = tf/16`` keeps the EXPECTED drift near
   0.25·ATR — a sixth of the 1.5·ATR stop on the only closed leg today
   (``trend_donchian_xrp_4h``, ``atr_stop_mult`` 1.5). For 4h it also covers
   the measured restart join (max 267 s over 1,100 soak boundaries) plus a
   tick and the settle delay. INFERRED from those named inputs; override per
   leg with ``decision_bar_catchup_seconds``.
2. PRICE — ``catchup_price_check``: the REALIZED drift. The current price must
   still sit inside the trade's own brackets (the repo's existing invalidation
   rule, ``prop_invalidation_prompt``: a run to SL means the setup already
   failed, a run to TP means the move already happened) AND within
   ``0.25 × |entry − SL|`` of the decision price (the same 0.25 as above, read
   off the leg's own stop). Override per leg with
   ``decision_bar_catchup_max_drift_stop_frac``.

A caught-up signal whose current price cannot be read is refused (fail closed):
the bound is what makes a late entry safe, so an unchecked one is not taken.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

LEDGER_NAME = "decision_bar_ledger.json"

#: When this process started, and who it is — what lets a reader of the ledger
#: say "evaluated by a PREVIOUS process" instead of guessing.
PROCESS_STARTED = time.time()
PID = os.getpid()

#: Time bound as a fraction of the bar (see module docstring, bound 1).
CATCHUP_BAR_FRACTION = 1.0 / 16.0
#: Realized-drift bound as a fraction of |entry - SL| (bound 2).
DEFAULT_MAX_DRIFT_STOP_FRAC = 0.25

_LOCK = threading.Lock()
_STATE: Optional[dict] = None
_PATH_OVERRIDE: Optional[Path] = None


def ledger_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    from src.utils.paths import runtime_state_dir
    return runtime_state_dir() / LEDGER_NAME


def _load_locked() -> dict:
    global _STATE
    if _STATE is None:
        try:
            raw = json.loads(ledger_path().read_text(encoding="utf-8"))
            legs = raw.get("legs") if isinstance(raw, dict) else None
            _STATE = {"legs": legs if isinstance(legs, dict) else {}}
        except FileNotFoundError:
            _STATE = {"legs": {}}
        except Exception as exc:  # noqa: BLE001
            # Unreadable ledger: start empty. The worst case is one bar being
            # re-decided after a restart, which is what happened before the
            # ledger existed; the DB-backed same-bar entry guard still applies.
            logger.warning("decision_bar_ledger: unreadable (%s) — starting empty", exc)
            _STATE = {"legs": {}}
    return _STATE


def _save_locked() -> None:
    try:
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_STATE, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.warning("decision_bar_ledger: save failed (%s)", exc)


def get(key: str) -> Optional[dict]:
    """The leg's ledger row, or ``None`` when nothing has been recorded."""
    with _LOCK:
        row = _load_locked()["legs"].get(key)
        return dict(row) if isinstance(row, dict) else None


def update(key: str, **fields: Any) -> dict:
    """Merge ``fields`` into the leg's row (stamping pid + process start), persist,
    and return the new row."""
    with _LOCK:
        legs = _load_locked()["legs"]
        row = dict(legs.get(key) or {})
        row.update(fields)
        row["pid"] = PID
        row["process_started"] = PROCESS_STARTED
        legs[key] = row
        _save_locked()
        return dict(row)


def update_many(rows: dict) -> None:
    """``update`` for several legs with ONE write (the per-tick forming record)."""
    if not rows:
        return
    with _LOCK:
        legs = _load_locked()["legs"]
        for key, fields in rows.items():
            row = dict(legs.get(key) or {})
            row.update(fields)
            row["pid"] = PID
            row["process_started"] = PROCESS_STARTED
            legs[key] = row
        _save_locked()


def by_previous_process(row: Optional[dict]) -> bool:
    return bool(row) and (row.get("pid") != PID
                          or row.get("process_started") != PROCESS_STARTED)


def reset_for_tests(path: Optional[Path] = None) -> None:
    """Drop the in-memory copy (simulates a process restart) and optionally
    point the ledger at ``path``."""
    global _STATE, _PATH_OVERRIDE
    with _LOCK:
        _STATE = None
        if path is not None:
            _PATH_OVERRIDE = path


# ---------------------------------------------------------------- staleness


def catchup_bound_seconds(tf_s: float, vcfg: Optional[dict] = None, *,
                          floor_s: float = 0.0) -> float:
    """How long after a bar CLOSES it may still be decided on (bound 1)."""
    vcfg = vcfg or {}
    explicit = vcfg.get("decision_bar_catchup_seconds")
    if explicit is not None:
        bound = float(explicit)
    else:
        bound = max(float(tf_s) * CATCHUP_BAR_FRACTION, float(floor_s))
    return min(bound, float(tf_s) - 1.0)


def catchup_price_check(side: str, entry: Any, sl: Any, tp: Any, current: Any,
                        max_frac: float = DEFAULT_MAX_DRIFT_STOP_FRAC) -> tuple[bool, str]:
    """``(ok, reason)`` for entering a caught-up signal at ``current`` (bound 2)."""
    try:
        entry_f, sl_f, cur = float(entry), float(sl), float(current)
    except (TypeError, ValueError):
        return False, "current_price_unreadable"
    stop = abs(entry_f - sl_f)
    if not stop > 0:
        return False, "stop_distance_unreadable"
    long_ = str(side).lower() in ("buy", "long")
    tp_f: Optional[float]
    try:
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        tp_f = None
    if (long_ and cur <= sl_f) or (not long_ and cur >= sl_f):
        return False, f"price {cur} already through SL {sl_f}"
    if tp_f is not None and ((long_ and cur >= tp_f) or (not long_ and cur <= tp_f)):
        return False, f"price {cur} already through TP {tp_f}"
    drift = abs(cur - entry_f)
    if drift > max_frac * stop:
        return False, (f"price {cur} drifted {drift:.6g} from entry {entry_f} "
                       f"> {max_frac:g} x stop {stop:.6g}")
    return True, f"price {cur} within {max_frac:g} x stop of entry {entry_f}"


# ----------------------------------------------------- as-of (forming legs)

_ASOF: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "decision_bar_asof", default=None)


@contextmanager
def evaluate_as_of(asof_s: float) -> Iterator[dict]:
    """Inside this block ``market_data.fetch_candles`` returns frames cut to the
    bars that OPENED before ``asof_s`` (fetched uncached), and records the price
    it cut off as the current price per ``(SYMBOL, tf_seconds)`` in the yielded
    dict's ``"current"`` map. Used to decide a forming leg's just-closed bar."""
    holder: dict = {"asof": float(asof_s), "current": {}}
    token = _ASOF.set(holder)
    try:
        yield holder
    finally:
        _ASOF.reset(token)


def current_asof() -> Optional[dict]:
    return _ASOF.get()


def trim_to_asof(frame: Any, symbol: str, timeframe: str, holder: dict) -> Any:
    """Drop rows that opened at/after ``holder['asof']``; remember the last
    close dropped (the live price) for the drift check."""
    if frame is None:
        return None
    from src.runtime.closed_bars import TF_SECONDS, _epoch_seconds
    try:
        opens = [_epoch_seconds(v) for v in frame["timestamp"].tolist()]
    except Exception:  # noqa: BLE001
        return frame
    asof = holder["asof"]
    keep = [o is not None and o < asof for o in opens]
    if all(keep):
        return frame
    n_keep = keep.index(False) if False in keep else len(keep)
    tf_s = TF_SECONDS.get(str(timeframe))
    if tf_s:
        try:
            holder["current"][(str(symbol).upper().replace("/", ""), tf_s)] = float(
                frame["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            pass
    return frame.iloc[:n_keep]


__all__ = [
    "CATCHUP_BAR_FRACTION", "DEFAULT_MAX_DRIFT_STOP_FRAC", "LEDGER_NAME", "PID",
    "PROCESS_STARTED", "by_previous_process", "catchup_bound_seconds",
    "catchup_price_check", "current_asof", "evaluate_as_of", "get",
    "ledger_path", "reset_for_tests", "trim_to_asof", "update", "update_many",
]
