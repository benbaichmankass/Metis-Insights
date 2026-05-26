"""Tests for the canonical event-kind taxonomy (M12 S4).

The taxonomy is the contract between three layers:

  - The notifier (`src.runtime.mobile_push.notifier.FcmNotifier`) — filters
    by ``kind`` against ``device_tokens.subscriptions``.
  - The bot-side call sites (`src.runtime.notify`, `src.units.db.database`)
    — must import a constant, never write a raw string.
  - The Android subscription UI — fetches the list via
    ``GET /api/bot/devices/event-kinds`` and renders one toggle per row.

Renaming a kind silently breaks all already-installed apps, so the tests
here pin the kind strings explicitly: a code review on a rename gets a
loud, intentional test failure.
"""
from __future__ import annotations

import importlib
import inspect

from src.runtime.mobile_push import event_kinds


def test_canonical_strings_pinned() -> None:
    """Pin the wire strings so a rename is a loud test failure, not a
    silent break of all installed apps."""
    assert event_kinds.TRADE_CLOSED == "trade_closed"
    assert event_kinds.TELEGRAM == "telegram"
    assert event_kinds.SIGNAL_EMITTED == "signal_emitted"
    assert event_kinds.HEALTH_CONCERN == "health_concern"
    assert event_kinds.SERVICE_DOWN == "service_down"
    assert event_kinds.PNL_DIGEST == "pnl_digest"


def test_all_kinds_lists_every_constant_once() -> None:
    """ALL_KINDS is the order-preserving canonical iteration; missing or
    duplicate entries would skew the Android UI order."""
    assert len(event_kinds.ALL_KINDS) == len(set(event_kinds.ALL_KINDS))
    # Every TYPE-uppercase module attr ending in a kind string should be
    # present in ALL_KINDS (catches "added a constant, forgot the registry").
    module_attrs = {
        name: value
        for name, value in inspect.getmembers(event_kinds)
        if name.isupper() and isinstance(value, str) and name != "LABELS"
        and name not in ("ALL_KINDS", "IN_FLIGHT", "DESCRIPTIONS")
    }
    for name, value in module_attrs.items():
        assert value in event_kinds.ALL_KINDS, (
            f"{name}={value} declared but missing from ALL_KINDS"
        )


def test_labels_and_descriptions_cover_every_kind() -> None:
    """LABELS / DESCRIPTIONS keys must match ALL_KINDS — the Android UI
    blows up if it pulls a kind without a label."""
    for k in event_kinds.ALL_KINDS:
        assert k in event_kinds.LABELS, f"missing LABEL for {k}"
        assert k in event_kinds.DESCRIPTIONS, f"missing DESCRIPTION for {k}"
        assert event_kinds.LABELS[k], f"empty LABEL for {k}"
        assert event_kinds.DESCRIPTIONS[k], f"empty DESCRIPTION for {k}"


def test_in_flight_is_subset_of_all_kinds() -> None:
    """IN_FLIGHT can't list a kind ALL_KINDS doesn't know about."""
    assert event_kinds.IN_FLIGHT.issubset(set(event_kinds.ALL_KINDS))


def test_in_flight_includes_real_call_sites() -> None:
    """trade_closed + telegram have live call sites today — they must be
    in IN_FLIGHT or the docstring lies to operators about which toggles
    work."""
    assert event_kinds.TRADE_CLOSED in event_kinds.IN_FLIGHT
    assert event_kinds.TELEGRAM in event_kinds.IN_FLIGHT


def test_is_known_accepts_canonical() -> None:
    for k in event_kinds.ALL_KINDS:
        assert event_kinds.is_known(k)


def test_is_known_rejects_typos() -> None:
    assert not event_kinds.is_known("trade_close")  # typo
    assert not event_kinds.is_known("")
    assert not event_kinds.is_known("TRADE_CLOSED")  # case-sensitive
    assert not event_kinds.is_known("signal")  # singular doesn't match plural


def test_constants_are_strings_not_enum() -> None:
    """Final[str] not enum.Enum — the wire format is plain JSON strings;
    operators copy these into curl payloads and we don't want to force an
    .value lookup on every call site."""
    assert isinstance(event_kinds.TRADE_CLOSED, str)
    assert isinstance(event_kinds.TELEGRAM, str)


def test_module_reloads_cleanly() -> None:
    """No hidden module-level side effects (e.g. DB writes, network calls)
    that would make the module unsafe to import during health-review."""
    importlib.reload(event_kinds)
    # After reload the constants must still hold the same values.
    assert event_kinds.TRADE_CLOSED == "trade_closed"
