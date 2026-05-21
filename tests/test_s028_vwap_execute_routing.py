"""Regression: Coordinator.multi_account_execute must route through
``src.units.accounts.execute.execute_pkg`` for live VWAP/turtle_soup
trades — not through ``account.place_order`` →
``integrator.route_order`` → ``BybitAPI.place(dry_run=False)`` (which
raises NotImplementedError and was the root cause of the
"VWAP signals fire but Bybit never executes" bug recorded in the
2026-05-02 hourly report).

The dry-run path is exercised so this regression survives in
sandboxed runs that have no exchange creds. The live path is
exercised via a stubbed ``bybit_client_for`` so we don't need a
real Bybit account.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


# NOTE: no ``api_key_env`` here on purpose. With one set, load_accounts
# marks the account ``configured=False`` (the env var isn't present in the
# test process), and multi_account_execute now drops unconfigured accounts
# at the eligibility filter — BEFORE the dispatch loop these tests exercise.
# These tests simulate the *client-construction* missing-creds path by
# patching ``bybit_client_for`` to return None, so the account must stay
# configured to reach that branch.
_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _vwap_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=50_000.0,
        sl=50_500.0,
        tp=49_000.0,
        meta={"strategy_name": "vwap"},
    )


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


# ---------------------------------------------------------------------------
# Core regression — order package reaches execute_pkg, not BybitAPI.place
# ---------------------------------------------------------------------------


class TestOrderPackageReachesExecutePkg:
    """The bug shape: pre-fix, multi_account_execute called
    ``account.place_order`` which routed via ``BybitAPI.place`` and
    raised NotImplementedError on every live tick. Post-fix, the
    package must reach ``execute_pkg`` for both dry-run and live paths.
    """

    def test_dry_run_routes_through_execute_pkg(self, coord, accounts_yaml):
        captured = []

        def _stub_execute_pkg(pkg, account_cfg, **kwargs):
            captured.append({
                "pkg": pkg,
                "account_cfg": dict(account_cfg),
                "kwargs": dict(kwargs),
            })
            return f"dry-stub-{account_cfg.get('account_id')}"

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_execute_pkg,
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert len(captured) == 1, (
            "execute_pkg must be the routing target — multi_account_execute "
            f"called it {len(captured)} times instead of exactly 1"
        )
        call = captured[0]
        assert call["pkg"].strategy == "vwap"
        assert call["pkg"].symbol == "BTCUSDT"
        assert call["pkg"].direction == "short"
        assert call["account_cfg"]["account_id"] == "bybit_1"
        assert call["account_cfg"]["exchange"] == "bybit"
        # qty_override surfaces the per-account RiskManager-approved qty
        assert "qty_override" in call["kwargs"]
        assert call["kwargs"]["qty_override"] > 0
        assert call["kwargs"]["dry_run"] is True

        assert len(results) == 1
        assert results[0]["error"] is None
        assert results[0]["trade_id"] == "dry-stub-bybit_1"

    def test_live_path_constructs_per_account_client_and_calls_execute_pkg(
        self, coord, accounts_yaml,
    ):
        """The live path must (a) build an exchange_client via
        ``bybit_client_for`` (so the SDK call has creds) and (b) hand
        both pkg and client to ``execute_pkg``. This is the contract
        that broke pre-fix: live signals fanned out to
        BybitAPI.place(dry_run=False) which never received a client."""
        sentinel_client = object()
        captured = []

        def _stub_execute_pkg(pkg, account_cfg, **kwargs):
            captured.append({"pkg": pkg, "kwargs": dict(kwargs)})
            return "live-stub-orderid"

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=sentinel_client,
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_execute_pkg,
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert len(captured) == 1
        assert captured[0]["kwargs"]["exchange_client"] is sentinel_client
        assert captured[0]["kwargs"]["dry_run"] is False
        assert results[0]["error"] is None
        assert results[0]["trade_id"] == "live-stub-orderid"

    def test_no_call_to_bybit_api_place(self, coord, accounts_yaml):
        """Belt-and-braces: confirm the legacy NotImplementedError site
        is never reached. ``BybitAPI.place(dry_run=False)`` raises with
        the message that names the fix; if that path is touched at all
        the test fails — even when execute_pkg is also mocked."""
        from src.units.accounts import integrator as integ

        with patch.object(
            integ.BybitAPI, "place",
            side_effect=AssertionError(
                "BybitAPI.place must not be called from multi_account_execute"
            ),
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            return_value="dry-stub",
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert results[0]["error"] is None


# ---------------------------------------------------------------------------
# Diagnostic ping on execution failure
# ---------------------------------------------------------------------------


class TestExecutionFailureDiagnosticPing:
    def test_ping_enqueued_when_execute_pkg_raises(
        self, coord, accounts_yaml, tmp_path, monkeypatch,
    ):
        from src.runtime import execution_diagnostics as diag

        ping_dir = tmp_path / "pending_pings"
        monkeypatch.setattr(diag, "PENDING_PINGS_DIR", ping_dir)

        def _boom(pkg, account_cfg, **kwargs):
            raise RuntimeError("Bybit API rejected: insufficient margin")

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_boom,
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert results[0]["trade_id"] is None
        assert "RuntimeError" in (results[0]["error"] or "")

        files = list(ping_dir.glob("*-execfail.json")) if ping_dir.exists() else []
        assert len(files) == 1, (
            f"a diagnostic ping must be enqueued on per-account execution "
            f"failure; found {files}"
        )
        import json
        payload = json.loads(files[0].read_text())
        assert payload["priority"] == "high"
        body = payload["body"]
        assert "bybit_1" in body
        assert "vwap" in body
        assert "BTCUSDT" in body
        assert "sell" in body  # direction=short → side=sell
        assert "insufficient margin" in body

    def test_live_mode_missing_creds_emits_ping_and_does_not_silently_dry_run(
        self, coord, accounts_yaml, tmp_path, monkeypatch,
    ):
        """If ``bybit_client_for`` returns None in live mode (api_key_env
        missing from process env), the previous bug shape would have
        let ``execute_pkg`` silently flip ``is_dry=True`` — producing a
        dry trade_id while the operator believed they were live. The
        fix surfaces it as a hard failure with a diagnostic ping."""
        from src.runtime import execution_diagnostics as diag
        ping_dir = tmp_path / "pending_pings"
        monkeypatch.setattr(diag, "PENDING_PINGS_DIR", ping_dir)

        sentinel_called = []

        def _stub_execute_pkg(*args, **kwargs):
            sentinel_called.append(kwargs)
            return "should-not-reach"

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=None,
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_execute_pkg,
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert sentinel_called == [], (
            "execute_pkg must not be called when live creds are missing — "
            "silent dry-run fallback is the failure mode the fix removes"
        )
        assert results[0]["trade_id"] is None
        # Coordinator message changed from "missing API credentials" to
        # "not fully configured: api_key_env=..." (BUG-034 rewire + BUG-045
        # per-account mode fix).  Check the stable phrase that is always present.
        assert "not fully configured" in (results[0]["error"] or "")
        files = list(ping_dir.glob("*-execfail.json")) if ping_dir.exists() else []
        assert len(files) == 1

    def test_no_ping_when_execution_succeeds(
        self, coord, accounts_yaml, tmp_path, monkeypatch,
    ):
        from src.runtime import execution_diagnostics as diag
        ping_dir = tmp_path / "pending_pings"
        monkeypatch.setattr(diag, "PENDING_PINGS_DIR", ping_dir)

        with patch(
            "src.units.accounts.execute.execute_pkg",
            return_value="dry-ok",
        ):
            coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        files = list(ping_dir.glob("*-execfail.json")) if ping_dir.exists() else []
        assert files == [], (
            "execution_diagnostics must stay quiet on successful dispatches; "
            f"found stray pings: {files}"
        )
