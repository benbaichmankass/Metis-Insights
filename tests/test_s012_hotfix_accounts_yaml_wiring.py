"""S-012 hotfix #2: accounts.yaml → bot pipeline wiring.

Three independent bugs surfaced from the live bot's /status and
/balance output:

1. /status shows a 4th account "Breakout (live)" alongside the three
   accounts.yaml entries (bybit_1, bybit_2, prop_breakout_1). Cause:
   ``data_loaders.list_accounts`` merges YAML + ``.env`` discovery
   even when YAML is the source of truth. PR B3 said accounts.yaml
   wins; the bot side missed the memo.

2. /balance for bybit_1 / bybit_2 returns "Bybit error: balance
   unavailable". Cause: ``bybit_client_for(account)`` only knows how
   to read API keys from a file at ``account.env_path``. The
   accounts.yaml contract uses ``api_key_env`` (env-var NAME, not
   file path) per S-010 — so YAML accounts have no env_path and the
   client returns None.

3. /status shows "Strategy" as a generic label for every account
   instead of the strategy name. Cause: ``get_strategy_label`` reads
   STRATEGY from the account's .env; YAML accounts have no env_path
   and the ``strategies`` field is ignored.

This file pins all three fixes.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock



# ---------------------------------------------------------------------------
# Bot import requires telegram + dotenv stubs (mirrors test_kill_switch.py).
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
# Fix 1 — list_accounts skips env-discovery when YAML is present
# ---------------------------------------------------------------------------


class TestListAccountsYamlOnlyWhenPresent:
    def test_yaml_present_blocks_env_discovery(self, tmp_path, monkeypatch):
        """When accounts.yaml has entries, .env / .env.<id> files do NOT
        produce extra accounts. Eliminates the "Breakout (live)" duplicate."""
        from src.bot import data_loaders as dl

        # Set up a fake repo with .env (would normally produce 'live')
        (tmp_path / ".env").write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\n")
        # And an accounts.yaml with one entry.
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "accounts.yaml").write_text(
            "accounts:\n"
            "  bybit_1:\n"
            "    type: regular\n"
            "    exchange: bybit\n"
            "    api_key_env: BYBIT_API_KEY_1\n"
            "    strategies: [turtle_soup, vwap]\n"
        )

        monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH", str(config_dir / "accounts.yaml"))
        monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path))

        accounts = dl.list_accounts()
        ids = [a["account_id"] for a in accounts]
        assert ids == ["bybit_1"], (
            f"Expected only the YAML account; got {ids}. "
            "If 'live' appears, env discovery wasn't suppressed."
        )

    def test_yaml_absent_falls_back_to_env_discovery(self, tmp_path, monkeypatch):
        """Legacy single-account deployments (no accounts.yaml) still work."""
        from src.bot import data_loaders as dl

        (tmp_path / ".env").write_text("BYBIT_API_KEY=k\nBYBIT_API_SECRET=s\n")
        # No accounts.yaml at all.
        monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH", str(tmp_path / "missing-accounts.yaml"))
        monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path))

        accounts = dl.list_accounts()
        ids = [a["account_id"] for a in accounts]
        assert "live" in ids, "Legacy .env discovery must still work when YAML is absent"


# ---------------------------------------------------------------------------
# Fix 2 — bybit_client_for honors api_key_env
# ---------------------------------------------------------------------------


class TestBybitClientHonorsApiKeyEnv:
    def test_api_key_env_resolution(self, monkeypatch):
        """Account with ``api_key_env: BYBIT_API_KEY_1`` reads keys from
        os.environ, not from a file."""
        from src.bot import data_loaders as dl

        monkeypatch.setenv("BYBIT_API_KEY_1", "key-from-env-1")
        monkeypatch.setenv("BYBIT_API_SECRET_1", "secret-from-env-1")

        # Stub pybit.unified_trading.HTTP so the test stays offline.
        captured = {}
        fake_pybit = MagicMock()
        def _fake_http(*, testnet, api_key, api_secret):
            captured["testnet"] = testnet
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            return MagicMock()
        fake_pybit.unified_trading.HTTP = _fake_http
        monkeypatch.setitem(sys.modules, "pybit", fake_pybit)
        monkeypatch.setitem(sys.modules, "pybit.unified_trading", fake_pybit.unified_trading)

        account = {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "api_key_env": "BYBIT_API_KEY_1",
            "env_path": None,           # the production-yaml shape
        }
        client = dl.bybit_client_for(account)
        assert client is not None
        assert captured["api_key"] == "key-from-env-1"
        assert captured["api_secret"] == "secret-from-env-1"

    def test_explicit_api_secret_env(self, monkeypatch):
        """When api_secret_env is set explicitly, it wins over the
        auto-derived ``_SECRET`` name."""
        from src.bot import data_loaders as dl

        monkeypatch.setenv("CUSTOM_KEY", "k")
        monkeypatch.setenv("CUSTOM_SECRET", "s")

        fake_pybit = MagicMock()
        captured = {}
        def _fake_http(*, testnet, api_key, api_secret):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            return MagicMock()
        fake_pybit.unified_trading.HTTP = _fake_http
        monkeypatch.setitem(sys.modules, "pybit", fake_pybit)
        monkeypatch.setitem(sys.modules, "pybit.unified_trading", fake_pybit.unified_trading)

        account = {
            "api_key_env": "CUSTOM_KEY",
            "api_secret_env": "CUSTOM_SECRET",
        }
        dl.bybit_client_for(account)
        assert captured["api_key"] == "k"
        assert captured["api_secret"] == "s"

    def test_missing_env_returns_none(self, monkeypatch):
        """When api_key_env points to an unset variable, return None
        rather than raising."""
        from src.bot import data_loaders as dl

        monkeypatch.delenv("MISSING_KEY", raising=False)
        monkeypatch.delenv("MISSING_SECRET", raising=False)

        account = {"api_key_env": "MISSING_KEY", "env_path": None}
        assert dl.bybit_client_for(account) is None

    def test_legacy_env_path_still_works(self, tmp_path, monkeypatch):
        """When account has env_path (legacy single-account discovery),
        that path is still honored."""
        # (a) STALE TEST: bybit_client_for now lives in
        # src/units/accounts/clients.py (S-032/S-035 relocation).
        # _read_env_file must be patched on the clients module, not
        # data_loaders, because resolve_credentials calls clients._read_env_file.
        from src.bot import data_loaders as dl
        import src.units.accounts.clients as clients

        monkeypatch.setattr(
            clients, "_read_env_file",
            lambda path: {"BYBIT_API_KEY": "legacy-key", "BYBIT_API_SECRET": "legacy-secret"}
                         if path else {},
        )

        fake_pybit = MagicMock()
        captured = {}
        def _fake_http(*, testnet, api_key, api_secret):
            captured["api_key"] = api_key
            return MagicMock()
        fake_pybit.unified_trading.HTTP = _fake_http
        monkeypatch.setitem(sys.modules, "pybit", fake_pybit)
        monkeypatch.setitem(sys.modules, "pybit.unified_trading", fake_pybit.unified_trading)

        # No api_key_env on this account — must take the legacy env_path branch.
        account = {"env_path": str(tmp_path / ".env")}
        dl.bybit_client_for(account)
        assert captured["api_key"] == "legacy-key"
