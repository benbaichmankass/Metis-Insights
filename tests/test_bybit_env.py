"""Smoke test: Bybit credential env vars are present.

Skips automatically in CI or any environment where the variables are not set.
Never prints or asserts on key values.
"""
from __future__ import annotations

import os

import pytest


def _bybit_vars() -> tuple[str, str]:
    key = os.getenv("BYBIT_API_KEY") or os.getenv("BYBIT_TESTNET_API_KEY", "")
    secret = os.getenv("BYBIT_API_SECRET") or os.getenv("BYBIT_TESTNET_API_SECRET", "")
    return key, secret


@pytest.mark.skipif(
    not any([os.getenv("BYBIT_API_KEY"), os.getenv("BYBIT_TESTNET_API_KEY")]),
    reason="Bybit env vars not set; skipping credential presence check",
)
def test_bybit_credential_env_vars_present():
    key, secret = _bybit_vars()
    assert key, "BYBIT_API_KEY or BYBIT_TESTNET_API_KEY must be non-empty"
    assert secret, "BYBIT_API_SECRET or BYBIT_TESTNET_API_SECRET must be non-empty"
