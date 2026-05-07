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
        from src.bot import data_loaders as dl

        # Bypass the dotenv stub by replacing _read_env_file with a real
        # dict — the production module reads via dotenv_values which is
        # MagicMock'd at module-collection time in this test file.
        monkeypatch.setattr(
            dl, "_read_env_file",
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


# ---------------------------------------------------------------------------
# Fix 3 — get_strategy_label uses account["strategies"] from accounts.yaml
# ---------------------------------------------------------------------------


class TestGetStrategyLabelFromYaml:
    def test_single_strategy_account_uses_strategy_name(self, monkeypatch):
        """An account with a single strategy in its yaml entry shows that
        strategy's name as the label."""
        from src.bot import telegram_query_bot as bot

        account = {
            "account_id": "vwap_only",
            "env_path": None,
            "strategies": ["vwap"],
        }
        assert bot.get_strategy_label(account) == "VWAP"

    def test_single_turtle_soup_account(self):
        from src.bot import telegram_query_bot as bot

        account = {"env_path": None, "strategies": ["turtle_soup"]}
        assert bot.get_strategy_label(account) == "Turtle Soup"

    def test_multi_strategy_account_shows_multi(self):
        """The post-S-012 multiplexer norm: every account runs both
        turtle_soup + vwap → label is "Multi"."""
        from src.bot import telegram_query_bot as bot

        account = {
            "account_id": "bybit_1",
            "env_path": None,
            "strategies": ["turtle_soup", "vwap"],
        }
        assert bot.get_strategy_label(account) == "Multi"

    def test_env_strategy_wins_when_set(self, monkeypatch):
        """The .env STRATEGY override beats the YAML strategies list
        (legacy precedence preserved for env-discovered accounts)."""
        from src.bot import telegram_query_bot as bot

        # _account_env normally reads via dotenv_values, which is stubbed
        # at module-collection time in this test file. Replace it with a
        # plain dict matching what the production reader would return
        # for an env file containing STRATEGY=vwap.
        monkeypatch.setattr(bot, "_account_env", lambda acc: {"STRATEGY": "vwap"})
        account = {
            "env_path": "/tmp/fake.env",
            "strategies": ["turtle_soup"],
        }
        assert bot.get_strategy_label(account) == "VWAP"

    def test_yaml_unknown_strategy_falls_through_to_default(self):
        """A strategy not in _STRATEGY_DISPLAY falls through to the
        global env or default label."""
        from src.bot import telegram_query_bot as bot

        account = {"env_path": None, "strategies": ["weird_unmapped"]}
        # No env override → "Strategy" default.
        assert bot.get_strategy_label(account) == "Strategy"

    def test_no_account_no_env(self, monkeypatch):
        """No account argument, no STRATEGY env, no accounts → default."""
        from src.bot import telegram_query_bot as bot

        monkeypatch.setattr(bot.dl, "list_accounts", lambda: [])
        monkeypatch.delenv("STRATEGY", raising=False)
        assert bot.get_strategy_label() == "Strategy"
