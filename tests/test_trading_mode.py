"""Tests for src.runtime.trading_mode (BUG-031 contract)."""
from __future__ import annotations

import pytest

from src.runtime.trading_mode import (
    LIVE_DEFAULTS,
    allow_live_trading,
    is_dry_run,
    is_dry_truthy,
    is_live_truthy,
)


@pytest.mark.parametrize(
    "value",
    ["true", "True", "TRUE", "1", "yes", "on", "live", "LIVE", "Live", True, 1],
)
def test_is_live_truthy_accepts(value):
    assert is_live_truthy(value) is True


@pytest.mark.parametrize(
    "value",
    ["false", "0", "no", "off", "dry", "", None, False, 0, "garbage"],
)
def test_is_live_truthy_rejects(value):
    assert is_live_truthy(value) is False


@pytest.mark.parametrize(
    "value",
    ["true", "1", "yes", "on", "dry", "dry_run", "dry-run", "paper", "PAPER", True],
)
def test_is_dry_truthy_accepts(value):
    assert is_dry_truthy(value) is True


@pytest.mark.parametrize(
    "value",
    ["false", "0", "no", "off", "live", "", None, False, "garbage"],
)
def test_is_dry_truthy_rejects(value):
    assert is_dry_truthy(value) is False


def test_default_is_live(monkeypatch):
    """With no env vars set, the system is live."""
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert allow_live_trading() is True
    assert is_dry_run() is False


def test_explicit_live_string_is_live(monkeypatch):
    """BUG-031: ALLOW_LIVE_TRADING=live (operator's natural form) is honoured."""
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "live")
    assert allow_live_trading() is True


def test_dry_run_paper_alias(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "paper")
    assert is_dry_run() is True


def test_live_defaults_constants():
    """The LIVE_DEFAULTS dict is the canonical default-pair."""
    assert LIVE_DEFAULTS == {"ALLOW_LIVE_TRADING": "true", "DRY_RUN": "false"}
