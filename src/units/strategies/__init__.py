"""Strategies package — config loader (S-011 PR #4).

``load_strategy_config()`` reads config/strategies.yaml and returns the
per-strategy parameter dict.  This is the single source of truth for
risk_pct, timeframe, symbols, and other strategy-level params.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.utils.paths import repo_root as _repo_root

_REPO_ROOT = _repo_root()
_DEFAULT_STRATEGIES_YAML = os.path.join(_REPO_ROOT, "config", "strategies.yaml")


def load_strategy_config(
    config_path: str = _DEFAULT_STRATEGIES_YAML,
) -> Dict[str, Dict[str, Any]]:
    """Load strategy parameters from *config_path*.

    Parameters
    ----------
    config_path : str
        Path to strategies.yaml.

    Returns
    -------
    dict[str, dict]
        ``{strategy_name: {param: value, ...}}`` for every entry in the
        YAML ``strategies`` section.

    Raises
    ------
    FileNotFoundError
        When *config_path* does not exist.
    """
    import yaml

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    return dict(raw.get("strategies") or {})


def save_strategy_config(
    params: Dict[str, Dict[str, Any]],
    config_path: str = _DEFAULT_STRATEGIES_YAML,
) -> None:
    """Persist *params* back to *config_path*.

    Merges with the existing file so that fields not present in *params*
    (e.g. ``service``, ``signal_prefixes``) are preserved.

    Parameters
    ----------
    params : dict
        Updated strategy params, same shape as ``load_strategy_config()`` output.
    config_path : str
        Path to write.  Must be writable.
    """
    import yaml

    existing: Dict[str, Any] = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or {}

    current = dict(existing.get("strategies") or {})
    for name, updates in params.items():
        if name in current:
            current[name].update(updates)
        else:
            current[name] = updates

    existing["strategies"] = current
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(existing, fh, default_flow_style=False, sort_keys=False)
