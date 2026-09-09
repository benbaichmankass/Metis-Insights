"""Wiring helper for the closed → exchange-flat invariant tick hook
(S-067 follow-up #3 Phase-2).

Phase-1 (PR #658) shipped the ``closed_flat_invariant`` module +
design memo + tests but no tick-loop wiring. This module is the
wiring — kept in its own file so the live-order-path edit to
``src/runtime/order_monitor.py`` is a single import + call site
(3 lines) rather than a full-file rewrite.

Usage from ``order_monitor.run_monitor_tick``, **after** the
orphan reconcilers and **before** ``return summaries``::

    from src.runtime._closed_flat_wiring import maybe_run_closed_flat_check
    maybe_run_closed_flat_check(db, summaries)

The helper:

* Runs **unconditionally** (BASELINE since 2026-06-17). The check was
  previously gated default-OFF behind ``CLOSED_FLAT_INVARIANT_ENABLED``;
  that flag was retired (a safety invariant behind a default-off flag is
  the Prime-Directive anti-pattern) and is no longer read. The check is
  alert-only — it never mutates a position; the orphan reconciler is the
  safety net.
* Builds an account resolver from
  ``order_monitor._load_account_cfgs_for_reconcile`` and calls
  ``closed_flat_invariant.check`` with it. Violations are logged to
  ``runtime_logs/invariant_violations.jsonl`` and Telegram'd via
  ``outcomes.report`` (the canonical alert path).
* Never raises — the existing orphan reconciler is the eventual
  safety net during the soak window.

**This module is the invariant's cadence source and its
``could_not_look`` consumer** (audit F-11 + F-12, 2026-09-09):

* It MEASURES the interval between its own consecutive invocations and
  derives the lookback from it, so consecutive checks TILE the timeline
  instead of leaving a gap. The old fixed 60 s constant was shorter than
  the measured invocation period (≥ 101.9 s), so ≥ 41 % of every period
  was examined by nobody — by arithmetic, not by failure.
  ⚠️ The interval is MEASURED, never derived from a declared cadence:
  ``exit_loop_health`` records that a ``max(interval, pass)`` derivation
  is blind to a stall between the sleep and the next completion.
* It BRANCHES on ``could_not_look``: a tick in which the exchange could not
  be read writes a summary entry even when zero violations were found,
  because "we found nothing" and "we could not look" are different facts
  and only one of them clears the invariant.

See:
* ``docs/claude/closed-flat-invariant.md`` — design memo + rollout
  plan + trade-#1049 retrospective.
* ``docs/claude/closed-flat-invariant-phase2-wiring.md`` — the
  3-line ``order_monitor.py`` patch to apply (this PR).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Multiplier applied to the MEASURED invocation interval. 1.5 is a margin, not
#: a guess at the period: it absorbs a tick that runs longer than the last one
#: without re-examining much. The overlap it buys is bounded — a closed trade
#: is examined on at most ~2 consecutive passes — so it cannot turn a standing
#: violation into an alarm flood (the desensitised-alarm P1).
_CADENCE_MARGIN = 1.5

#: Window used on the FIRST invocation in a process, where no interval has been
#: measured yet. Deliberately NOT ``DEFAULT_WINDOW_SECONDS``: the trader
#: restarts on every merge to ``main``, so falling back to 60 s would reproduce
#: the exact defect on the first pass of every process — and a restart is
#: precisely when a close is most likely to have been missed. 300 s covers
#: ~2.4 of the audit's measured 101.9 s minimum period.
_BOOTSTRAP_WINDOW_SECONDS = 300.0

#: Monotonic timestamp of the previous invocation, or ``None`` before the
#: first. Per-process by construction — see ``_BOOTSTRAP_WINDOW_SECONDS``.
_last_invocation_monotonic: Optional[float] = None


def derive_window_seconds(
    since_last_seconds: Optional[float],
) -> Tuple[float, str]:
    """Return ``(window_seconds, cadence_basis)`` for one invocation.

    ``cadence_basis`` is ``measured`` when an interval was observed and
    ``bootstrap`` when this is the first pass in the process. Those are two
    different facts about how much to trust the coverage figure, so the soak
    row carries which one applied rather than only the number.
    """
    if since_last_seconds is None or since_last_seconds <= 0:
        return _BOOTSTRAP_WINDOW_SECONDS, "bootstrap"
    from src.runtime.closed_flat_invariant import DEFAULT_WINDOW_SECONDS
    return (
        max(float(DEFAULT_WINDOW_SECONDS),
            float(since_last_seconds) * _CADENCE_MARGIN),
        "measured",
    )


def _next_window() -> Tuple[float, str]:
    """Measure the interval since the previous invocation and derive a window."""
    global _last_invocation_monotonic
    now = time.monotonic()
    prev = _last_invocation_monotonic
    _last_invocation_monotonic = now
    return derive_window_seconds(None if prev is None else now - prev)


def maybe_run_closed_flat_check(
    db: Any,
    summaries: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run the closed → exchange-flat invariant check (baseline, unconditional).

    Parameters
    ----------
    db : Any
        Database wrapper (must support ``connect()``) or a
        ``sqlite3.Connection`` — same shape ``closed_flat_invariant.check``
        accepts.
    summaries : dict, optional
        If provided, the helper writes a
        ``__closed_flat_invariant__`` entry carrying the per-state counts,
        the window actually used and how it was derived, so the caller's
        per-tick summary surfaces what the pass established.

    Returns
    -------
    dict | None
        The summary entry that was written into ``summaries`` (or
        would have been if ``summaries`` was ``None``). ``None`` ONLY when
        the pass ran AND every recently-closed row graded ``flat``.
        ⚠️ A pass with zero violations but a ``could_not_look`` read still
        returns an entry: "we found nothing" and "we could not look" are
        different facts, and only the first is a clean tick. The check itself
        is unconditional — the ``CLOSED_FLAT_INVARIANT_ENABLED`` gate was
        removed 2026-06-17.
    """
    try:
        from src.runtime import closed_flat_invariant
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "closed_flat_wiring: closed_flat_invariant unavailable: %s", exc,
        )
        return None

    # BASELINE (2026-06-17): the closed→exchange-flat invariant check is now
    # unconditional. It was previously gated default-OFF by
    # CLOSED_FLAT_INVARIANT_ENABLED "until the operator opts in" — a safety
    # invariant behind a default-off flag, the Prime-Directive anti-pattern.
    # The check is ALERT-ONLY (logs + Telegrams a DB-closed-but-exchange-open
    # violation; never mutates a position — the orphan reconciler is the
    # safety net), so making it always-run is low risk and the compliant
    # baseline. Auto-flatten remains a separate, deliberately-unbuilt step.

    try:
        # Lazy-import the cfg loader so unit tests can monkeypatch
        # _load_account_cfgs_for_reconcile in order_monitor without
        # importing the full module here.
        from src.runtime.order_monitor import _load_account_cfgs_for_reconcile
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "closed_flat_wiring: account-cfg loader unavailable: %s", exc,
        )
        return None

    try:
        cfg_map = _load_account_cfgs_for_reconcile()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "closed_flat_wiring: cfg load raised: %s", exc,
        )
        return None

    def _resolver(account_id: str) -> Optional[Dict[str, Any]]:
        return cfg_map.get(account_id)

    window_seconds, cadence_basis = _next_window()

    try:
        result = closed_flat_invariant.check_detailed(
            db, account_resolver=_resolver,
            window_seconds=window_seconds,
            cadence_basis=cadence_basis,
        )
    except Exception as exc:  # noqa: BLE001
        # closed_flat_invariant.check_detailed has its own never-raise
        # wrapper, so reaching this branch means something genuinely
        # unexpected happened (e.g. import-time crash). Log + swallow.
        logger.warning(
            "closed_flat_wiring: check_detailed() raised (suppressed): %s", exc,
        )
        return None

    try:
        return _summarise(result, summaries)
    except Exception as exc:  # noqa: BLE001
        # NEVER-RAISE. This runs on the live monitor tick, and a summary the
        # caller cannot build is not worth crashing the tick loop for — the
        # violations log and the coverage soak are already written by then.
        logger.warning(
            "closed_flat_wiring: summarising the result raised (suppressed): "
            "%s", exc,
        )
        return None


def _summarise(
    result: Any,
    summaries: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Turn one check result into the caller's per-tick summary entry."""
    from src.runtime import closed_flat_invariant

    counts = result.state_counts
    n_flat = counts.get(closed_flat_invariant.RESIDUAL_STATE_FLAT, 0)
    n_residual = counts.get(closed_flat_invariant.RESIDUAL_STATE_RESIDUAL, 0)
    n_blind = counts.get(closed_flat_invariant.RESIDUAL_STATE_COULD_NOT_LOOK, 0)

    # ⚠️ THE `could_not_look` BRANCH — the reason this contract is registered.
    # A pass that examined trades but could not READ the exchange for some of
    # them has NOT established that those trades are flat. Before 2026-09-09
    # that outcome was indistinguishable from `flat` and the tick read clean.
    #
    # It is deliberately NOT paged: a down IB Gateway would otherwise produce
    # one operator page per recently-closed row per tick, which is the
    # desensitised-alarm P1. It is loud on the surfaces a session reads (this
    # log line, the tick summary, and the `closed_flat_coverage` soak) and
    # silent on the one reserved for money-at-risk.
    if n_blind:
        logger.warning(
            "closed_flat_wiring: %d of %d recently-closed row(s) graded "
            "could_not_look (%s) — the invariant is NOT clear for them, it is "
            "UNREAD; %d graded flat, %d residual",
            n_blind, result.examined,
            ", ".join(sorted({u.reason for u in result.unreadable})) or "unknown",
            n_flat, n_residual,
        )

    if result.checked and not n_residual and not n_blind:
        # Everything examined graded `flat`: a genuinely clean tick, and the
        # ONLY shape that may be reported as one.
        return None

    entry = {
        "violations": n_residual,
        "state_counts": dict(counts),
        "examined": result.examined,
        "checked": result.checked,
        "window_seconds": round(float(result.window_seconds), 3),
        "cadence_basis": result.cadence_basis,
        "controls_ok": result.controls_ok,
        "phase": "alert_only",
    }
    if summaries is not None:
        summaries["__closed_flat_invariant__"] = entry
    return entry
