"""Closed → exchange-flat invariant reconciler (S-067 follow-up #3).

**Alert-only, BASELINE (2026-06-17).** The invariant CHECK runs
unconditionally on every monitor tick — it was previously gated
default-OFF by ``CLOSED_FLAT_INVARIANT_ENABLED`` "until the operator
opts in", a safety invariant behind a default-off flag (the
Prime-Directive anti-pattern; removed alongside the same-class
``NAKED_POSITION_AUTOPROTECT`` / ``MONITOR_RECONCILE_ENABLED`` gates).
It only LOGS + ALERTS a violation; **auto-flatten remains a separate,
deliberately-unbuilt step** (the orphan reconciler is the safety net).

See ``docs/claude/closed-flat-invariant.md`` for the full design
memo, including rollout plan and trade-#1049 retrospective.

## Contract

For every ``trade_journal.db::trades`` row that flipped to
``status='closed'`` within the last ``window_seconds``, query the
exchange's open-position list for the matching account. If the
exchange still shows a non-zero position on the same symbol +
side, that's a contract violation:

* The DB says the trade is closed.
* The exchange says the position is still open.

Phase-1 response: log to
``runtime_logs/invariant_violations.jsonl`` and surface a
Telegram alert via ``outcomes.report``. Do NOT flatten the
position — the existing orphan-position reconciler is the
eventual safety net during phase-1 soak.

## What this module could NOT do until 2026-09-09 (audit F-11/F-12/F-13)

It is the ONE mechanism that can independently contradict *"this trade is
closed"*, and four separate things stopped it saying anything. All four are
fixed here; none of them touches an order path.

1. **It could not tell a failed read from a flat book.** Every exchange-read
   failure returned ``0.0``, the value that means *flat*, so the invariant was
   cleared by exactly the condition it exists to catch. The read now returns a
   three-state :class:`ResidualRead` — ``flat`` / ``residual`` /
   ``could_not_look`` — registered with ``collapsed-state-guard`` as
   ``closed_flat.residual_state``.
2. **It could not see a residual AT ALL.** The size was read from ``qty`` /
   ``contracts``; ``clients.py::account_open_positions`` emits ``size`` on
   4 of 4 venue branches. See ``_SIZE_KEYS``.
3. **It examined less of the timeline than it skipped.** A fixed 60 s lookback
   against a measured invocation period of ≥ 101.9 s left ≥ 41 % of every
   period examined by nobody. The caller now DERIVES the window from its own
   measured cadence and publishes it on the ``closed_flat_coverage`` soak.
4. **Nobody could read what it said.** ``invariant_violations.jsonl`` was on no
   diag allowlist and the alert was ``Level.WARN``, which ``outcomes`` excludes
   from Telegram. Both fixed; see :func:`_default_alerter`.

⚠️ **Its 14.7-day record of zero violations was therefore evidence of
nothing, and the first violation it reports is NOT a regression** — it is this
mechanism starting to work.

## Never-raise

This module follows the same never-raise contract as
``runtime_status.write_status``: a malformed account, a Bybit API
outage, or a corrupt JSONL append must NOT propagate up the tick
loop and crash the trader. Failures are caught + logged + skipped.
The orphan reconciler is the eventual safety net.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_DEFAULT_VIOLATIONS_LOG = runtime_logs_dir() / "invariant_violations.jsonl"
#: Declared as a constant so `test_every_declared_soak_log_has_a_read_surface`
#: DERIVES this soak's read-surface requirement rather than relying on whoever
#: adds it remembering to allowlist it — the recurrence detector for
#: BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE, whose
#: fourth instance is why a derived probe replaced an enumerated one. F-13 is
#: that exact class, so this soak opts INTO the detector on the commit that
#: creates it.
SOAK_LOG_NAME = "closed_flat_coverage.jsonl"

_DEFAULT_COVERAGE_LOG = runtime_logs_dir() / SOAK_LOG_NAME

# Anything <= this absolute residual qty is treated as exchange-flat.
# Bybit occasionally returns dust like 1e-9 on a fully-flattened
# position; the existing orphan reconciler uses the same threshold.
_DUST_THRESHOLD = 1e-8

DEFAULT_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class InvariantViolation:
    """One closed→exchange-flat violation."""

    detected_at: str       # ISO-8601 UTC
    trade_id: int
    account_id: str
    symbol: str
    db_status: str         # always 'closed' in phase-1
    exchange_qty: float    # signed; >0 means residual long, <0 short
    phase: str = "alert_only"


# ---------------------------------------------------------------------------
# Side normalisation (mirror of order_monitor._exchange_position_set)
# ---------------------------------------------------------------------------

_DB_SIDE_TO_CANONICAL = {
    "long": "long", "buy": "long",
    "short": "short", "sell": "short",
}
_EXCHANGE_SIDE_TO_CANONICAL = {
    "buy": "long", "long": "long",
    "sell": "short", "short": "short",
}


def _canonical_db_side(direction: Any) -> Optional[str]:
    if not isinstance(direction, str):
        return None
    return _DB_SIDE_TO_CANONICAL.get(direction.strip().lower())


def _canonical_exchange_side(side: Any) -> Optional[str]:
    if not isinstance(side, str):
        return None
    return _EXCHANGE_SIDE_TO_CANONICAL.get(side.strip().lower())


# ---------------------------------------------------------------------------
# Recently-closed query
# ---------------------------------------------------------------------------


def _fetch_recently_closed(
    db, *, cutoff_iso: str,
) -> List[dict[str, Any]]:
    """Query trades that flipped to status='closed' since *cutoff_iso*.

    Accepts a Database wrapper (with ``connect()``), a sqlite3
    Connection (passed through), or a path-like (str / Path).

    Uses ``COALESCE(op.updated_at, notes::closed_at, created_at)``
    precedence — same shape as the trades_closed router's closed-at
    fallback chain. Pre-S-030 trade rows didn't have an explicit
    close timestamp; the reconciler-close path stuffs it into the
    ``notes`` JSON.

    Returns a list of dicts: ``id``, ``account_id``, ``symbol``,
    ``direction``.
    """
    if isinstance(db, sqlite3.Connection):
        conn = db
        owned = False
    elif hasattr(db, "connect"):
        conn = db.connect()
        owned = True
    elif isinstance(db, (str, os.PathLike)):
        # S-CFI-FIX: only path-likes fall through to sqlite3.connect.
        # Anything else used to land here too, and sqlite3 happily
        # opened a database file at the object's repr() — leaving
        # zero-byte files like "<sqlite3.Connection object at 0x...>"
        # at whatever cwd the caller had. PR #658 leaked nine of
        # those into the repo root.
        conn = sqlite3.connect(os.fspath(db))
        owned = True
    else:
        raise TypeError(
            "closed_flat_invariant._fetch_recently_closed: db must be a "
            "sqlite3.Connection, a Database wrapper with .connect(), or "
            f"a path-like (str / os.PathLike); got {type(db).__name__}"
        )
    try:
        conn.row_factory = sqlite3.Row
        # Subquery handles the closed-at fallback. The notes JSON
        # extraction is sqlite3's json_extract — present on every
        # CPython 3.11 stdlib build.
        #
        # The ``json_valid(t.notes)`` guard is load-bearing: bare
        # ``json_extract`` over the whole table raises ``malformed JSON``
        # and aborts the ENTIRE query the moment a *single* closed row has
        # a non-JSON ``notes`` blob — which silently disabled this safety
        # invariant every tick (a truncated ``json.dumps(...)[:N]`` writer
        # produced one such row; see BL-20260619 / the order_monitor
        # truncation fix). Guarding per-row degrades a bad ``notes`` to
        # NULL → the COALESCE falls through to ``t.created_at`` for that
        # row only, instead of failing the check for all rows.
        cur = conn.execute(
            """
            SELECT t.id, t.account_id, t.symbol, t.direction
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
              AND datetime(
                    COALESCE(
                        op.updated_at,
                        CASE WHEN json_valid(t.notes)
                             THEN json_extract(t.notes, '$.closed_at')
                        END,
                        t.created_at
                    )
                  ) >= datetime(?)
            """,
            (cutoff_iso,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# Residual read — THREE STATES, NEVER COLLAPSED (F-11, 2026-09-09)
# ---------------------------------------------------------------------------
#
# Registered with ``collapsed-state-guard`` as ``closed_flat.residual_state``.
#
# Until 2026-09-09 the exchange read returned a bare ``float`` and every
# failure path returned ``0.0`` — the same value that means *genuinely flat*.
# The caller reads ``residual == 0.0`` as *no violation*, so THE ONE MECHANISM
# THAT CAN INDEPENDENTLY CONTRADICT "this trade is closed" was cleared by
# exactly the condition it exists to catch: an exchange it could not read.
# Measured population: 5 of 5 early-return sites in this module's exchange-read
# path collapsed (``account`` unresolvable; ``account_open_positions`` raised;
# ``fetcher()`` raised; ``positions is None``; ``positions == []``).
#
# Its input is ``clients.py::account_open_positions``, whose own docstring
# requires the opposite: "``None`` on any failure path so callers can
# distinguish 'no positions' (``[]``) from 'could not read' (``None``)" —
# INCLUDING an empty IB snapshot from a Gateway that is not verified logged-in.
# The convention is honoured elsewhere (positive control:
# ``hourly_report.py`` — ``len(positions) if isinstance(positions, list)
# else None``); this module was the consumer that dropped it.

RESIDUAL_STATE_FLAT = "flat"
#: We looked, and the venue holds no residual position on this symbol+side.
RESIDUAL_STATE_RESIDUAL = "residual"
#: We looked, and the venue still holds a position — the invariant violation.
RESIDUAL_STATE_COULD_NOT_LOOK = "could_not_look"
#: WE DID NOT LOOK. Never a synonym for flat, and never clears the invariant.

RESIDUAL_STATES = (
    RESIDUAL_STATE_FLAT,
    RESIDUAL_STATE_RESIDUAL,
    RESIDUAL_STATE_COULD_NOT_LOOK,
)

#: Position-dict keys carrying the size, in precedence order.
#:
#: ⚠️ ``size`` IS FIRST AND IS THE ONLY ONE PRODUCTION EVER EMITS. This module
#: shipped reading ``qty``/``contracts`` only, and
#: ``clients.py::account_open_positions`` normalises EVERY venue branch
#: (bybit / interactive_brokers / alpaca / oanda) to
#: ``{symbol, side, size, entry_price, unrealised_pnl}`` —
#: ``IBClient.positions``'s own docstring pins that shape. So a live residual
#: read ``float(None or None or 0) == 0.0`` and graded FLAT, on every account,
#: from the module's first commit: the invariant could not report a violation
#: even with a perfect read, a perfect window and a perfect read surface.
#: The other two keys are kept as a fallback so a hypothetical caller passing a
#: raw venue payload still grades, never as a statement that anything sends them.
_SIZE_KEYS = ("size", "qty", "contracts")


@dataclass(frozen=True)
class ResidualRead:
    """The outcome of one exchange residual read.

    ``qty`` is meaningful ONLY when ``residual_state == "residual"`` (signed:
    +long / -short). It is ``0.0`` for ``flat`` AND for ``could_not_look`` —
    which is precisely why the state, not the number, is what callers branch on.
    """

    residual_state: str
    qty: float = 0.0
    reason: str = ""


def _size_of(p: dict) -> Optional[float]:
    """Return the position row's size, or ``None`` if no key carried one."""
    for key in _SIZE_KEYS:
        if key not in p:
            continue
        raw = p.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _fetch_exchange_residual(
    account_resolver: Callable[[str], Any],
    account_id: str,
    symbol: str,
    canonical_side: str,
) -> ResidualRead:
    """Read the residual for *symbol* on *account_id*.

    Never raises. A failure grades ``could_not_look`` — NOT ``flat``.
    """
    account = account_resolver(account_id)
    if account is None:
        logger.warning(
            "closed_flat_invariant: account %s not resolvable — grading "
            "could_not_look (NOT flat)", account_id,
        )
        return ResidualRead(RESIDUAL_STATE_COULD_NOT_LOOK,
                            reason="account_unresolvable")
    fetcher = getattr(account, "open_positions", None)
    if fetcher is None:
        # Some account-builder shapes use ``account_open_positions(acc)``
        # instead. Try that as a fallback before giving up.
        try:
            from src.units.accounts.clients import account_open_positions
            positions = account_open_positions(account)
        except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract; the read grades could_not_look, which does NOT clear the invariant.
            logger.warning(
                "closed_flat_invariant: account_open_positions(%s) failed: %s "
                "— grading could_not_look (NOT flat)", account_id, exc,
            )
            return ResidualRead(RESIDUAL_STATE_COULD_NOT_LOOK,
                                reason="account_open_positions_raised")
    else:
        try:
            positions = fetcher()
        except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract; the read grades could_not_look, which does NOT clear the invariant.
            logger.warning(
                "closed_flat_invariant: %s.open_positions() failed: %s "
                "— grading could_not_look (NOT flat)", account_id, exc,
            )
            return ResidualRead(RESIDUAL_STATE_COULD_NOT_LOOK,
                                reason="open_positions_raised")
    return _residual_from_positions(positions, symbol, canonical_side)


def _residual_from_positions(
    positions: Optional[Iterable[Any]],
    symbol: str,
    canonical_side: str,
) -> ResidualRead:
    """Grade the position list for *symbol* + *canonical_side*.

    ``None`` in ⇒ ``could_not_look``. An empty LIST ⇒ ``flat``. Those are two
    different facts and the whole point of this function's return type.
    """
    if positions is None:
        return ResidualRead(RESIDUAL_STATE_COULD_NOT_LOOK,
                            reason="read_returned_none")
    if not isinstance(positions, (list, tuple)):
        # A non-list, non-None answer is not a snapshot we can grade. Refusing
        # is the same call `account_open_positions` makes on a non-dict account.
        return ResidualRead(RESIDUAL_STATE_COULD_NOT_LOOK,
                            reason="unreadable_positions_type")
    total = 0.0
    for p in positions:
        if not isinstance(p, dict):
            continue
        if p.get("symbol") != symbol:
            continue
        if _canonical_exchange_side(p.get("side")) != canonical_side:
            continue
        size = _size_of(p)
        if size is None:
            continue
        total += size
    if abs(total) <= _DUST_THRESHOLD:
        return ResidualRead(RESIDUAL_STATE_FLAT)
    signed = -abs(total) if canonical_side == "short" else abs(total)
    return ResidualRead(RESIDUAL_STATE_RESIDUAL, qty=signed)


# ---------------------------------------------------------------------------
# Planted controls (F-13 follow-through)
# ---------------------------------------------------------------------------


def run_residual_controls() -> bool:
    """Grade three PLANTED inputs through the deployed classifier.

    ⚠️ **SCOPE — read this before quoting ``controls_ok``.** This exercises the
    PURE classifier inside the running process. It proves the deployed code
    still tells a residual from a flat book from an unreadable one. It does
    **NOT** reach a venue, an account resolver, a DB row or the alert path, so
    ``controls_ok: true`` is NOT evidence the invariant works end to end and
    must never be reported as such. Its value is the narrower one the audit
    asked for: a zero-violation soak row that carries no control at all cannot
    be told apart from a classifier that has stopped classifying.
    """
    try:
        planted = _residual_from_positions(
            [{"symbol": "CTRLUSDT", "side": "Buy", "size": 1.0}],
            "CTRLUSDT", "long",
        )
        flat = _residual_from_positions([], "CTRLUSDT", "long")
        blind = _residual_from_positions(None, "CTRLUSDT", "long")
    except Exception:  # noqa: BLE001  # allow-silent: a control that raises is a FAILED control, never an absent one.
        logger.exception("closed_flat_invariant: residual controls raised")
        return False
    return (
        planted.residual_state == RESIDUAL_STATE_RESIDUAL
        and planted.qty == 1.0
        and flat.residual_state == RESIDUAL_STATE_FLAT
        and blind.residual_state == RESIDUAL_STATE_COULD_NOT_LOOK
    )


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnreadableRead:
    """One recently-closed trade whose exchange read could not be graded."""

    trade_id: int
    account_id: str
    symbol: str
    reason: str


@dataclass(frozen=True)
class ClosedFlatCheckResult:
    """What one invariant pass actually established.

    ``checked=False`` means the trades query itself failed — *we did not look
    at all*, which is a third thing from "looked and found nothing".
    """

    violations: List[InvariantViolation]
    unreadable: List[UnreadableRead]
    #: One count per declared residual state. ALL THREE KEYS ARE ALWAYS
    #: PRESENT, including when the count is zero — an absent key would let a
    #: consumer's ``.get(state, 0)`` read "we never graded this state" and
    #: "we graded it zero times" as the same thing, which is the collapse this
    #: contract exists to prevent, one level up in the reporting.
    state_counts: Dict[str, int] = field(
        default_factory=lambda: {s: 0 for s in RESIDUAL_STATES})
    examined: int = 0
    window_seconds: float = float(DEFAULT_WINDOW_SECONDS)
    cadence_basis: str = "caller_supplied"
    controls_ok: Optional[bool] = None
    checked: bool = True


def check(
    db,
    account_resolver: Callable[[str], Any],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now: Optional[datetime] = None,
    violations_log: Optional[Path] = None,
    alerter: Optional[Callable[[str, str, dict], None]] = None,
) -> List[InvariantViolation]:
    """Back-compat wrapper: the violations list only.

    ⚠️ **Production must call :func:`check_detailed`.** This shape cannot
    express ``could_not_look`` — an empty list here means "no violations
    FOUND", never "no violations EXIST" — and it writes no coverage soak row.
    Kept because it is the published signature and several tests use it.
    """
    return check_detailed(
        db, account_resolver,
        window_seconds=window_seconds, now=now,
        violations_log=violations_log, alerter=alerter,
        coverage_log=False,
    ).violations


def check_detailed(
    db,
    account_resolver: Callable[[str], Any],
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    cadence_basis: str = "caller_supplied",
    now: Optional[datetime] = None,
    violations_log: Optional[Path] = None,
    alerter: Optional[Callable[[str, str, dict], None]] = None,
    coverage_log: Any = None,
) -> ClosedFlatCheckResult:
    """Run the invariant check and report everything the pass established.

    Per violation: append a structured row to *violations_log* and call
    *alerter* (default ``outcomes.report`` at ``Level.ERROR``, which
    ``outcomes`` forwards to Telegram).

    An unreadable exchange read is recorded and **does not clear the
    invariant**; it is deliberately NOT paged, because a down IB Gateway would
    otherwise produce one page per closed trade per tick — the alarm-fatigue
    P1. It surfaces on the ``closed_flat_coverage`` soak and in the caller's
    tick summary instead.

    *coverage_log* — a path to write the soak row to, ``None`` for the default
    path, or ``False`` to write no row at all.

    Never raises.
    """
    result = _check_inner(
        db, account_resolver,
        window_seconds=window_seconds, cadence_basis=cadence_basis,
        now=now, violations_log=violations_log, alerter=alerter,
    )
    if coverage_log is not False:
        _emit_coverage(result, coverage_log, now)
    return result


def _check_inner(
    db,
    account_resolver: Callable[[str], Any],
    *,
    window_seconds: float,
    cadence_basis: str,
    now: Optional[datetime],
    violations_log: Optional[Path],
    alerter: Optional[Callable[[str, str, dict], None]],
) -> ClosedFlatCheckResult:
    controls_ok = run_residual_controls()
    try:
        now_dt = now or datetime.now(timezone.utc)
        window = max(0.0, float(window_seconds))
        cutoff = (now_dt - timedelta(seconds=window)).isoformat()
        try:
            rows = _fetch_recently_closed(db, cutoff_iso=cutoff)
        except sqlite3.Error as exc:
            logger.warning(
                "closed_flat_invariant: trades query failed: %s", exc,
            )
            return ClosedFlatCheckResult(
                [], [], window_seconds=window, cadence_basis=cadence_basis,
                controls_ok=controls_ok, checked=False,
            )
        violations: List[InvariantViolation] = []
        unreadable: List[UnreadableRead] = []
        counts: Dict[str, int] = {s: 0 for s in RESIDUAL_STATES}
        examined = 0
        for row in rows:
            canonical = _canonical_db_side(row.get("direction"))
            if canonical is None:
                continue
            account_id = row.get("account_id") or ""
            symbol = row.get("symbol") or ""
            if not account_id or not symbol:
                continue
            examined += 1
            read = _fetch_exchange_residual(
                account_resolver, account_id, symbol, canonical,
            )
            counts[read.residual_state] = counts.get(read.residual_state, 0) + 1
            if read.residual_state == RESIDUAL_STATE_COULD_NOT_LOOK:
                unreadable.append(UnreadableRead(
                    trade_id=int(row["id"]), account_id=account_id,
                    symbol=symbol, reason=read.reason,
                ))
                continue
            if read.residual_state == RESIDUAL_STATE_FLAT:
                continue
            violations.append(InvariantViolation(
                detected_at=now_dt.isoformat(),
                trade_id=int(row["id"]),
                account_id=account_id,
                symbol=symbol,
                db_status="closed",
                exchange_qty=float(read.qty),
            ))
        if violations:
            _emit_violations(violations, violations_log, alerter)
        return ClosedFlatCheckResult(
            violations, unreadable, state_counts=counts, examined=examined,
            window_seconds=window, cadence_basis=cadence_basis,
            controls_ok=controls_ok, checked=True,
        )
    except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract — must not crash the tick loop; checked=False records that we did not look.
        logger.exception(
            "closed_flat_invariant: check() failed (suppressed): %s", exc,
        )
        return ClosedFlatCheckResult(
            [], [], window_seconds=float(window_seconds),
            cadence_basis=cadence_basis, controls_ok=controls_ok,
            checked=False,
        )


def _emit_coverage(
    result: ClosedFlatCheckResult,
    coverage_log: Any,
    now: Optional[datetime],
) -> None:
    """Append one ``closed_flat_coverage`` soak row.

    This is the surface that makes a zero INTERPRETABLE: it carries the window
    actually used, how that window was derived, how many rows were examined,
    how many reads could not be graded, and whether the planted controls held.
    Readable at ``/api/diag/log_file?name=closed_flat_coverage``.
    """
    path = _DEFAULT_COVERAGE_LOG if coverage_log is None else Path(coverage_log)
    row = {
        "observed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "checked": result.checked,
        "window_seconds": round(float(result.window_seconds), 3),
        "cadence_basis": result.cadence_basis,
        "examined": result.examined,
        # Keyed on the residual-state vocabulary itself, so a reader of the
        # soak sees the same three words the code branches on.
        "state_counts": dict(result.state_counts),
        "could_not_look_reasons": sorted({u.reason for u in result.unreadable}),
        "controls_ok": result.controls_ok,
        "controls_scope": "pure_classifier_only",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        logger.warning(
            "closed_flat_invariant: coverage_log write failed: %s", exc,
        )


def _emit_violations(
    violations: List[InvariantViolation],
    violations_log: Optional[Path],
    alerter: Optional[Callable[[str, str, dict], None]],
) -> None:
    log_path = violations_log or _DEFAULT_VIOLATIONS_LOG
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            for v in violations:
                fh.write(json.dumps(asdict(v)) + "\n")
    except OSError as exc:
        logger.warning(
            "closed_flat_invariant: violations_log write failed: %s", exc,
        )

    fn = alerter or _default_alerter()
    if fn is None:
        return
    for v in violations:
        try:
            fn(
                "closed_flat_invariant",
                f"trade #{v.trade_id} closed in DB but {v.symbol} has "
                f"{v.exchange_qty:+g} open on {v.account_id}",
                asdict(v),
            )
        except Exception as exc:  # noqa: BLE001  # allow-silent: alerter must never crash the tick loop; violations_log is the durable record.
            logger.warning(
                "closed_flat_invariant: alerter failed: %s", exc,
            )


def _default_alerter() -> Optional[Callable[[str, str, dict], None]]:
    """Return the production alerter if available, else None.

    ⚠️ **Level.ERROR, not Level.WARN (F-13, 2026-09-09).** ``outcomes.py``
    restricts Telegram to ``{ERROR, CRITICAL}``, so this alert was emitted at a
    level that reached the operator on no surface at all. ERROR rather than
    CRITICAL is deliberate: both reach Telegram, and this repo reserves
    CRITICAL for a position that is UNPROTECTED or REVERSED. A DB-closed row
    whose venue position is still open is money-at-risk and alert-only — the
    orphan reconciler remains the safety net, and nothing here touches an
    order path.
    """
    try:
        from src.runtime.outcomes import Level, report

        def _alerter(channel: str, summary: str, payload: dict) -> None:
            report(channel, level=Level.ERROR, reason=summary, **payload)

        return _alerter
    except Exception as exc:  # noqa: BLE001  # allow-silent: outcomes import is best-effort; violations still land in the JSONL log.
        logger.debug(
            "closed_flat_invariant: outcomes.report unavailable: %s", exc,
        )
        return None
