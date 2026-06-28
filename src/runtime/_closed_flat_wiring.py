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

See:
* ``docs/claude/closed-flat-invariant.md`` — design memo + rollout
  plan + trade-#1049 retrospective.
* ``docs/claude/closed-flat-invariant-phase2-wiring.md`` — the
  3-line ``order_monitor.py`` patch to apply (this PR).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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
        ``__closed_flat_invariant__`` entry on violation
        (``{"violations": N, "phase": "alert_only"}``) so the caller's
        per-tick summary surfaces the count.

    Returns
    -------
    dict | None
        The summary entry that was written into ``summaries`` (or
        would have been if ``summaries`` was ``None``). ``None`` when
        no violations were found (the check itself is unconditional —
        the ``CLOSED_FLAT_INVARIANT_ENABLED`` gate was removed 2026-06-17).
        Used by tests
        that pass ``summaries=None`` and inspect the return value.
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

    try:
        violations = closed_flat_invariant.check(
            db, account_resolver=_resolver,
        )
    except Exception as exc:  # noqa: BLE001
        # closed_flat_invariant.check has its own never-raise wrapper,
        # so reaching this branch means something genuinely unexpected
        # happened (e.g. import-time crash). Log + swallow.
        logger.warning(
            "closed_flat_wiring: check() raised (suppressed): %s", exc,
        )
        return None

    if not violations:
        return None

    entry = {
        "violations": len(violations),
        "phase": "alert_only",
    }
    if summaries is not None:
        summaries["__closed_flat_invariant__"] = entry
    return entry
