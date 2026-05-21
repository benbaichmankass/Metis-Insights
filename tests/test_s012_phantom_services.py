"""S-012 PR D3: phantom-service regression tests.

Pins the repo-side root cause + the Telegram-bot guardrail for the
symptom that triggered S-012:

    ✅ ict-trader-live started. Status: active
    ❌ Failed to start ict-trader-bak: Unit ict-trader-bak.service not found.
    ❌ Failed to start ict-trader-example: Unit ict-trader-example.service not found.

Two independent regressions:
  1. ``data_loaders._load_env_accounts()`` must skip reserved env files
     (``.env.example``, ``.env.bak``, ``.env.template``, …) so they
     never produce phantom account_ids / service references.
  2. ``telegram_query_bot.toggle_service()`` must refuse to invoke
     systemctl when the requested service has no matching unit file in
     ``deploy/`` — surfacing config drift loudly instead of silently
     emitting a systemctl "Unit not found" error.

PM § 8 #5 (b) — ship the regression test; PM runs the § 4.5 VM-side
diagnostics separately.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stub heavy / unavailable deps before importing telegram_query_bot.
# Mirrors the pattern in tests/test_kill_switch.py.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
_ContextTypes = MagicMock()
_ContextTypes.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ContextTypes


# ---------------------------------------------------------------------------
# Root-cause fix: env discovery filters reserved file names
# ---------------------------------------------------------------------------


class TestEnvDiscoveryFiltersReservedNames:
    """Reserved .env.<name> files must not produce phantom accounts."""

    def test_env_example_does_not_produce_account(self, tmp_path):
        from src.bot import data_loaders as dl

        (tmp_path / ".env.example").write_text("API_KEY=template\n")
        accounts = dl._load_env_accounts(repo_root=str(tmp_path))
        ids = [a["account_id"] for a in accounts]
        assert "example" not in ids, (
            "S-012 phantom regression: .env.example produced account_id='example'. "
            "_ENV_DISCOVERY_RESERVED in src/bot/data_loaders.py must filter it."
        )

    def test_env_bak_does_not_produce_account(self, tmp_path):
        from src.bot import data_loaders as dl

        (tmp_path / ".env").write_text("BYBIT_API_KEY=k\n")  # legitimate
        (tmp_path / ".env.bak").write_text("BYBIT_API_KEY=stale\n")  # backup file
        accounts = dl._load_env_accounts(repo_root=str(tmp_path))
        ids = [a["account_id"] for a in accounts]
        assert "bak" not in ids
        # Legitimate .env still produces the live account.
        assert "live" in ids

    def test_env_template_sample_dist_default_filtered(self, tmp_path):
        from src.bot import data_loaders as dl

        for reserved in (".env.template", ".env.sample", ".env.dist", ".env.default"):
            (tmp_path / reserved).write_text("API_KEY=x\n")
        accounts = dl._load_env_accounts(repo_root=str(tmp_path))
        ids = {a["account_id"] for a in accounts}
        assert ids.isdisjoint({"template", "sample", "dist", "default"})

    def test_real_env_account_still_discovered(self, tmp_path):
        """The filter must not block legitimate account env files."""
        from src.bot import data_loaders as dl

        (tmp_path / ".env.binance-sub-1").write_text(
            "BINANCE_API_KEY=k\nBINANCE_API_SECRET=s\n"
        )
        accounts = dl._load_env_accounts(repo_root=str(tmp_path))
        ids = [a["account_id"] for a in accounts]
        assert "binance-sub-1" in ids

    def test_reserved_set_includes_known_phantom_names(self):
        """The reserved set must contain the names that tripped S-012."""
        from src.bot.data_loaders import _ENV_DISCOVERY_RESERVED

        assert "example" in _ENV_DISCOVERY_RESERVED
        assert "bak" in _ENV_DISCOVERY_RESERVED


# ---------------------------------------------------------------------------
# Bot-side guardrail: toggle_service refuses unknown services
# ---------------------------------------------------------------------------


class TestToggleServiceValidatesUnitFile:
    def test_refuses_unknown_service(self, monkeypatch):
        """toggle_service() must refuse a service with no matching unit file."""
        import src.bot.telegram_query_bot as bot

        called = {"systemctl": False}

        def _fake_run(cmd, *a, **kw):
            called["systemctl"] = True
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""
            return _R()

        import src.bot.cloud_notifier as _cn
        monkeypatch.setattr(_cn.subprocess, "run", _fake_run)
        # Force a known set so the test is deterministic.
        monkeypatch.setattr(
            _cn, "_known_systemd_units",
            lambda repo_root=None: {"ict-trader-live", "ict-telegram-bot"},
        )

        result = bot.toggle_service("ict-trader-bak", "start")
        assert "Refusing" in result or "no matching unit" in result
        assert "ict-trader-bak" in result
        assert called["systemctl"] is False, (
            "toggle_service must NOT invoke systemctl when the unit is unknown"
        )

    def test_phantom_example_service_blocked(self, monkeypatch):
        """The exact symptom that triggered S-012: ict-trader-example refused."""
        import src.bot.telegram_query_bot as bot

        called = {"systemctl": False}

        def _fake_run(cmd, *a, **kw):
            called["systemctl"] = True
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""
            return _R()

        import src.bot.cloud_notifier as _cn
        monkeypatch.setattr(_cn.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            _cn, "_known_systemd_units",
            lambda repo_root=None: {"ict-trader-live", "ict-telegram-bot"},
        )

        result = bot.toggle_service("ict-trader-example", "start")
        assert "ict-trader-example" in result
        assert called["systemctl"] is False

    def test_known_service_still_proceeds(self, monkeypatch):
        """Real services pass through to systemctl as before."""
        import src.bot.telegram_query_bot as bot

        invocations = []

        def _fake_run(cmd, *a, **kw):
            invocations.append(cmd)
            class _R:
                returncode = 0
                stdout = "active\n"
                stderr = ""
            return _R()

        import src.bot.cloud_notifier as _cn
        monkeypatch.setattr(_cn.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            _cn, "_known_systemd_units",
            lambda repo_root=None: {"ict-trader-live", "ict-telegram-bot"},
        )

        result = bot.toggle_service("ict-trader-live", "start")
        # First invocation is the start (toggle); the second is is-active.
        assert any("start" in cmd for cmd in invocations)
        assert "ict-trader-live" in result

    def test_known_units_returns_canonical_set(self):
        """_known_systemd_units must read deploy/ directly."""
        from src.bot.cloud_notifier import _known_systemd_units

        units = _known_systemd_units()
        # Single-process architecture (PR D2): exactly one trader-side unit.
        assert "ict-trader-live" in units
        assert "ict-telegram-bot" in units
        # Phantom names must not be present.
        assert "ict-trader-example" not in units
        assert "ict-trader-bak" not in units
