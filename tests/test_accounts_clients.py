"""Tests for src.units.accounts.clients (canonical per-account client owner)."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from src.units.accounts import clients


def test_resolve_credentials_from_api_key_env(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY_1", "key-one")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "secret-one")
    out = clients.resolve_credentials({"api_key_env": "BYBIT_API_KEY_1"})
    assert out == {"api_key": "key-one", "api_secret": "secret-one"}


def test_resolve_credentials_explicit_secret_env(monkeypatch):
    monkeypatch.setenv("BYBIT_KEY", "k")
    monkeypatch.setenv("BYBIT_SECRET_CUSTOM", "s")
    out = clients.resolve_credentials({
        "api_key_env": "BYBIT_KEY",
        "api_secret_env": "BYBIT_SECRET_CUSTOM",
    })
    assert out == {"api_key": "k", "api_secret": "s"}


def test_resolve_credentials_missing_returns_none(monkeypatch):
    monkeypatch.delenv("BYBIT_API_KEY_99", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_99", raising=False)
    assert clients.resolve_credentials({"api_key_env": "BYBIT_API_KEY_99"}) is None


def test_resolve_credentials_not_a_dict():
    assert clients.resolve_credentials("not a dict") is None
    assert clients.resolve_credentials(None) is None


def test_resolve_credentials_no_keys_at_all():
    assert clients.resolve_credentials({}) is None


def test_data_loaders_re_exports_match():
    """Back-compat shim: bot.data_loaders re-exports from accounts.clients."""
    from src.bot import data_loaders as dl
    assert dl.bybit_client_for is clients.bybit_client_for
    assert dl.binance_conn_for is clients.binance_conn_for


def test_two_accounts_with_different_env_vars_resolve_distinctly(monkeypatch):
    """The BUG-030 regression: bybit_1 and bybit_2 must resolve to distinct
    credentials when distinct env vars are set."""
    monkeypatch.setenv("BYBIT_API_KEY_1", "key-one")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "secret-one")
    monkeypatch.setenv("BYBIT_API_KEY_2", "key-two")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "secret-two")

    a1 = clients.resolve_credentials({"api_key_env": "BYBIT_API_KEY_1"})
    a2 = clients.resolve_credentials({"api_key_env": "BYBIT_API_KEY_2"})

    assert a1 is not None and a2 is not None
    assert a1["api_key"] != a2["api_key"]
    assert a1["api_secret"] != a2["api_secret"]
