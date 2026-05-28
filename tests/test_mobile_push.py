"""Tests for the mobile_push subsystem (M12 S1).

Covers:

- The ``publish_event`` module-level contract: must never raise into
  caller, must respect ``MOBILE_PUSH_ENABLED`` flag, must short-circuit
  to inert when credentials are missing.
- ``FcmNotifier.from_env`` builds an inert notifier on missing /
  malformed env without raising.
- Subscription filter logic (``_truthy_subscription``): default-permissive
  when null / empty / malformed, list-membership semantics, dict toggle
  semantics.
- The notifier swallows HTTP/network failures and does not raise.

We do NOT test the OAuth2 token acquisition end-to-end (that needs
real google-auth + a real service account); the access-token path is
mocked or the notifier is forced inert.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.runtime import mobile_push
from src.runtime.mobile_push.notifier import (
    FcmNotifier,
    _truthy_subscription,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts from a fresh process-wide notifier and a
    clean credential env (so an unrelated env-var set on the dev machine
    doesn't leak into ``from_env`` and flip an inert-expectation test
    into an active one)."""
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON_PATH", raising=False)
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FCM_PROJECT_ID", raising=False)
    mobile_push.reset_singleton_for_testing()
    yield
    mobile_push.reset_singleton_for_testing()


# ---------------------------------------------------------------------------
# publish_event contract
# ---------------------------------------------------------------------------


def test_publish_event_is_noop_when_feature_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-off: missing / falsey MOBILE_PUSH_ENABLED → no-op."""
    monkeypatch.delenv("MOBILE_PUSH_ENABLED", raising=False)
    # Should not raise, even with bogus payload.
    mobile_push.publish_event("trade_closed", {"trade_id": 42})


def test_publish_event_is_noop_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on + credentials missing → inert; still no-op, no raise."""
    monkeypatch.setenv("MOBILE_PUSH_ENABLED", "1")
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON", raising=False)
    mobile_push.publish_event("trade_closed", {"trade_id": 42})


def test_publish_event_swallows_all_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception inside the notifier path must be swallowed."""

    class _ExplosiveNotifier:
        def publish_to_subscribers(self, **_: Any) -> None:
            raise RuntimeError("simulated detonation")

    def _broken_get() -> Any:
        return _ExplosiveNotifier()

    monkeypatch.setattr(mobile_push, "_get_notifier", _broken_get)
    # Must not raise.
    mobile_push.publish_event("trade_closed", {"trade_id": 42})


# ---------------------------------------------------------------------------
# FcmNotifier.from_env construction
# ---------------------------------------------------------------------------


def test_from_env_returns_inert_when_json_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON", raising=False)
    n = FcmNotifier.from_env()
    assert n.is_active is False


def test_from_env_returns_inert_when_json_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "{not json")
    n = FcmNotifier.from_env()
    assert n.is_active is False


def test_from_env_returns_inert_when_project_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FCM_SERVICE_ACCOUNT_JSON",
        json.dumps({"client_email": "x", "private_key": "y"}),
    )
    monkeypatch.delenv("FCM_PROJECT_ID", raising=False)
    n = FcmNotifier.from_env()
    assert n.is_active is False


def test_from_env_builds_active_notifier_with_full_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FCM_SERVICE_ACCOUNT_JSON",
        json.dumps(
            {
                "project_id": "ict-trader-mobile-app",
                "client_email": "x@y.iam.gserviceaccount.com",
                "private_key": "stub",
                "type": "service_account",
            }
        ),
    )
    n = FcmNotifier.from_env()
    assert n.is_active is True


# ---------------------------------------------------------------------------
# FcmNotifier.from_env — path-based credentials (production wire)
# ---------------------------------------------------------------------------

_VALID_SERVICE_ACCOUNT_JSON = json.dumps(
    {
        "project_id": "ict-trader-mobile-app",
        "client_email": "x@y.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nstub\n-----END PRIVATE KEY-----\n",
        "type": "service_account",
    }
)


def test_from_env_reads_credentials_from_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Production wire: JSON sits in a file (systemd-safe), env points at it."""
    creds = tmp_path / "fcm_service_account.json"
    creds.write_text(_VALID_SERVICE_ACCOUNT_JSON)
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(creds))
    n = FcmNotifier.from_env()
    assert n.is_active is True


def test_from_env_path_takes_priority_over_inline_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If both vars are set, _PATH wins. Keeps the prod wire deterministic
    even when a leftover ``FCM_SERVICE_ACCOUNT_JSON=...`` line lingers in
    .env from the broken pre-fix deploy."""
    creds = tmp_path / "creds.json"
    creds.write_text(_VALID_SERVICE_ACCOUNT_JSON)
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(creds))
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "this-is-not-valid-json")
    n = FcmNotifier.from_env()
    assert n.is_active is True


def test_from_env_inert_when_path_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "FCM_SERVICE_ACCOUNT_JSON_PATH",
        str(tmp_path / "does-not-exist.json"),
    )
    n = FcmNotifier.from_env()
    assert n.is_active is False


def test_from_env_inert_when_path_file_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    creds = tmp_path / "empty.json"
    creds.write_text("")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(creds))
    n = FcmNotifier.from_env()
    assert n.is_active is False


def test_from_env_inert_when_path_file_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    creds = tmp_path / "bad.json"
    creds.write_text("{not-json")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(creds))
    n = FcmNotifier.from_env()
    assert n.is_active is False


# ---------------------------------------------------------------------------
# Subscription filter semantics
# ---------------------------------------------------------------------------


def test_subscription_default_permissive_on_null() -> None:
    assert _truthy_subscription(None, "trade_closed") is True


def test_subscription_default_permissive_on_empty_string() -> None:
    assert _truthy_subscription("", "trade_closed") is True
    assert _truthy_subscription("   ", "trade_closed") is True


def test_subscription_default_permissive_on_malformed_json() -> None:
    assert _truthy_subscription("{not json", "trade_closed") is True


def test_subscription_list_inclusion() -> None:
    assert _truthy_subscription('["trade_closed"]', "trade_closed") is True
    assert _truthy_subscription('["signals"]', "trade_closed") is False


def test_subscription_empty_list_is_permissive() -> None:
    """``[]`` should mean 'subscribed to everything', not 'subscribed
    to nothing'. Matches the default-permissive principle — an explicit
    opt-in list is the operator's way to narrow scope, not their way to
    accidentally silence everything by saving an empty preferences
    screen."""
    assert _truthy_subscription("[]", "trade_closed") is True


def test_subscription_dict_toggles() -> None:
    s = '{"trade_closed": true, "signals": false}'
    assert _truthy_subscription(s, "trade_closed") is True
    assert _truthy_subscription(s, "signals") is False


def test_subscription_dict_missing_key_is_permissive() -> None:
    """Per the docstring contract: keys not in the dict default to
    True. This keeps new event kinds rolled out from the bot side
    visible by default until the operator explicitly mutes them."""
    s = '{"trade_closed": true}'
    assert _truthy_subscription(s, "watchdog_alert") is True


# ---------------------------------------------------------------------------
# Notifier publish path — failure isolation
# ---------------------------------------------------------------------------


def test_publish_to_subscribers_is_noop_when_inert() -> None:
    """Inert notifier must short-circuit before touching the DB."""
    n = FcmNotifier.inert()
    stats = n.publish_to_subscribers(kind="trade_closed", payload={})
    assert stats == {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_unsubscribed": 0,
    }


def test_publish_to_subscribers_swallows_db_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the device-tokens DB lookup raises, publish must return
    empty stats rather than propagate the exception."""
    n = FcmNotifier(
        service_account_info={
            "project_id": "ict-trader-mobile-app",
            "client_email": "x@y.iam.gserviceaccount.com",
            "private_key": "stub",
        },
        project_id="ict-trader-mobile-app",
    )

    def _explode() -> Any:
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(n, "_load_devices", _explode)
    stats = n.publish_to_subscribers(kind="trade_closed", payload={})
    assert stats["attempted"] == 0
    assert stats["failed"] == 0


def test_publish_one_swallows_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 / network error from FCM must not raise; the publish
    method returns False so the caller can count it as failed."""
    n = FcmNotifier(
        service_account_info={
            "project_id": "ict-trader-mobile-app",
            "client_email": "x@y.iam.gserviceaccount.com",
            "private_key": "stub",
        },
        project_id="ict-trader-mobile-app",
    )

    monkeypatch.setattr(n, "_get_access_token", lambda: "fake-access-token")

    class _Boomer:
        def post(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("simulated network outage")

    n._http_client = _Boomer()  # type: ignore[assignment]
    ok = n._publish_one(token="t", kind="trade_closed", payload={"x": 1})
    assert ok is False


def test_publish_one_returns_false_on_non_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = FcmNotifier(
        service_account_info={
            "project_id": "ict-trader-mobile-app",
            "client_email": "x@y.iam.gserviceaccount.com",
            "private_key": "stub",
        },
        project_id="ict-trader-mobile-app",
    )
    monkeypatch.setattr(n, "_get_access_token", lambda: "fake-access-token")

    class _FakeResp:
        status_code = 500
        text = "Internal Server Error"

    class _Fake500Client:
        def post(self, *_args: Any, **_kwargs: Any) -> Any:
            return _FakeResp()

    n._http_client = _Fake500Client()  # type: ignore[assignment]
    ok = n._publish_one(token="t", kind="trade_closed", payload={})
    assert ok is False


def test_build_message_coerces_values_to_strings() -> None:
    """FCM data payloads require string values."""
    n = FcmNotifier.inert()
    msg = n._build_message(
        token="t",
        kind="trade_closed",
        payload={"trade_id": 42, "pnl": 1.5, "skip_me": None, "symbol": "BTCUSDT"},
    )
    assert msg["token"] == "t"
    assert msg["data"]["event_kind"] == "trade_closed"
    assert msg["data"]["trade_id"] == "42"
    assert msg["data"]["pnl"] == "1.5"
    assert msg["data"]["symbol"] == "BTCUSDT"
    assert "skip_me" not in msg["data"]  # null values dropped


def test_build_message_sets_high_android_priority() -> None:
    """Data-only messages must ride at HIGH priority so FCM delivers them
    immediately instead of batching them out of Doze (the operator's
    'notifications arrive in delayed batches' report)."""
    n = FcmNotifier.inert()
    msg = n._build_message(token="t", kind="trade_closed", payload={})
    assert msg["android"]["priority"] == "HIGH"


# ---------------------------------------------------------------------------
# Subscription filtering at fan-out time
# ---------------------------------------------------------------------------


def test_publish_to_subscribers_skips_unsubscribed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Devices subscribed only to ``signals`` must not receive
    ``trade_closed`` events."""
    n = FcmNotifier(
        service_account_info={
            "project_id": "ict-trader-mobile-app",
            "client_email": "x@y.iam.gserviceaccount.com",
            "private_key": "stub",
        },
        project_id="ict-trader-mobile-app",
    )
    # Stub out _load_devices to avoid touching the real DB.
    monkeypatch.setattr(
        n,
        "_load_devices",
        lambda: [
            ("token_a", None),  # subscribed to all
            ("token_b", '["signals"]'),  # subscribed only to signals
            ("token_c", '["trade_closed"]'),  # subscribed only to trade_closed
        ],
    )
    # Pretend every publish succeeds without hitting the network.
    monkeypatch.setattr(n, "_publish_one", lambda **_kwargs: True)
    stats = n.publish_to_subscribers(kind="trade_closed", payload={})
    assert stats["skipped_unsubscribed"] == 1  # token_b
    assert stats["attempted"] == 2  # token_a + token_c
    assert stats["succeeded"] == 2
