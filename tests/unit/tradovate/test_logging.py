"""Secrets must never appear in log output."""
from src.units.accounts.tradovate.logging_utils import _scrub


def test_scrub_redacts_secret_keys():
    out = _scrub({"name": "u", "password": "hunter2", "sec": "s3cr3t"})
    assert out["password"] == "***"
    assert out["sec"] == "***"
    assert out["name"] == "u"


def test_scrub_truncates_long_strings():
    out = _scrub("A" * 200)
    assert "len=200" in out
    assert len(out) < 200


def test_scrub_nested():
    out = _scrub({"creds": {"accessToken": "abc", "userId": 1}})
    assert out["creds"]["accessToken"] == "***"
    assert out["creds"]["userId"] == 1
