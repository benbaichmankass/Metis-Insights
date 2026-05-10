"""Regression test for the runtime_status `_read_live_per_account` default-flip
bug.

Before: when the override dict was empty, every account in
`config/accounts.yaml` rendered as `live=False` (dry) in the dashboard
regardless of its YAML `mode: live` declaration. Operators saw a
permanent "runtime: dry" indicator on live accounts and either ignored
it (cry-wolf) or, on 2026-05-10, escalated as a live-trading outage.

After: the resolver mirrors `src.units.accounts._resolve_mode` —
overrides win, then YAML `mode`, then default `live`.
"""
from __future__ import annotations

from pathlib import Path

from src.web.runtime_status import _read_live_per_account


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "accounts.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_yaml_live_default_no_overrides_renders_live(tmp_path):
    """A YAML-`live` account with no override must show `live=True`.

    This is the regression case — pre-fix this returned `live=False`
    because `overrides.get(name, True)` defaulted to True (dry).
    """
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1:
    mode: live
  bybit_2:
    mode: live
""")
    assert _read_live_per_account(yaml_path, overrides={}) == {
        "bybit_1": True,
        "bybit_2": True,
    }


def test_yaml_dry_default_no_overrides_renders_dry(tmp_path):
    """A YAML-`dry_run` account with no override must show `live=False`."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  prop_velotrade_1:
    mode: dry_run
""")
    assert _read_live_per_account(yaml_path, overrides={}) == {
        "prop_velotrade_1": False,
    }


def test_yaml_omits_mode_defaults_to_live(tmp_path):
    """Per CLAUDE.md, when `mode` is absent the default is `live`."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1: {}
""")
    assert _read_live_per_account(yaml_path, overrides={}) == {"bybit_1": True}


def test_runtime_override_dry_wins_over_yaml_live(tmp_path):
    """`/accounts dry bybit_1` must flip the dashboard to dry."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1:
    mode: live
""")
    assert _read_live_per_account(yaml_path, overrides={"bybit_1": True}) == {
        "bybit_1": False,
    }


def test_runtime_override_live_wins_over_yaml_dry(tmp_path):
    """`/accounts live prop_velotrade_1` must flip the dashboard to live."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  prop_velotrade_1:
    mode: dry_run
""")
    assert _read_live_per_account(yaml_path, overrides={"prop_velotrade_1": False}) == {
        "prop_velotrade_1": True,
    }


def test_mixed_accounts_some_overridden_some_not(tmp_path):
    """Each account is resolved independently; an override on one must
    not affect another's default resolution."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1:
    mode: live
  bybit_2:
    mode: live
  prop_velotrade_1:
    mode: dry_run
""")
    out = _read_live_per_account(
        yaml_path, overrides={"bybit_2": True}  # operator forced bybit_2 dry
    )
    assert out == {
        "bybit_1": True,        # YAML live, no override → live
        "bybit_2": False,       # YAML live, but override = dry
        "prop_velotrade_1": False,  # YAML dry_run, no override → dry
    }


def test_dry_aliases_all_resolve_to_dry(tmp_path):
    """The canonical resolver accepts dry / dry_run / dry-run / paper."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  a: {mode: dry}
  b: {mode: dry_run}
  c: {mode: dry-run}
  d: {mode: paper}
  e: {mode: LIVE}
""")
    out = _read_live_per_account(yaml_path, overrides={})
    assert out == {"a": False, "b": False, "c": False, "d": False, "e": True}
