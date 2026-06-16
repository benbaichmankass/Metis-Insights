"""Mobile push notification subsystem (M12 S1).

Pushes structured event notifications to registered Android devices via
**Firebase Cloud Messaging HTTP v1**. The notifier is a *side observer*
of existing event sinks (trade closes, signals, watchdog alerts) — it
never sits on the order-execution path, never raises into callers, and
is best-effort and unconditional (no enable flag; inert when unconfigured).

Trust contract (mirrors the dashboard's read-only consumer pattern):

- Observer-only: subscribes to existing event sinks; does not introduce
  new sinks the trader depends on.
- Best-effort: every publish is wrapped + logged; FCM 5xx, 4xx, network
  errors, OAuth2 failures, and missing credentials all silently degrade
  to "no notification sent" + a WARNING log line.
- Unconditional: ``publish_event`` always attempts (best-effort). There is
  no enable/disable flag — when FCM isn't configured it is simply inert.
- Reductive: notifications can only *inform* the operator's phone; they
  cannot influence trader behavior. The notifier holds no decision
  authority and surfaces no inputs that re-enter the order pipeline.

Public surface:

- ``publish_event(kind, payload)`` — module-level convenience that
  resolves a process-wide ``FcmNotifier`` singleton and fans out to
  every registered device token whose subscription set includes
  ``kind``.

The singleton is constructed lazily on the first ``publish_event`` call
so module import is free even when the feature flag is off. Re-imports
do not rebuild it.

Configuration (environment):

There is **no enable/disable flag** — mobile push always runs (best-effort).
When FCM credentials aren't configured it is simply inert (one WARNING, no
sends). The prior ``MOBILE_PUSH_ENABLED`` default-off enable-gate was removed
2026-06-16: it was a silent footgun (push quietly off until someone remembered
to flip it), and per the Prime Directive a wanted capability gets no default-off
gate. Configuring FCM is the only thing that makes push *do* anything.

- ``FCM_SERVICE_ACCOUNT_JSON`` — the entire service-account JSON blob
  (downloaded from Firebase Console → Project Settings → Service
  accounts). Holds private_key + client_email; required for OAuth2.
- ``FCM_PROJECT_ID`` — Firebase project_id, e.g.
  ``ict-trader-mobile-app``. Falls back to the ``project_id`` field
  inside the service-account JSON when unset.

Sprint plan: ``docs/sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md`` § S1.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from src.runtime.mobile_push.notifier import FcmNotifier

logger = logging.getLogger(__name__)

_singleton_lock = threading.Lock()
_singleton: FcmNotifier | None = None


def _get_notifier() -> FcmNotifier | None:
    # No enable/disable gate — mobile push always builds its notifier and
    # always attempts (best-effort). When FCM isn't configured, from_env()
    # yields an inert notifier whose publish is a no-op (one WARNING).
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            try:
                _singleton = FcmNotifier.from_env()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mobile_push: failed to build FcmNotifier from env "
                    "(notifier will be inert): %s",
                    exc,
                )
                _singleton = FcmNotifier.inert()
        return _singleton


def publish_event(kind: str, payload: dict[str, Any]) -> None:
    """Fan out a structured event notification to every subscribed device.

    Best-effort. Any failure path (credentials missing, DB unavailable,
    FCM 5xx, network timeout) logs a WARNING and returns cleanly — this function never raises into the caller. That is the
    load-bearing invariant: the trader must never crash because a phone
    notification failed.

    Args:
        kind: Event kind, e.g. ``"trade_closed"``. Used both for routing
            (devices subscribe per-kind) and for the FCM data payload so
            the Android side can deep-link.
        payload: Event-specific dict. Will be JSON-serialized as the FCM
            data message; primitives only (no datetime / Decimal — the
            caller is responsible for stringification).
    """
    try:
        notifier = _get_notifier()
        if notifier is None:
            return
        notifier.publish_to_subscribers(kind=kind, payload=payload)
    except Exception as exc:  # noqa: BLE001 — must never raise to caller
        logger.warning("mobile_push.publish_event failed: %s", exc)


def reset_singleton_for_testing() -> None:
    """Drop the process-wide notifier so the next publish rebuilds it.

    For test code only. Never call in production code paths.
    """
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = ["publish_event", "reset_singleton_for_testing"]
