"""Auth: bundle parsing + refresh-margin behaviour without hitting network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.units.accounts.tradovate.auth import TokenBundle, _bundle_from_payload


def test_bundle_parses_iso_expiry():
    b = _bundle_from_payload({
        "accessToken": "AAAA", "mdAccessToken": "BBBB",
        "expirationTime": "2030-01-01T00:00:00Z", "userId": 99,
    })
    assert b.access_token == "AAAA"
    assert b.md_access_token == "BBBB"
    assert b.expires_at.year == 2030
    assert b.user_id == 99


def test_bundle_parses_epoch_millis():
    epoch_ms = (datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()) * 1000
    b = _bundle_from_payload({"accessToken": "x", "expirationTime": epoch_ms})
    assert b.expires_at.year == 2030


def test_bundle_falls_back_when_missing_expiry():
    b = _bundle_from_payload({"accessToken": "x"})
    # falls back to ~30 minutes ahead
    delta = (b.expires_at - datetime.now(timezone.utc)).total_seconds()
    assert 1500 < delta < 1900


def test_is_expiring_within_margin():
    b = TokenBundle(
        access_token="x", md_access_token=None,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    assert b.is_expiring(margin_s=120) is True
    assert b.is_expiring(margin_s=10) is False
