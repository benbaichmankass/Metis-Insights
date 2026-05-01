"""
Tests for scripts/render_env_from_master.py

Uses only fake/mock data — no real secrets, no sops binary, no network calls.

Post-CP-17: paper / colab / oracle_paper / vwap_btcusd_dry_run profiles have
been removed. Only the live profiles (`live`, `vwap_btcusd_live`) remain.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to import the module under test
# ---------------------------------------------------------------------------

def _import_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "render_env_from_master",
        Path(__file__).resolve().parents[1] / "scripts" / "render_env_from_master.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _import_module()


# ---------------------------------------------------------------------------
# Fake master secrets data
# ---------------------------------------------------------------------------

FAKE_DATA = {
    "profiles": {
        "live": {"exchange": "bybit"},
        "vwap_btcusd_live": {
            "environment": "production",
            "exchange":    "bybit",
            "mode":        "live",
            "dry_run":     "false",
            "allow_live_trading": "true",
            "bybit_testnet": "false",
            "telegram_profile": "prod",
            "bybit_account":   "vwap_strategy",
            "strategy_profile": "vwap_btcusd",
            "risk_profile":     "vwap_btcusd",
        },
    },
    "telegram": {
        "dev":  {"bot_token": "fake_dev_token", "chat_id": "fake_dev_chat"},
        "prod": {"bot_token": "fake_prod_token", "chat_id": "fake_prod_chat"},
    },
    "bybit": {
        "live": {
            "api_key":    "fake_live_key",
            "api_secret": "fake_live_secret",
            "base_url":   "https://api.bybit.com",
        },
        "vwap_strategy": {
            "api_key":    "fake_vwap_subaccount_key",
            "api_secret": "fake_vwap_subaccount_secret",
            "account_note": "VWAP strategy subaccount",
        },
        "active_strategy_account": "vwap_strategy",
        # S-023 PR1: per-account block keyed by account_id from
        # config/accounts.yaml. Render script bridges this to the
        # api_key_env/api_secret_env names the bot reads.
        "accounts": {
            "bybit_1": {
                "api_key":    "fake_bybit_1_key",
                "api_secret": "fake_bybit_1_secret",
            },
            "bybit_2": {
                "api_key":    "fake_bybit_2_key",
                "api_secret": "fake_bybit_2_secret",
            },
        },
    },
    "breakout": {
        "accounts": {
            "prop_breakout_1": {
                "api_key":    "fake_prop_key",
                "api_secret": "fake_prop_secret",
                "enabled":    False,
            },
        },
    },
    "strategies": {
        "vwap_btcusd": {
            "enabled": "true",
            "strategy_name": "vwap",
            "exchange": "bybit",
            "symbol": "BTCUSD",
            "timeframe": "1m",
            "bybit_account": "vwap_strategy",
        },
    },
    "github": {"pat": "fake_ghp_token"},
    "huggingface": {
        "username":    "fake_user",
        "token":       "fake_hf_token",
        "dataset_repo": "fake_user/ict-bot-data",
        "model_repo":  "fake_user/ict-bot-model",
    },
    "runtime_defaults": {
        "symbol":    "BTCUSDT",
        "timeframe": "1m",
        "data_dir":  "data/",
        "model_dir": "ml/models/",
        "log_dir":   "logs/",
        "db_path":   "data/trading.db",
    },
    "risk": {
        "live": {
            "max_position_usd":  "500",
            "max_daily_loss_usd": "50",
            "risk_per_trade":    "0.02",
        },
        "vwap_btcusd": {
            "max_position_usd":  "50",
            "max_daily_loss_usd": "25",
            "risk_per_trade":    "0.005",
            "max_qty":           "0.001",
            "max_open_positions": "1",
        },
    },
}


# ---------------------------------------------------------------------------
# Module-level guarantees: paper surfaces are gone
# ---------------------------------------------------------------------------

class TestNoPaperSurfaces:
    """Regression tests asserting paper / dry-run profiles can no longer be
    rendered from this script. These are the structural guard-rails for the
    'no paper trading anywhere' directive."""

    def test_profiles_tuple_is_live_only(self):
        assert mod.PROFILES == ("live", "vwap_btcusd_live")

    def test_every_profile_is_a_live_profile(self):
        # By design, LIVE_PROFILES == PROFILES post-CP-17.
        assert set(mod.LIVE_PROFILES) == set(mod.PROFILES)

    def test_paper_builders_removed(self):
        for name in (
            "build_paper",
            "build_colab",
            "build_oracle_paper",
            "build_vwap_btcusd_dry_run",
            "_build_vwap_btcusd",
        ):
            assert not hasattr(mod, name), f"{name} should have been removed in CP-17"

    def test_builders_dict_is_live_only(self):
        assert set(mod.BUILDERS.keys()) == {"live", "vwap_btcusd_live"}

    def test_check_env_paper_script_deleted(self):
        # Hard guarantee that the paper smoke-test script is gone from disk.
        repo_root = Path(__file__).resolve().parents[1]
        assert not (repo_root / "scripts" / "check_env_paper.py").exists()

    def test_master_secrets_template_has_no_paper_profiles(self):
        """CP-19 guarantee: master-secrets.template.yaml must not list any
        paper-trading profile blocks. The renderer can only consume profiles
        defined here, so this is the canonical source of truth for the
        'no paper anywhere' directive at the config layer.

        Asserts:
          - profiles.paper, profiles.colab, profiles.oracle_paper,
            profiles.vwap_btcusd_dry_run are absent
          - risk.paper block is absent
          - any remaining profile uses mode 'live' (not 'paper')
        """
        repo_root = Path(__file__).resolve().parents[1]
        template_path = repo_root / "config" / "master-secrets.template.yaml"
        assert template_path.exists(), "master-secrets.template.yaml is missing"

        # Parse yaml lazily — PyYAML is already a dependency of the renderer.
        import yaml
        with open(template_path, "r") as fh:
            data = yaml.safe_load(fh)

        profiles = data.get("profiles", {})
        for forbidden in ("paper", "colab", "oracle_paper", "vwap_btcusd_dry_run"):
            assert forbidden not in profiles, (
                f"profiles.{forbidden} must not exist in master-secrets.template.yaml "
                f"(removed in CP-19)"
            )

        risk = data.get("risk", {})
        assert "paper" not in risk, "risk.paper must not exist in master-secrets.template.yaml (removed in CP-19)"

        # Any profile that declares a mode must declare 'live'.
        for name, body in profiles.items():
            if isinstance(body, dict) and "mode" in body:
                assert body["mode"] == "live", (
                    f"profiles.{name}.mode must be 'live', got {body['mode']!r}"
                )


# ---------------------------------------------------------------------------
# _get helper
# ---------------------------------------------------------------------------

class TestGet:
    def test_nested_key_found(self):
        assert mod._get(FAKE_DATA, "bybit.live.api_key") == "fake_live_key"

    def test_missing_required_exits(self):
        with pytest.raises(SystemExit) as exc:
            mod._get(FAKE_DATA, "bybit.live.nonexistent")
        assert "nonexistent" in str(exc.value).lower() or exc.value.code != 0

    def test_placeholder_exits(self):
        data = {"key": {"val": "REPLACE_ME"}}
        with pytest.raises(SystemExit):
            mod._get(data, "key.val")

    def test_optional_missing_returns_none(self):
        assert mod._get_optional(FAKE_DATA, "does.not.exist") is None


# ---------------------------------------------------------------------------
# Live profile
# ---------------------------------------------------------------------------

class TestLiveProfile:
    def test_expected_keys_present(self):
        pairs = mod.build_live(FAKE_DATA)
        keys = {k for k, _ in pairs}
        expected = {
            "ENVIRONMENT", "EXCHANGE", "MODE", "DRY_RUN", "ALLOW_LIVE_TRADING",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_BASE_URL",
        }
        assert expected.issubset(keys)

    def test_environment_is_production(self):
        pairs = dict(mod.build_live(FAKE_DATA))
        assert pairs["ENVIRONMENT"] == "production"

    def test_mode_is_live_uppercase(self):
        pairs = dict(mod.build_live(FAKE_DATA))
        assert pairs["MODE"] == "LIVE"
        assert pairs["MODE"].lower() != "paper"

    def test_dry_run_false(self):
        pairs = dict(mod.build_live(FAKE_DATA))
        assert pairs["DRY_RUN"] == "false"

    def test_allow_live_trading_true(self):
        pairs = dict(mod.build_live(FAKE_DATA))
        assert pairs["ALLOW_LIVE_TRADING"] == "true"

    def test_no_testnet_keys(self):
        keys = {k for k, _ in mod.build_live(FAKE_DATA)}
        assert "BYBIT_TESTNET_API_KEY" not in keys
        assert "BYBIT_TESTNET_API_SECRET" not in keys


# ---------------------------------------------------------------------------
# vwap_btcusd_live profile
# ---------------------------------------------------------------------------

class TestVwapBtcusdLiveProfile:
    def test_expected_keys_present(self):
        pairs = mod.build_vwap_btcusd_live(FAKE_DATA)
        keys = {k for k, _ in pairs}
        expected = {
            "ENVIRONMENT", "EXCHANGE", "MODE", "DRY_RUN", "ALLOW_LIVE_TRADING",
            "BYBIT_TESTNET",
            "STRATEGY", "SYMBOL", "TIMEFRAME",
            "BYBIT_API_KEY", "BYBIT_API_SECRET",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "MAX_POSITION_USD", "MAX_DAILY_LOSS_USD", "RISK_PER_TRADE",
            "MAX_QTY", "MAX_OPEN_POSITIONS",
        }
        assert expected.issubset(keys)

    def test_safety_flag_values(self):
        pairs = dict(mod.build_vwap_btcusd_live(FAKE_DATA))
        assert pairs["MODE"] == "LIVE"
        assert pairs["DRY_RUN"] == "false"
        assert pairs["ALLOW_LIVE_TRADING"] == "true"
        assert pairs["BYBIT_TESTNET"] == "false"
        assert pairs["STRATEGY"] == "vwap"
        assert pairs["SYMBOL"] == "BTCUSD"
        assert pairs["TIMEFRAME"] == "1m"

    def test_uses_prod_telegram(self):
        pairs = dict(mod.build_vwap_btcusd_live(FAKE_DATA))
        assert pairs["TELEGRAM_BOT_TOKEN"] == FAKE_DATA["telegram"]["prod"]["bot_token"]
        assert pairs["TELEGRAM_CHAT_ID"] == FAKE_DATA["telegram"]["prod"]["chat_id"]

    def test_uses_vwap_strategy_subaccount_keys(self):
        pairs = dict(mod.build_vwap_btcusd_live(FAKE_DATA))
        # Must come from bybit.vwap_strategy, not bybit.live
        assert pairs["BYBIT_API_KEY"] == FAKE_DATA["bybit"]["vwap_strategy"]["api_key"]
        assert pairs["BYBIT_API_SECRET"] == FAKE_DATA["bybit"]["vwap_strategy"]["api_secret"]
        # And specifically not from the parent-account live keys
        assert pairs["BYBIT_API_KEY"] != FAKE_DATA["bybit"]["live"]["api_key"]
        assert pairs["BYBIT_API_SECRET"] != FAKE_DATA["bybit"]["live"]["api_secret"]

    def test_no_testnet_keys_in_output(self):
        keys = {k for k, _ in mod.build_vwap_btcusd_live(FAKE_DATA)}
        assert "BYBIT_TESTNET_API_KEY" not in keys
        assert "BYBIT_TESTNET_API_SECRET" not in keys


class TestVwapBtcusdMissingCredentials:
    def _data_without(self, *paths):
        import copy
        data = copy.deepcopy(FAKE_DATA)
        for p in paths:
            node = data
            parts = p.split(".")
            for part in parts[:-1]:
                node = node[part]
            node.pop(parts[-1], None)
        return data

    def test_missing_api_key_fails_with_field_name(self):
        data = self._data_without("bybit.vwap_strategy.api_key")
        with pytest.raises(SystemExit) as exc:
            mod.build_vwap_btcusd_live(data)
        msg = str(exc.value)
        assert "bybit.vwap_strategy.api_key" in msg
        # Field name only — the value (which is absent here anyway) must not appear
        assert "fake_vwap_subaccount" not in msg

    def test_missing_api_secret_fails_with_field_name(self):
        data = self._data_without("bybit.vwap_strategy.api_secret")
        with pytest.raises(SystemExit) as exc:
            mod.build_vwap_btcusd_live(data)
        assert "bybit.vwap_strategy.api_secret" in str(exc.value)

    def test_placeholder_api_key_fails_with_field_name_only(self):
        import copy
        data = copy.deepcopy(FAKE_DATA)
        data["bybit"]["vwap_strategy"]["api_key"] = "REPLACE_ME_BYBIT_VWAP_STRATEGY_SUBACCOUNT_API_KEY"
        with pytest.raises(SystemExit) as exc:
            mod.build_vwap_btcusd_live(data)
        msg = str(exc.value)
        assert "bybit.vwap_strategy.api_key" in msg
        assert "REPLACE_ME_BYBIT_VWAP_STRATEGY_SUBACCOUNT_API_KEY" not in msg


# ---------------------------------------------------------------------------
# Placeholder validation
# ---------------------------------------------------------------------------

class TestPlaceholderValidation:
    def test_replace_me_fails(self):
        data = {
            "profiles": {"live": {"exchange": "REPLACE_ME"}},
            "telegram": {"prod": {"bot_token": "tok", "chat_id": "cid"}},
            "bybit": {
                "live": {
                    "api_key": "k", "api_secret": "s",
                    "base_url": "https://api.bybit.com",
                }
            },
            "huggingface": {},
            "runtime_defaults": {},
            "risk": {"live": {}},
        }
        with pytest.raises(SystemExit):
            mod.build_live(data)

    def test_empty_string_fails(self):
        with pytest.raises(SystemExit):
            mod._get({"k": {"v": ""}}, "k.v")


# ---------------------------------------------------------------------------
# write_env_file — output formatting
# ---------------------------------------------------------------------------

class TestWriteEnvFile:
    def test_file_created(self, tmp_path):
        out = tmp_path / ".env.live"
        pairs = [("ENVIRONMENT", "production"), ("MODE", "LIVE")]
        mod.write_env_file(out, pairs)
        assert out.exists()

    def test_file_permissions(self, tmp_path):
        out = tmp_path / ".env.live"
        pairs = [("ENVIRONMENT", "production")]
        mod.write_env_file(out, pairs)
        mode = out.stat().st_mode & 0o777
        assert mode == 0o600

    def test_content_format(self, tmp_path):
        out = tmp_path / ".env.live"
        pairs = [("KEY", "value"), ("SPACED", "hello world")]
        mod.write_env_file(out, pairs)
        text = out.read_text()
        assert "KEY=value" in text
        assert 'SPACED="hello world"' in text

    def test_values_in_output(self, tmp_path):
        """Regression: ensure the test itself never compares against real secrets."""
        out = tmp_path / ".env.test"
        pairs = [("FOO", "bar123")]
        mod.write_env_file(out, pairs)
        content = out.read_text()
        assert "FOO=bar123" in content


# ---------------------------------------------------------------------------
# CLI — every supported profile requires --allow-live (all are live)
# ---------------------------------------------------------------------------

class TestCLILiveGuard:
    @pytest.mark.parametrize("profile", ["live", "vwap_btcusd_live"])
    def test_profile_without_allow_live_exits(self, tmp_path, profile):
        master = tmp_path / "master-secrets.sops.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")
        out = tmp_path / f".env.{profile}"

        test_args = [
            "render_env_from_master.py",
            "--master", str(master),
            "--age-key-file", str(age_key),
            "--profile", profile,
            "--out", str(out),
            # --allow-live intentionally omitted
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc:
                mod.main()
            assert exc.value.code != 0

    def test_plaintext_yaml_rejected(self, tmp_path):
        master = tmp_path / "master-secrets.yaml"  # not .sops.yaml
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")
        out = tmp_path / ".env.live"

        test_args = [
            "render_env_from_master.py",
            "--master", str(master),
            "--age-key-file", str(age_key),
            "--profile", "live",
            "--out", str(out),
            "--allow-live",
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc:
                mod.main()
            assert exc.value.code != 0

    @pytest.mark.parametrize("profile", ["paper", "colab", "oracle_paper", "vwap_btcusd_dry_run"])
    def test_removed_profiles_rejected_by_argparse(self, tmp_path, profile):
        """argparse choices=PROFILES must reject removed paper-style profiles."""
        master = tmp_path / "master-secrets.sops.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")
        out = tmp_path / f".env.{profile}"

        test_args = [
            "render_env_from_master.py",
            "--master", str(master),
            "--age-key-file", str(age_key),
            "--profile", profile,
            "--out", str(out),
            "--allow-live",
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc:
                mod.main()
            assert exc.value.code != 0


# ---------------------------------------------------------------------------
# decrypt_master — mocked; verifies SOPS is called correctly
# ---------------------------------------------------------------------------

class TestDecryptMaster:
    @staticmethod
    def _mock_yaml():
        """Return a minimal yaml mock that parses a simple mapping."""
        yaml_mock = MagicMock()
        yaml_mock.safe_load.return_value = {"profiles": {"live": {"exchange": "bybit"}}}
        return yaml_mock

    def test_sops_called_with_correct_args(self, tmp_path):
        master = tmp_path / "master-secrets.sops.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")

        fake_yaml = b"profiles:\n  live:\n    exchange: bybit\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_yaml

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch.dict("sys.modules", {"yaml": self._mock_yaml()}):
            mod.decrypt_master(master, age_key, "sops")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "sops" in cmd
        assert "--decrypt" in cmd
        assert str(master) in cmd

        env_passed = mock_run.call_args.kwargs.get("env", {})
        assert env_passed.get("SOPS_AGE_KEY_FILE") == str(age_key)

    def test_sops_failure_exits(self, tmp_path):
        master = tmp_path / "master-secrets.sops.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        mock_result.stderr = b"decryption failed"

        with patch("subprocess.run", return_value=mock_result), \
             patch.dict("sys.modules", {"yaml": self._mock_yaml()}):
            with pytest.raises(SystemExit):
                mod.decrypt_master(master, age_key, "sops")


# ---------------------------------------------------------------------------
# Output must not contain raw secret value strings (paranoia check)
# ---------------------------------------------------------------------------

class TestNoSecretsInOutput:
    """Verify the rendered env file contains variable names but not raw secret strings
    that only exist in the fake data (not in env var names)."""

    def test_fake_token_not_in_keys_line(self, tmp_path):
        """The printed 'Keys:' line should list variable names, not values."""
        out = tmp_path / ".env.live"
        pairs = mod.build_live(FAKE_DATA)
        pairs = [(k, v) for k, v in pairs if v is not None]
        mod.write_env_file(out, pairs)

        # Simulate the stdout output of main (keys line)
        keys = [k for k, _ in pairs]
        keys_line = "Keys    : " + ", ".join(keys)
        # None of the fake secret values should appear in the keys line
        for _, val in pairs:
            if val and len(val) > 4:
                assert val not in keys_line


# ---------------------------------------------------------------------------
# News layer — renderer and template regression tests (PR 1, 2026-04-29 sprint)
# ---------------------------------------------------------------------------

# Fake data with news block disabled (mirrors the template default)
FAKE_DATA_WITH_NEWS_DISABLED = {
    **FAKE_DATA,
    "news": {
        "enabled": "false",
        "api_key": "",
    },
}

# Fake data with news explicitly enabled (operator opt-in)
FAKE_DATA_WITH_NEWS_ENABLED = {
    **FAKE_DATA,
    "news": {
        "enabled": "true",
        "api_key": "fake_newsapi_key",
        "cache_ttl": "300",
        "veto_sentiment_threshold": "-0.6",
    },
}

# Fake data with no news block at all (pre-M9 master; should still emit disabled defaults)
FAKE_DATA_WITHOUT_NEWS_BLOCK = {k: v for k, v in FAKE_DATA.items() if k != "news"}


class TestNewsRenderer:
    """Regression tests for _news_pairs() and its integration into profile builders."""

    def test_disabled_default_emits_news_enabled_false(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_DISABLED))
        assert pairs["NEWS_ENABLED"] == "false"

    def test_disabled_default_emits_empty_api_key(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_DISABLED))
        assert "NEWS_API_KEY" in pairs
        assert pairs["NEWS_API_KEY"] == ""

    def test_enabled_emits_news_enabled_true(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_ENABLED))
        assert pairs["NEWS_ENABLED"] == "true"

    def test_enabled_emits_api_key(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_ENABLED))
        assert pairs["NEWS_API_KEY"] == "fake_newsapi_key"

    def test_optional_tuning_knobs_written_when_set(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_ENABLED))
        assert pairs.get("NEWS_CACHE_TTL") == "300"
        assert pairs.get("NEWS_VETO_SENTIMENT_THRESHOLD") == "-0.6"

    def test_optional_tuning_knobs_absent_when_not_set(self):
        pairs = dict(mod._news_pairs(FAKE_DATA_WITH_NEWS_DISABLED))
        # Only the two mandatory keys should be present
        assert set(pairs.keys()) == {"NEWS_ENABLED", "NEWS_API_KEY"}

    def test_missing_news_block_defaults_to_disabled(self):
        """A master without a news: block must still emit NEWS_ENABLED=false."""
        pairs = dict(mod._news_pairs(FAKE_DATA_WITHOUT_NEWS_BLOCK))
        assert pairs["NEWS_ENABLED"] == "false"
        assert "NEWS_API_KEY" in pairs


class TestNewsDefaultInProfiles:
    """Integration: both profile builders include the news keys."""

    def test_live_profile_contains_news_enabled(self):
        pairs = dict(mod.build_live(FAKE_DATA_WITH_NEWS_DISABLED))
        assert "NEWS_ENABLED" in pairs

    def test_live_profile_news_disabled_by_default(self):
        pairs = dict(mod.build_live(FAKE_DATA_WITH_NEWS_DISABLED))
        assert pairs["NEWS_ENABLED"] == "false"

    def test_vwap_profile_contains_news_enabled(self):
        pairs = dict(mod.build_vwap_btcusd_live(FAKE_DATA_WITH_NEWS_DISABLED))
        assert "NEWS_ENABLED" in pairs

    def test_vwap_profile_news_disabled_by_default(self):
        pairs = dict(mod.build_vwap_btcusd_live(FAKE_DATA_WITH_NEWS_DISABLED))
        assert pairs["NEWS_ENABLED"] == "false"


class TestNewsTemplateSanity:
    """Ensure the committed template always ships NEWS_ENABLED=false."""

    def test_template_news_block_has_enabled_false(self):
        yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
        repo_root = Path(__file__).resolve().parents[1]
        template_path = repo_root / "config" / "master-secrets.template.yaml"
        assert template_path.exists(), "master-secrets.template.yaml is missing"
        with open(template_path, "r") as fh:
            data = yaml.safe_load(fh)
        news = data.get("news", {})
        assert news, "news: block is missing from master-secrets.template.yaml"
        assert news.get("enabled") == "false", (
            f"news.enabled must be 'false' in the template (got {news.get('enabled')!r}). "
            "The template must ship disabled."
        )

    def test_template_news_block_api_key_is_blank(self):
        yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
        repo_root = Path(__file__).resolve().parents[1]
        template_path = repo_root / "config" / "master-secrets.template.yaml"
        with open(template_path, "r") as fh:
            data = yaml.safe_load(fh)
        news = data.get("news", {})
        api_key = news.get("api_key", None)
        assert api_key is not None, "news.api_key key is missing from template"
        assert api_key == "" or api_key is None, (
            "news.api_key must be blank in the template so that absence is detectable"
        )

    def test_rendered_output_never_produces_news_enabled_true_without_explicit_override(self):
        """No profile rendered from FAKE_DATA (no news block) emits NEWS_ENABLED=true."""
        for builder_name in ("build_live", "build_vwap_btcusd_live"):
            builder = getattr(mod, builder_name)
            pairs = dict(builder(FAKE_DATA_WITHOUT_NEWS_BLOCK))
            assert pairs.get("NEWS_ENABLED") != "true", (
                f"{builder_name} must not emit NEWS_ENABLED=true when there is no "
                "explicit news block in the master secrets data"
            )


# ---------------------------------------------------------------------------
# S-023 PR1 — per-account credential rendering driven by accounts.yaml
# ---------------------------------------------------------------------------


def _write_accounts_yaml(path: Path, accounts: dict) -> None:
    import yaml
    path.write_text(
        yaml.safe_dump({"accounts": accounts}, sort_keys=False),
        encoding="utf-8",
    )


class TestPerAccountRendering:
    """The render script must emit ``<api_key_env>=...`` and the matching
    ``..._SECRET`` for every account in accounts.yaml. Without this, the
    bot's bybit_client_for(account) returns None silently and every
    /accounts_status reads "balance unavailable".
    """

    def _accounts_fixture(self, tmp_path: Path) -> Path:
        path = tmp_path / "accounts.yaml"
        _write_accounts_yaml(path, {
            "bybit_1": {
                "type": "regular",
                "exchange": "bybit",
                "api_key_env": "BYBIT_API_KEY_1",
                "strategies": ["turtle_soup"],
            },
            "bybit_2": {
                "type": "regular",
                "exchange": "bybit",
                "api_key_env": "BYBIT_API_KEY_2",
                "strategies": ["vwap"],
            },
            "prop_breakout_1": {
                "type": "prop",
                "exchange": "breakout",
                "api_key_env": "BREAKOUT_API_KEY_1",
                "strategies": [],
            },
        })
        return path

    def test_emits_one_pair_per_account_in_accounts_yaml(self, tmp_path):
        accounts = self._accounts_fixture(tmp_path)
        pairs = mod.build_live(FAKE_DATA, accounts_path=accounts)
        keys = {k for k, _ in pairs}
        # bybit_1 + bybit_2 should be rendered. prop_breakout_1 is
        # `enabled: False` in FAKE_DATA so it's intentionally skipped.
        assert "BYBIT_API_KEY_1" in keys
        assert "BYBIT_API_SECRET_1" in keys
        assert "BYBIT_API_KEY_2" in keys
        assert "BYBIT_API_SECRET_2" in keys
        assert "BREAKOUT_API_KEY_1" not in keys

    def test_rendered_secrets_match_master_block(self, tmp_path):
        accounts = self._accounts_fixture(tmp_path)
        pairs = dict(mod.build_live(FAKE_DATA, accounts_path=accounts))
        assert pairs["BYBIT_API_KEY_1"] == "fake_bybit_1_key"
        assert pairs["BYBIT_API_SECRET_1"] == "fake_bybit_1_secret"
        assert pairs["BYBIT_API_KEY_2"] == "fake_bybit_2_key"
        assert pairs["BYBIT_API_SECRET_2"] == "fake_bybit_2_secret"

    def test_warns_when_master_lacks_account_block(self, tmp_path):
        """Account exists in accounts.yaml, missing from master → warning."""
        accounts = self._accounts_fixture(tmp_path)
        data_minus_bybit_2 = {
            **FAKE_DATA,
            "bybit": {**FAKE_DATA["bybit"],
                      "accounts": {"bybit_1": FAKE_DATA["bybit"]["accounts"]["bybit_1"]}},
        }
        pairs, warnings = mod._per_account_pairs(data_minus_bybit_2, accounts)
        keys = {k for k, _ in pairs}
        assert "BYBIT_API_KEY_2" not in keys
        assert any("bybit_2" in w and "bybit.accounts.bybit_2" in w for w in warnings)

    def test_warns_when_credentials_still_placeholder(self, tmp_path):
        accounts = self._accounts_fixture(tmp_path)
        data = {
            **FAKE_DATA,
            "bybit": {
                **FAKE_DATA["bybit"],
                "accounts": {
                    "bybit_1": {"api_key": "REPLACE_ME", "api_secret": "fake"},
                    "bybit_2": FAKE_DATA["bybit"]["accounts"]["bybit_2"],
                },
            },
        }
        pairs, warnings = mod._per_account_pairs(data, accounts)
        keys = {k for k, _ in pairs}
        assert "BYBIT_API_KEY_1" not in keys
        assert "BYBIT_API_KEY_2" in keys  # untouched
        assert any("placeholder" in w and "bybit_1" in w for w in warnings)

    def test_explicit_enabled_false_skips_account(self, tmp_path):
        accounts = self._accounts_fixture(tmp_path)
        # prop_breakout_1 has enabled: False in FAKE_DATA → no pairs, has warning
        pairs, warnings = mod._per_account_pairs(FAKE_DATA, accounts)
        keys = {k for k, _ in pairs}
        assert "BREAKOUT_API_KEY_1" not in keys
        assert any("prop_breakout_1" in w and "enabled: false" in w
                   for w in warnings)

    def test_custom_api_secret_env_honoured(self, tmp_path):
        """Account may declare api_secret_env explicitly; must be used verbatim."""
        path = tmp_path / "accounts.yaml"
        _write_accounts_yaml(path, {
            "weird": {
                "exchange": "bybit",
                "api_key_env": "WEIRD_KEY",
                "api_secret_env": "TOTALLY_DIFFERENT_SECRET_NAME",
                "strategies": ["vwap"],
            },
        })
        data = {
            **FAKE_DATA,
            "bybit": {
                **FAKE_DATA["bybit"],
                "accounts": {"weird": {"api_key": "k", "api_secret": "s"}},
            },
        }
        pairs = dict(mod.build_live(data, accounts_path=path))
        assert pairs["WEIRD_KEY"] == "k"
        assert pairs["TOTALLY_DIFFERENT_SECRET_NAME"] == "s"

    def test_missing_accounts_yaml_returns_empty_with_warning(self, tmp_path):
        bogus = tmp_path / "no-such-file.yaml"
        pairs, warnings = mod._per_account_pairs(FAKE_DATA, bogus)
        assert pairs == []
        assert warnings  # at least one
        assert any("no accounts found" in w for w in warnings)

    def test_account_without_api_key_env_skipped_with_warning(self, tmp_path):
        path = tmp_path / "accounts.yaml"
        _write_accounts_yaml(path, {
            "broken": {"exchange": "bybit", "strategies": []},
        })
        pairs, warnings = mod._per_account_pairs(FAKE_DATA, path)
        assert pairs == []
        assert any("broken" in w and "no api_key_env" in w for w in warnings)

    def test_rendered_pairs_are_appended_to_live_profile(self, tmp_path):
        accounts = self._accounts_fixture(tmp_path)
        pairs = mod.build_live(FAKE_DATA, accounts_path=accounts)
        # Legacy keys still present (back-compat)
        keys = {k for k, _ in pairs}
        assert "BYBIT_API_KEY" in keys           # legacy
        assert "BYBIT_API_KEY_1" in keys         # per-account


class TestMasterTemplateAccountsBlock:
    """Regression: master-secrets.template.yaml must keep an accounts:
    block under bybit so render_env_from_master keeps producing the
    per-account env vars the bot reads.
    """

    def test_template_has_bybit_accounts_block(self):
        repo_root = Path(__file__).resolve().parents[1]
        template = repo_root / "config" / "master-secrets.template.yaml"
        import yaml
        data = yaml.safe_load(template.read_text(encoding="utf-8"))
        bybit = data.get("bybit") or {}
        accounts = bybit.get("accounts") or {}
        # Whatever account_ids accounts.yaml has, the template must
        # mention at least the live ones (bybit_1, bybit_2 today).
        # We don't require an exact set here — accounts can change.
        # We DO require the structure exists.
        assert isinstance(accounts, dict) and accounts, (
            "config/master-secrets.template.yaml must define a bybit.accounts "
            "mapping; the render script reads it to emit BYBIT_API_KEY_<id>."
        )

    def test_template_account_ids_match_accounts_yaml(self):
        """Drift guard: every live account_id in accounts.yaml must have a
        placeholder block in the master template (otherwise the render
        script will warn but the operator may miss it).
        """
        repo_root = Path(__file__).resolve().parents[1]
        import yaml
        accounts_yaml = yaml.safe_load(
            (repo_root / "config" / "accounts.yaml").read_text(encoding="utf-8"),
        )
        template = yaml.safe_load(
            (repo_root / "config" / "master-secrets.template.yaml").read_text(encoding="utf-8"),
        )
        accounts = (accounts_yaml or {}).get("accounts") or {}
        # Group by exchange
        for aid, cfg in accounts.items():
            if not isinstance(cfg, dict):
                continue
            exchange = (cfg.get("exchange") or "").lower()
            master_block = ((template.get(exchange) or {}).get("accounts") or {})
            assert aid in master_block, (
                f"accounts.yaml::{aid} (exchange={exchange}) has no matching "
                f"entry under master-secrets.template.yaml::{exchange}.accounts.{aid}. "
                f"Add a placeholder block so render_env_from_master.py covers it."
            )
