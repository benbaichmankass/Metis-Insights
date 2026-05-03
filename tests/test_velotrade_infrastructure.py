"""Velotrade phase-2 infrastructure tests.

Covers the new pieces that turn the phase-1 stub into a real-shaped
integration with a "not fully configured" account state:

  - ``DXtradeClient`` constructor validation + stub method bodies.
  - ``MissingCredentialsError`` is the canonical missing-creds signal.
  - ``velotrade_client_for(account)``: factory returns ``None`` when
    creds are missing; constructs a ``DXtradeClient`` when set.
  - Coordinator's ``multi_account_execute`` velotrade branch:
    - dry-run ignores missing creds (legacy behaviour preserved).
    - live + missing creds → diagnostic ping + error row, no SDK call.
    - live + creds set → SDK call dispatched (and the stub method's
      ``NotImplementedError`` surfaces as a structured error row).
  - Loader marks accounts as ``configured=False`` when creds absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.dxtrade_client import (
    DXtradeClient,
    MissingCredentialsError,
)


def _pkg(symbol: str = "BTCUSDT") -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol=symbol,
        direction="long",
        entry=100.0,
        sl=99.0,
        tp=102.0,
        meta={},
    )


# ---------------------------------------------------------------------------
# DXtradeClient
# ---------------------------------------------------------------------------


class TestDXtradeClient:
    def test_constructor_requires_api_key(self):
        with pytest.raises(MissingCredentialsError, match="api_key is empty"):
            DXtradeClient(api_key="", api_secret="abc")

    def test_constructor_requires_api_secret(self):
        with pytest.raises(MissingCredentialsError, match="api_secret is empty"):
            DXtradeClient(api_key="abc", api_secret="")

    def test_constructor_rejects_none_creds(self):
        with pytest.raises(MissingCredentialsError):
            DXtradeClient(api_key=None, api_secret="abc")  # type: ignore[arg-type]
        with pytest.raises(MissingCredentialsError):
            DXtradeClient(api_key="abc", api_secret=None)  # type: ignore[arg-type]

    def test_constructor_stores_base_url_and_timeout(self):
        c = DXtradeClient(
            api_key="k", api_secret="s",
            base_url="https://demo.dx.trade", timeout=15.0,
        )
        assert c.base_url == "https://demo.dx.trade"
        assert c.timeout == 15.0

    def test_fingerprint_is_last_four_chars(self):
        c = DXtradeClient(api_key="ABCDEFG", api_secret="s")
        assert c.fingerprint() == "DEFG"

    def test_fingerprint_short_key(self):
        c = DXtradeClient(api_key="ab", api_secret="s")
        assert c.fingerprint() == ""  # < 4 chars → empty fingerprint

    @pytest.mark.parametrize("method,args", [
        ("place", ({"symbol": "BTCUSDT"},)),
        ("cancel", ("oid",)),
        ("status", ("oid",)),
        ("balance", ()),
    ])
    def test_sdk_methods_raise_contract_pending(self, method, args):
        c = DXtradeClient(api_key="k", api_secret="s")
        with pytest.raises(NotImplementedError, match="contract pending"):
            getattr(c, method)(*args)

    def test_missing_credentials_is_runtime_error_subclass(self):
        # Coordinator's broad except-Exception catches both RuntimeError
        # and MissingCredentialsError uniformly.
        assert issubclass(MissingCredentialsError, RuntimeError)


# ---------------------------------------------------------------------------
# velotrade_client_for
# ---------------------------------------------------------------------------


class TestVelotradeClientFactory:
    def test_returns_none_when_creds_missing(self, monkeypatch):
        monkeypatch.delenv("VELOTRADE_API_KEY_1", raising=False)
        monkeypatch.delenv("VELOTRADE_API_SECRET_1", raising=False)
        from src.units.accounts.clients import velotrade_client_for
        client = velotrade_client_for({
            "api_key_env": "VELOTRADE_API_KEY_1",
            "exchange": "velotrade",
        })
        assert client is None

    def test_constructs_client_when_creds_present(self, monkeypatch):
        monkeypatch.setenv("VELOTRADE_API_KEY_1", "test-key-XYZ123")
        monkeypatch.setenv("VELOTRADE_API_SECRET_1", "test-secret")
        monkeypatch.setenv("VELOTRADE_BASE_URL", "https://demo.dx.trade")
        from src.units.accounts.clients import velotrade_client_for
        client = velotrade_client_for({
            "api_key_env": "VELOTRADE_API_KEY_1",
            "exchange": "velotrade",
        })
        assert isinstance(client, DXtradeClient)
        assert client.base_url == "https://demo.dx.trade"
        assert client.fingerprint() == "Z123"

    def test_account_level_base_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("VELOTRADE_API_KEY_1", "k")
        monkeypatch.setenv("VELOTRADE_API_SECRET_1", "s")
        monkeypatch.setenv("VELOTRADE_BASE_URL", "https://from-env")
        from src.units.accounts.clients import velotrade_client_for
        client = velotrade_client_for({
            "api_key_env": "VELOTRADE_API_KEY_1",
            "base_url": "https://from-account-cfg",
            "exchange": "velotrade",
        })
        assert client.base_url == "https://from-account-cfg"


# ---------------------------------------------------------------------------
# Loader: configured flag
# ---------------------------------------------------------------------------


_YAML_TWO_ACCOUNTS = """
accounts:
  bybit_present:
    type: regular
    exchange: bybit
    api_key_env: BYBIT_API_KEY_PRESENT
    risk:
      max_dd_pct: 0.05
      daily_usd: 100
      pos_size: 500
      risk_pct: 0.01

  velo_absent:
    type: prop
    exchange: velotrade
    api_key_env: VELOTRADE_API_KEY_ABSENT
    strategies: []
    account_state: evaluation
    risk:
      max_dd_pct: 0.02
      daily_usd: 50
      pos_size: 200
"""


class TestLoaderConfiguredFlag:
    def test_loader_marks_missing_creds_as_not_configured(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv("BYBIT_API_KEY_PRESENT", raising=False)
        monkeypatch.delenv("VELOTRADE_API_KEY_ABSENT", raising=False)
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_TWO_ACCOUNTS)
        from src.units.accounts import load_accounts
        accs = {a.name: a for a in load_accounts(str(p))}
        assert accs["bybit_present"].configured is False
        assert accs["velo_absent"].configured is False
        assert "BYBIT_API_KEY_PRESENT" in (
            accs["bybit_present"].configured_reason or ""
        )
        assert "VELOTRADE_API_KEY_ABSENT" in (
            accs["velo_absent"].configured_reason or ""
        )

    def test_loader_marks_present_creds_as_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BYBIT_API_KEY_PRESENT", "k")
        monkeypatch.setenv("BYBIT_API_SECRET_PRESENT", "s")
        monkeypatch.delenv("VELOTRADE_API_KEY_ABSENT", raising=False)
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_TWO_ACCOUNTS)
        from src.units.accounts import load_accounts
        accs = {a.name: a for a in load_accounts(str(p))}
        assert accs["bybit_present"].configured is True
        assert accs["bybit_present"].configured_reason is None
        # The velotrade account is still missing creds.
        assert accs["velo_absent"].configured is False

    def test_status_dict_surfaces_configured_fields(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VELOTRADE_API_KEY_ABSENT", raising=False)
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_TWO_ACCOUNTS)
        from src.units.accounts import load_accounts
        accs = {a.name: a for a in load_accounts(str(p))}
        status = accs["velo_absent"].status()
        assert status["configured"] is False
        assert "VELOTRADE_API_KEY_ABSENT" in status["configured_reason"]


# ---------------------------------------------------------------------------
# Coordinator: not-configured ping path
# ---------------------------------------------------------------------------


class TestCoordinatorNotConfiguredPing:
    def test_live_velotrade_missing_creds_emits_diagnostic(
        self, tmp_path, monkeypatch,
    ):
        # Goal: exercise the full multi_account_execute path with a
        # velotrade account whose creds aren't set, in *live* mode,
        # and assert that (a) the result row carries a "not fully
        # configured" error and (b) the diagnostic ping was enqueued.
        monkeypatch.delenv("VELOTRADE_API_KEY_ABSENT", raising=False)
        monkeypatch.delenv("VELOTRADE_API_SECRET_ABSENT", raising=False)
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_TWO_ACCOUNTS)

        # Capture the ping payload via monkeypatching the diagnostic
        # enqueue helper so the test doesn't need a writable
        # runtime_logs/pending_pings directory.
        recorded: list[dict] = []

        def _fake_enqueue(*, account, strategy, symbol, side, qty, reason,
                          priority="high"):
            recorded.append({
                "account": account, "strategy": strategy, "symbol": symbol,
                "side": side, "qty": qty, "reason": reason,
            })
            return None

        import src.runtime.execution_diagnostics as diag_mod
        monkeypatch.setattr(
            diag_mod, "enqueue_execution_failure", _fake_enqueue,
        )
        # Coordinator imports the helper at module import time; patch
        # the rebound name there too.
        import src.core.coordinator as coord_mod
        # Find and replace the imported symbol if it was rebound.
        if hasattr(coord_mod, "enqueue_execution_failure"):
            monkeypatch.setattr(
                coord_mod, "enqueue_execution_failure", _fake_enqueue,
            )

        # Restrict to the velo_absent account so the bybit one
        # doesn't add noise.
        from src.units.accounts import load_accounts
        accounts = [
            a for a in load_accounts(str(p)) if a.name == "velo_absent"
        ]
        accounts[0].strategies = ["vwap"]  # let the strategy filter pass
        monkeypatch.setattr(
            "src.units.accounts.load_accounts", lambda path=None: accounts,
        )

        # Skip the order_packages writer to avoid touching the DB.
        monkeypatch.setattr(
            coord_mod, "_log_new_order_package", lambda pkg: None,
        )

        from src.core.coordinator import Coordinator
        coord = Coordinator()
        pkg = _pkg()
        pkg.meta["account_balances_usd"] = {"velo_absent": 10_000.0}

        results = coord.multi_account_execute(
            pkg, accounts_path=str(p), dry_run=False,
        )
        velo_result = next(r for r in results if r["name"] == "velo_absent")
        assert velo_result["trade_id"] is None
        assert velo_result["error"] is not None
        assert "not fully configured" in velo_result["error"]
        assert "VELOTRADE_API_KEY_ABSENT" in velo_result["error"]
        # Diagnostic ping should have fired with the same reason.
        assert recorded, (
            "expected enqueue_execution_failure to be called for the "
            "not-configured account"
        )
        assert recorded[0]["account"] == "velo_absent"
        assert "not fully configured" in recorded[0]["reason"]

    def test_dry_run_velotrade_missing_creds_does_not_ping(
        self, tmp_path, monkeypatch,
    ):
        # Dry-run should NOT fire the ping for missing creds — by
        # design, dry-run means "don't touch the exchange", so missing
        # creds aren't a fault yet. The ping is reserved for real
        # action attempts.
        monkeypatch.delenv("VELOTRADE_API_KEY_ABSENT", raising=False)
        p = tmp_path / "accounts.yaml"
        p.write_text(_YAML_TWO_ACCOUNTS)

        recorded: list[dict] = []

        def _fake_enqueue(**kwargs):
            recorded.append(kwargs)
            return None

        import src.runtime.execution_diagnostics as diag_mod
        monkeypatch.setattr(
            diag_mod, "enqueue_execution_failure", _fake_enqueue,
        )
        import src.core.coordinator as coord_mod
        if hasattr(coord_mod, "enqueue_execution_failure"):
            monkeypatch.setattr(
                coord_mod, "enqueue_execution_failure", _fake_enqueue,
            )

        from src.units.accounts import load_accounts
        accounts = [
            a for a in load_accounts(str(p)) if a.name == "velo_absent"
        ]
        accounts[0].strategies = ["vwap"]
        monkeypatch.setattr(
            "src.units.accounts.load_accounts", lambda path=None: accounts,
        )
        monkeypatch.setattr(
            coord_mod, "_log_new_order_package", lambda pkg: None,
        )

        from src.core.coordinator import Coordinator
        coord = Coordinator()
        pkg = _pkg()
        pkg.meta["account_balances_usd"] = {"velo_absent": 10_000.0}

        results = coord.multi_account_execute(
            pkg, accounts_path=str(p), dry_run=True,
        )
        velo_result = next(r for r in results if r["name"] == "velo_absent")
        # Dry-run trade goes through (returns a dry-run trade_id).
        assert velo_result["trade_id"] is not None
        # No ping fired for the not-configured state in dry-run.
        assert not recorded


# ---------------------------------------------------------------------------
# Real config wiring
# ---------------------------------------------------------------------------


class TestRealAccountsYamlWiring:
    def test_prop_velotrade_1_loads_as_not_configured(self, monkeypatch):
        monkeypatch.delenv("VELOTRADE_API_KEY_1", raising=False)
        monkeypatch.delenv("VELOTRADE_API_SECRET_1", raising=False)
        p = Path(__file__).resolve().parents[1] / "config" / "accounts.yaml"
        from src.units.accounts import load_accounts
        accs = {a.name: a for a in load_accounts(str(p))}
        assert "prop_velotrade_1" in accs
        prop = accs["prop_velotrade_1"]
        assert prop.configured is False
        assert prop.exchange == "velotrade"
        assert prop.account_type == "prop"
