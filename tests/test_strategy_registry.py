"""Tests for src/strategy_registry.py (S-007 PR #113)."""
from __future__ import annotations

import os
import textwrap

import pytest

import src.strategy_registry as reg

# Path to the real YAML so integration tests can use it directly.
_REAL_YAML = os.path.join(
    os.path.dirname(__file__), "..", "config", "strategies.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: str) -> str:
    p = tmp_path / "strategies.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


# ---------------------------------------------------------------------------
# load_strategies — unit tests with synthetic YAML
# ---------------------------------------------------------------------------

def test_load_strategies_returns_list(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            service: ict-trader-alpha
            model: alpha_v1.joblib
          beta:
            service: ict-trader-beta
            model: null
    """)
    strategies = reg.load_strategies(path)
    assert isinstance(strategies, list)
    assert len(strategies) == 2


def test_load_strategies_fields(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            service: ict-trader-alpha
            model: alpha_v1.joblib
    """)
    s = reg.load_strategies(path)[0]
    assert s["name"] == "alpha"
    assert s["service"] == "ict-trader-alpha"
    assert s["model"] == "alpha_v1.joblib"


def test_load_strategies_null_model(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          gamma:
            service: ict-trader-gamma
            model: null
    """)
    s = reg.load_strategies(path)[0]
    assert s["model"] is None


def test_load_strategies_missing_service_defaults_to_live(tmp_path):
    """S-012 PR C4: missing service defaults to ict-trader-live.

    Single-process architecture (PM § 8 #1): every strategy runs inside
    the same systemd unit. Per-strategy service names are aspirational
    metadata that this sprint removed.
    """
    path = _write_yaml(tmp_path, """
        strategies:
          delta:
            model: null
    """)
    s = reg.load_strategies(path)[0]
    assert s["service"] == "ict-trader-live"


def test_load_strategies_bad_yaml_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies: "not-a-mapping"
    """)
    with pytest.raises(ValueError, match="expected mapping"):
        reg.load_strategies(path)


# ---------------------------------------------------------------------------
# model_path — unit tests
# ---------------------------------------------------------------------------

def test_model_path_returns_none_for_null_model(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    assert reg.model_path("vwap", path) is None


def test_model_path_returns_absolute_path(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          breakout_confirmation:
            service: ict-trader-breakout
            model: btc_v1.joblib
    """)
    result = reg.model_path("breakout_confirmation", path)
    assert result is not None
    assert os.path.isabs(result)
    assert result.endswith("btc_v1.joblib")


def test_model_path_unknown_strategy_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    with pytest.raises(KeyError, match="nonexistent"):
        reg.model_path("nonexistent", path)


# ---------------------------------------------------------------------------
# service_name — unit tests
# ---------------------------------------------------------------------------

def test_service_name_returns_string(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          ict:
            service: ict-trader-ict
            model: null
    """)
    assert reg.service_name("ict", path) == "ict-trader-ict"


def test_service_name_unknown_strategy_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    with pytest.raises(KeyError):
        reg.service_name("not_there", path)


# ---------------------------------------------------------------------------
# Integration — real config/strategies.yaml
# ---------------------------------------------------------------------------

def test_real_yaml_loads():
    strategies = reg.load_strategies(_REAL_YAML)
    # turtle_soup + vwap (live) + ict_scalp_5m (registered, disabled).
    # Bumped from 2 → 3 by the ict_scalp_5m landing; turn-on for live
    # is gated by the `enabled` flag in the YAML, not by the registry
    # row count.
    assert len(strategies) == 3


def test_real_yaml_has_required_strategies():
    strategies = reg.load_strategies(_REAL_YAML)
    names = {s["name"] for s in strategies}
    assert names == {"turtle_soup", "vwap", "ict_scalp_5m"}


def test_real_yaml_vwap_no_model():
    assert reg.model_path("vwap", _REAL_YAML) is None


def test_real_yaml_turtle_soup_no_model():
    assert reg.model_path("turtle_soup", _REAL_YAML) is None


def test_real_yaml_service_names():
    # S-012 single-process: every strategy currently runs inside
    # ict-trader-live. The `service:` field is scheduled for removal in PR C4.
    assert reg.service_name("turtle_soup", _REAL_YAML) == "ict-trader-live"
    assert reg.service_name("vwap", _REAL_YAML) == "ict-trader-live"


def test_real_yaml_all_strategies_have_service():
    for s in reg.load_strategies(_REAL_YAML):
        assert s["service"], f"strategy '{s['name']}' missing service"
