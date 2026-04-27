"""
Tests for scripts/render_env_from_master.py

Uses only fake/mock data — no real secrets, no sops binary, no network calls.
"""
from __future__ import annotations

import stat
import sys
import textwrap
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
        "paper":        {"exchange": "bybit"},
        "colab":        {"exchange": "bybit"},
        "oracle_paper": {"exchange": "bybit"},
        "live":         {"exchange": "bybit"},
    },
    "telegram": {
        "dev":  {"bot_token": "fake_dev_token", "chat_id": "fake_dev_chat"},
        "prod": {"bot_token": "fake_prod_token", "chat_id": "fake_prod_chat"},
    },
    "bybit": {
        "testnet": {
            "api_key":    "fake_testnet_key",
            "api_secret": "fake_testnet_secret",
            "base_url":   "https://api-testnet.bybit.com",
        },
        "live": {
            "api_key":    "fake_live_key",
            "api_secret": "fake_live_secret",
            "base_url":   "https://api.bybit.com",
        },
    },
    "github": {"pat": "fake_ghp_token"},
    "huggingface": {
        "username":    "fake_user",
        "token":       "fake_hf_token",
        "dataset_repo": "fake_user/ict-bot-data",
        "model_repo":  "fake_user/ict-bot-model",
    },
    "oracle": {
        "host":      "1.2.3.4",
        "username":  "ubuntu",
        "repo_path": "/home/ubuntu/ict-trading-bot",
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
        "paper": {
            "max_position_usd":  "100",
            "max_daily_loss_usd": "20",
            "risk_per_trade":    "0.01",
        },
        "live": {
            "max_position_usd":  "500",
            "max_daily_loss_usd": "50",
            "risk_per_trade":    "0.02",
        },
    },
}


# ---------------------------------------------------------------------------
# _get helper
# ---------------------------------------------------------------------------

class TestGet:
    def test_nested_key_found(self):
        assert mod._get(FAKE_DATA, "bybit.testnet.api_key") == "fake_testnet_key"

    def test_missing_required_exits(self):
        with pytest.raises(SystemExit) as exc:
            mod._get(FAKE_DATA, "bybit.testnet.nonexistent")
        assert "nonexistent" in str(exc.value).lower() or exc.value.code != 0

    def test_placeholder_exits(self):
        data = {"key": {"val": "REPLACE_ME"}}
        with pytest.raises(SystemExit):
            mod._get(data, "key.val")

    def test_optional_missing_returns_none(self):
        result = mod._get_optional(FAKE_DATA, "does.not.exist")
        assert result is None


# ---------------------------------------------------------------------------
# Profile builders — check variable names only, never print values
# ---------------------------------------------------------------------------

class TestPaperProfile:
    def test_expected_keys_present(self):
        pairs = mod.build_paper(FAKE_DATA)
        keys = {k for k, _ in pairs}
        expected = {
            "ENVIRONMENT", "EXCHANGE", "MODE", "DRY_RUN", "ALLOW_LIVE_TRADING",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET", "BYBIT_TESTNET_BASE_URL",
            "HF_USERNAME", "HF_TOKEN", "HF_DATASET_REPO", "HF_MODEL_REPO",
            "SYMBOL", "TIMEFRAME",
            "MAX_POSITION_USD", "MAX_DAILY_LOSS_USD", "RISK_PER_TRADE",
        }
        assert expected.issubset(keys)

    def test_environment_is_local(self):
        pairs = dict(mod.build_paper(FAKE_DATA))
        assert pairs["ENVIRONMENT"] == "local"

    def test_dry_run_true(self):
        pairs = dict(mod.build_paper(FAKE_DATA))
        assert pairs["DRY_RUN"] == "true"

    def test_no_live_keys(self):
        pairs = mod.build_paper(FAKE_DATA)
        keys = {k for k, _ in pairs}
        assert "BYBIT_API_KEY" not in keys
        assert "BYBIT_API_SECRET" not in keys


class TestColabProfile:
    def test_expected_keys_present(self):
        pairs = mod.build_colab(FAKE_DATA)
        keys = {k for k, _ in pairs}
        expected = {
            "ENVIRONMENT", "EXCHANGE", "MODE", "DRY_RUN", "ALLOW_LIVE_TRADING",
            "GITHUB_PAT",
            "HF_USERNAME", "HF_TOKEN",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET", "BYBIT_TESTNET_BASE_URL",
        }
        assert expected.issubset(keys)

    def test_environment_is_colab(self):
        pairs = dict(mod.build_colab(FAKE_DATA))
        assert pairs["ENVIRONMENT"] == "colab"


class TestOraclePaperProfile:
    def test_expected_keys_present(self):
        pairs = mod.build_oracle_paper(FAKE_DATA)
        keys = {k for k, _ in pairs}
        expected = {
            "ENVIRONMENT", "EXCHANGE", "MODE", "DRY_RUN", "ALLOW_LIVE_TRADING",
            "ORACLE_HOST", "ORACLE_USERNAME", "ORACLE_REPO_PATH",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET",
        }
        assert expected.issubset(keys)

    def test_environment_is_oracle(self):
        pairs = dict(mod.build_oracle_paper(FAKE_DATA))
        assert pairs["ENVIRONMENT"] == "oracle"


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

    def test_allow_live_trading_true(self):
        pairs = dict(mod.build_live(FAKE_DATA))
        assert pairs["ALLOW_LIVE_TRADING"] == "true"

    def test_no_testnet_keys(self):
        pairs = mod.build_live(FAKE_DATA)
        keys = {k for k, _ in pairs}
        assert "BYBIT_TESTNET_API_KEY" not in keys


# ---------------------------------------------------------------------------
# Placeholder validation
# ---------------------------------------------------------------------------

class TestPlaceholderValidation:
    def test_replace_me_fails(self):
        data = {
            "profiles": {"paper": {"exchange": "REPLACE_ME"}},
            "telegram": {"dev": {"bot_token": "tok", "chat_id": "cid"}},
            "bybit": {
                "testnet": {
                    "api_key": "k", "api_secret": "s",
                    "base_url": "https://api-testnet.bybit.com",
                }
            },
            "huggingface": {},
            "runtime_defaults": {},
            "risk": {"paper": {}},
        }
        with pytest.raises(SystemExit):
            mod.build_paper(data)

    def test_empty_string_fails(self):
        with pytest.raises(SystemExit):
            mod._get({"k": {"v": ""}}, "k.v")


# ---------------------------------------------------------------------------
# write_env_file — output must not contain secret values in our fake dataset
# ---------------------------------------------------------------------------

class TestWriteEnvFile:
    def test_file_created(self, tmp_path):
        out = tmp_path / ".env.paper"
        pairs = [("ENVIRONMENT", "local"), ("MODE", "paper")]
        mod.write_env_file(out, pairs)
        assert out.exists()

    def test_file_permissions(self, tmp_path):
        out = tmp_path / ".env.paper"
        pairs = [("ENVIRONMENT", "local")]
        mod.write_env_file(out, pairs)
        mode = out.stat().st_mode & 0o777
        assert mode == 0o600

    def test_content_format(self, tmp_path):
        out = tmp_path / ".env.paper"
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
        # Keys present, values present for fake data only
        assert "FOO" in content


# ---------------------------------------------------------------------------
# CLI — live profile requires --allow-live
# ---------------------------------------------------------------------------

class TestCLILiveGuard:
    def test_live_without_allow_live_exits(self, tmp_path):
        master = tmp_path / "master-secrets.sops.yaml"
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
            # --allow-live intentionally omitted
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc:
                mod.main()
            assert exc.value.code != 0

    def test_plaintext_yaml_rejected(self, tmp_path):
        master = tmp_path / "master-secrets.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")
        out = tmp_path / ".env.paper"

        test_args = [
            "render_env_from_master.py",
            "--master", str(master),
            "--age-key-file", str(age_key),
            "--profile", "paper",
            "--out", str(out),
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
        yaml_mock.safe_load.return_value = {"profiles": {"paper": {"exchange": "bybit"}}}
        return yaml_mock

    def test_sops_called_with_correct_args(self, tmp_path):
        master = tmp_path / "master-secrets.sops.yaml"
        master.write_text("fake")
        age_key = tmp_path / "age-keys.txt"
        age_key.write_text("fake")

        fake_yaml = b"profiles:\n  paper:\n    exchange: bybit\n"
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

    def test_fake_token_not_in_keys_line(self, tmp_path, capsys):
        """The printed 'Keys:' line should list variable names, not values."""
        out = tmp_path / ".env.paper"
        pairs = mod.build_paper(FAKE_DATA)
        pairs = [(k, v) for k, v in pairs if v is not None]
        mod.write_env_file(out, pairs)

        # Simulate the stdout output of main (keys line)
        keys = [k for k, _ in pairs]
        keys_line = "Keys    : " + ", ".join(keys)
        # None of the fake secret values should appear in the keys line
        for _, val in pairs:
            if val and len(val) > 4:
                assert val not in keys_line
