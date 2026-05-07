"""Tests for src.units.accounts.dup_key_check (BUG-033)."""
from __future__ import annotations

from types import SimpleNamespace


from src.units.accounts.dup_key_check import (
    find_duplicate_keys,
    warn_on_duplicate_keys,
)


def _acct(name: str, env_var: str, exchange: str = "bybit"):
    return SimpleNamespace(name=name, api_key_env=env_var, exchange=exchange)


def test_no_duplicates_returns_empty(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY_1", "one")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "s1")
    monkeypatch.setenv("BYBIT_API_KEY_2", "two")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "s2")
    accs = [_acct("a", "BYBIT_API_KEY_1"), _acct("b", "BYBIT_API_KEY_2")]
    assert find_duplicate_keys(accs) == []


def test_duplicate_keys_are_detected(monkeypatch):
    """The BUG-033 reproduction: two accounts pointed at the same key.

    This happens when accounts.yaml says api_key_env=BYBIT_API_KEY_1 for
    both accounts (typo) or when the master file populated both slots
    with the identical key.
    """
    monkeypatch.setenv("BYBIT_API_KEY_1", "shared_key_value")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "s")
    monkeypatch.setenv("BYBIT_API_KEY_2", "shared_key_value")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "s")
    accs = [_acct("bybit_1", "BYBIT_API_KEY_1"),
            _acct("bybit_2", "BYBIT_API_KEY_2")]
    dups = find_duplicate_keys(accs)
    assert len(dups) == 1
    suffix, names = dups[0]
    assert suffix == "alue"  # last 4 chars of "shared_key_value"
    assert names == ["bybit_1", "bybit_2"]


def test_missing_credentials_are_skipped(monkeypatch):
    """Accounts with no resolved key shouldn't trigger false positives."""
    monkeypatch.delenv("BYBIT_API_KEY_99", raising=False)
    accs = [_acct("ghost", "BYBIT_API_KEY_99"),
            _acct("ghost2", "BYBIT_API_KEY_99")]
    assert find_duplicate_keys(accs) == []


def test_warn_on_duplicate_keys_emits_outcome(monkeypatch):
    """warn_on_duplicate_keys must reach the outcomes pipeline."""
    monkeypatch.setenv("BYBIT_API_KEY_1", "dup_key_xx")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "s")
    monkeypatch.setenv("BYBIT_API_KEY_2", "dup_key_xx")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "s")
    accs = [_acct("bybit_1", "BYBIT_API_KEY_1"),
            _acct("bybit_2", "BYBIT_API_KEY_2")]

    captured = []

    def fake_report(action, status, *, level=None, reason=None, **ctx):
        captured.append({"action": action, "status": status,
                         "reason": reason, "ctx": ctx})

    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", fake_report)

    warn_on_duplicate_keys(accs)
    assert len(captured) == 1
    assert captured[0]["action"] == "accounts_dup_key"
    assert captured[0]["status"] == "detected"
    assert "bybit_1" in captured[0]["ctx"].get("accounts", "")
    assert "bybit_2" in captured[0]["ctx"].get("accounts", "")


def test_warn_never_raises_on_internal_failure(monkeypatch):
    """Must be safe to call from main.py startup with no try/except."""
    def boom(_):
        raise RuntimeError("simulated")
    monkeypatch.setattr(
        "src.units.accounts.dup_key_check.find_duplicate_keys", boom
    )
    # Must not raise.
    warn_on_duplicate_keys([_acct("a", "X")])
