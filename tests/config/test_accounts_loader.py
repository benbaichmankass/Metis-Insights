"""Tests for ``src/config/accounts_loader.py``.

The canonical reader for ``config/accounts.yaml``. The schema is
fixed: a top-level ``accounts:`` dict keyed by account_id. The most
important property is that the real production file parses correctly
— a regression test against the live ``config/accounts.yaml`` is the
load-bearing one here.
"""
from __future__ import annotations

from pathlib import Path

from src.config.accounts_loader import (
    DEFAULT_ACCOUNTS_YAML,
    load_accounts_dict,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestRealYaml:
    """Regression against the committed ``config/accounts.yaml`` —
    the file the production trader and dashboards consume."""

    def test_default_path_loads_existing_accounts(self):
        cfgs = load_accounts_dict()
        # bybit_1 + bybit_2 are the canonical two accounts; the file
        # may grow but neither should ever disappear silently.
        assert "bybit_1" in cfgs
        assert "bybit_2" in cfgs

    def test_each_cfg_is_a_dict_with_required_fields(self):
        cfgs = load_accounts_dict()
        for name, cfg in cfgs.items():
            assert isinstance(cfg, dict), f"{name} cfg is not a dict"
            # exchange + mode are the load-bearing identity / routing
            # fields. market_type is optional (prop / FX accounts may
            # have no market type; bybit accounts default to "spot" via
            # the consumer's normalisation).
            assert "exchange" in cfg, f"{name} missing exchange"
            assert "mode" in cfg, f"{name} missing mode"


class TestDictShape:
    """The reader's contract: dict shape only. Anything else returns
    empty so the dashboard / runtime-status writer / ops script can
    fall back gracefully."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "accounts.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_canonical_dict_form(self, tmp_path, monkeypatch):
        path = self._write(tmp_path, (
            "accounts:\n"
            "  bybit_1:\n"
            "    exchange: bybit\n"
            "    market_type: spot\n"
            "  bybit_2:\n"
            "    exchange: bybit\n"
            "    market_type: linear\n"
        ))
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert set(cfgs) == {"bybit_1", "bybit_2"}
        assert cfgs["bybit_2"]["market_type"] == "linear"

    def test_list_form_yaml_returns_empty(self, tmp_path, monkeypatch):
        """Older fixtures used a list-of-dicts shape. The canonical
        production shape (S-012 PR B3) is dict-only; anything else
        is treated as malformed and the reader returns empty rather
        than silently misparsing."""
        path = self._write(tmp_path, (
            "accounts:\n"
            "  - account_id: bybit_1\n"
            "    exchange: bybit\n"
        ))
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert cfgs == {}

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(tmp_path / "does-not-exist.yaml")
        assert cfgs == {}

    def test_malformed_yaml_returns_empty(self, tmp_path, monkeypatch):
        path = self._write(tmp_path, "accounts:\n  : :")
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert cfgs == {}

    def test_no_accounts_key_returns_empty(self, tmp_path, monkeypatch):
        path = self._write(tmp_path, "strategies:\n  vwap:\n    enabled: true\n")
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert cfgs == {}

    def test_non_dict_entries_skipped(self, tmp_path, monkeypatch):
        path = self._write(tmp_path, (
            "accounts:\n"
            "  bybit_1:\n"
            "    exchange: bybit\n"
            "  bad_entry: not_a_dict\n"
        ))
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert set(cfgs) == {"bybit_1"}


class TestPathResolution:
    """Resolution order: ACCOUNTS_YAML_PATH env > positional arg > default."""

    def test_env_var_wins_over_arg(self, tmp_path, monkeypatch):
        env_path = tmp_path / "from_env.yaml"
        env_path.write_text(
            "accounts:\n  via_env:\n    exchange: bybit\n",
            encoding="utf-8",
        )
        arg_path = tmp_path / "from_arg.yaml"
        arg_path.write_text(
            "accounts:\n  via_arg:\n    exchange: bybit\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(env_path))
        cfgs = load_accounts_dict(arg_path)
        assert "via_env" in cfgs
        assert "via_arg" not in cfgs

    def test_arg_wins_when_env_unset(self, tmp_path, monkeypatch):
        path = tmp_path / "explicit.yaml"
        path.write_text(
            "accounts:\n  via_arg:\n    exchange: bybit\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("ACCOUNTS_YAML_PATH", raising=False)
        cfgs = load_accounts_dict(path)
        assert "via_arg" in cfgs

    def test_default_path_is_repo_config(self):
        assert DEFAULT_ACCOUNTS_YAML == _REPO_ROOT / "config" / "accounts.yaml"
