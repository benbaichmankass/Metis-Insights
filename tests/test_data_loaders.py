"""Tests for src/bot/data_loaders.py — Sprint S-001 PR-B1 (registry).

Each loader has a happy path + at least one failure-mode test, per the
spec's acceptance criteria (docs/TELEGRAM-SPEC.md §6). DB readers and
exchange queries land in PR-B2 / PR-B3 with their own tests.
"""
import sys

import pytest

from src.bot import data_loaders as dl


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A throwaway repo root with empty deploy/ and config/ dirs."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH",
                        str(tmp_path / "config" / "accounts.yaml"))
    return tmp_path


# -- list_live_strategies -----------------------------------------------------

def test_list_live_strategies_happy_path():
    out = dl.list_live_strategies()
    # Defensive: in a sandbox without ccxt this returns []. In a healthy env
    # it must include the four strategies the multiplexer iterates.
    assert isinstance(out, list)
    if out:
        for expected in ("breakout_confirmation", "vwap", "killzone", "ict"):
            assert expected in out


def test_list_live_strategies_handles_pipeline_import_error(monkeypatch):
    """If src.runtime.pipeline is broken, the loader returns [].

    We simulate the broken state by injecting a sentinel module whose
    ``STRATEGIES`` attribute raises on access — safer than monkey-patching
    ``builtins.__import__`` (which would bleed into other tests via
    partially-loaded modules in sys.modules).
    """

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("simulated broken pipeline")

    monkeypatch.setitem(sys.modules, "src.runtime.pipeline", _Boom())
    assert dl.list_live_strategies() == []


# -- list_trader_services -----------------------------------------------------

def test_list_trader_services_scans_deploy_dir(fake_repo):
    deploy = fake_repo / "deploy"
    (deploy / "ict-trader-live.service").write_text("# unit\n")
    (deploy / "ict-trader-binance-1.service").write_text("# unit\n")
    (deploy / "ict-telegram-bot.service").write_text("# unit\n")  # not a trader
    (deploy / "ict-heartbeat.timer").write_text("# timer\n")  # not a service

    out = dl.list_trader_services()
    assert sorted(out) == ["ict-trader-binance-1", "ict-trader-live"]


def test_list_trader_services_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path / "does-not-exist"))
    assert dl.list_trader_services() == []


# -- list_accounts ------------------------------------------------------------

def test_list_accounts_legacy_env_only(fake_repo):
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    out = dl.list_accounts()
    assert len(out) == 1
    a = out[0]
    assert a["account_id"] == "live"
    assert a["service"] == "ict-trader-live"
    assert a["exchange"] == "bybit"
    assert a["source"] == "env"


def test_list_accounts_multi_env(fake_repo):
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    (fake_repo / ".env.binance-sub-1").write_text(
        "BINANCE_API_KEY=k\nBINANCE_API_SECRET=s\n"
    )
    out = dl.list_accounts()
    ids = [a["account_id"] for a in out]
    assert "live" in ids
    assert "binance-sub-1" in ids
    sub = next(a for a in out if a["account_id"] == "binance-sub-1")
    assert sub["service"] == "ict-trader-binance-sub-1"
    assert sub["exchange"] == "binance"


def test_list_accounts_empty_repo(fake_repo):
    # No .env, no yaml — must return [], not crash.
    assert dl.list_accounts() == []


def test_list_accounts_yaml_takes_precedence(fake_repo):
    pytest.importorskip("yaml")
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    yaml_path = fake_repo / "config" / "accounts.yaml"
    yaml_path.write_text(
        "accounts:\n"
        "  - account_id: live\n"
        "    exchange: bybit\n"
        "    env_path: /custom/.env\n"
        "    service: ict-trader-live\n"
        "    strategies: [ict]\n"
    )
    out = dl.list_accounts()
    live = next(a for a in out if a["account_id"] == "live")
    assert live["source"] == "yaml"
    assert live["env_path"] == "/custom/.env"
