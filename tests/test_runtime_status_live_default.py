"""Regression test for the runtime_status `_read_live_per_account` default-flip
bug.

Before: when the override dict was empty, every account in
`config/accounts.yaml` rendered as `live=False` (dry) in the dashboard
regardless of its YAML `mode: live` declaration. Operators saw a
permanent "runtime: dry" indicator on live accounts and either ignored
it (cry-wolf) or, on 2026-05-10, escalated as a live-trading outage.

After: the resolver mirrors `src.units.accounts._resolve_mode` — YAML
`mode`, then default `live`. (The in-memory override layer was removed
2026-06-10; accounts.yaml `mode:` is the only source.)
"""
from __future__ import annotations

from pathlib import Path

from src.web.runtime_status import _read_live_per_account


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "accounts.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_yaml_live_default_renders_live(tmp_path):
    """A YAML-`live` account must show `live=True`.

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
    assert _read_live_per_account(yaml_path) == {
        "bybit_1": True,
        "bybit_2": True,
    }


def test_yaml_dry_default_renders_dry(tmp_path):
    """A YAML-`dry_run` account must show `live=False`."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  prop_velotrade_1:
    mode: dry_run
""")
    assert _read_live_per_account(yaml_path) == {
        "prop_velotrade_1": False,
    }


def test_yaml_omits_mode_defaults_to_live(tmp_path):
    """Per CLAUDE.md, when `mode` is absent the default is `live`."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1: {}
""")
    assert _read_live_per_account(yaml_path) == {"bybit_1": True}


def test_mixed_accounts_resolve_independently(tmp_path):
    """Each account is resolved independently from its own YAML `mode`."""
    yaml_path = _write_yaml(tmp_path, """
accounts:
  bybit_1:
    mode: live
  bybit_2:
    mode: live
  prop_velotrade_1:
    mode: dry_run
""")
    assert _read_live_per_account(yaml_path) == {
        "bybit_1": True,
        "bybit_2": True,
        "prop_velotrade_1": False,
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
    out = _read_live_per_account(yaml_path)
    assert out == {"a": False, "b": False, "c": False, "d": False, "e": True}
