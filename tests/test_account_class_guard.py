"""Tests for the account_class CI guard (scripts/check_account_class.py).

The guard freezes the invariant that every account in accounts.yaml
carries a valid ``account_class`` (the paper/real funding category) and
that it never contradicts the Bybit-only ``demo`` transport flag.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD = _REPO_ROOT / "scripts" / "check_account_class.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_account_class", _GUARD)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_accounts(path: Path, accounts: dict) -> Path:
    import yaml

    path.write_text(yaml.safe_dump({"accounts": accounts}), encoding="utf-8")
    return path


class TestCheckAccounts:
    """Unit-test the pure ``check_accounts`` rule engine."""

    def test_valid_config_passes(self):
        mod = _load_guard()
        accounts = {
            "bybit_1": {"exchange": "bybit", "demo": True, "account_class": "paper"},
            "bybit_2": {"exchange": "bybit", "account_class": "real_money"},
            "ib_paper": {"exchange": "interactive_brokers", "account_class": "paper"},
            "ib_live": {"exchange": "interactive_brokers", "account_class": "real_money"},
        }
        assert mod.check_accounts(accounts) == []

    def test_missing_field_fails(self):
        mod = _load_guard()
        accounts = {"bybit_2": {"exchange": "bybit"}}  # no account_class
        violations = mod.check_accounts(accounts)
        assert len(violations) == 1
        assert "missing required `account_class`" in violations[0]

    def test_invalid_value_fails(self):
        mod = _load_guard()
        accounts = {"x": {"exchange": "bybit", "account_class": "demo"}}
        violations = mod.check_accounts(accounts)
        assert len(violations) == 1
        assert "invalid `account_class" in violations[0]

    def test_bybit_demo_real_money_inconsistent_fails(self):
        mod = _load_guard()
        accounts = {
            "bad": {"exchange": "bybit", "demo": True, "account_class": "real_money"},
        }
        violations = mod.check_accounts(accounts)
        # Two rules trip on this (bybit-specific + the general demo+real_money).
        assert violations
        assert any("real_money" in v for v in violations)

    def test_demo_real_money_any_exchange_fails(self):
        mod = _load_guard()
        accounts = {
            "bad": {"exchange": "oanda", "demo": True, "account_class": "real_money"},
        }
        violations = mod.check_accounts(accounts)
        assert any("demo" in v.lower() for v in violations)

    def test_bybit_demo_paper_is_ok(self):
        mod = _load_guard()
        accounts = {
            "bybit_1": {"exchange": "bybit", "demo": True, "account_class": "paper"},
        }
        assert mod.check_accounts(accounts) == []


class TestGuardCli:
    """End-to-end CLI runs against temp fixtures + the real repo."""

    def test_real_repo_passes(self):
        result = subprocess.run(
            [sys.executable, str(_GUARD), "--list"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"guard failed on the real accounts.yaml:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_cli_fails_on_missing_field(self, tmp_path):
        path = _write_accounts(tmp_path / "accounts.yaml", {
            "bybit_2": {"exchange": "bybit"},
        })
        result = subprocess.run(
            [sys.executable, str(_GUARD), "--accounts", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "account_class" in result.stderr

    def test_cli_fails_on_inconsistent_demo(self, tmp_path):
        path = _write_accounts(tmp_path / "accounts.yaml", {
            "bad": {"exchange": "bybit", "demo": True, "account_class": "real_money"},
        })
        result = subprocess.run(
            [sys.executable, str(_GUARD), "--accounts", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_cli_passes_on_valid_fixture(self, tmp_path):
        path = _write_accounts(tmp_path / "accounts.yaml", {
            "bybit_1": {"exchange": "bybit", "demo": True, "account_class": "paper"},
            "bybit_2": {"exchange": "bybit", "account_class": "real_money"},
        })
        result = subprocess.run(
            [sys.executable, str(_GUARD), "--accounts", str(path), "--list"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
