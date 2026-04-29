"""Units package — dynamic unit loader (S-009 PR #2).

``load_enabled_units()`` reads config/units.yaml and returns only the
strategy/account entries that have ``enabled: true`` (or no ``enabled``
field, which defaults to enabled).  This powers the App unit's
enable/disable workflow without touching the Coordinator's main config path.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_UNITS_YAML = os.path.join(_REPO_ROOT, "config", "units.yaml")


def load_enabled_units(config_path: str = _DEFAULT_UNITS_YAML) -> Dict[str, Any]:
    """Return a filtered view of units.yaml containing only enabled entries.

    Strategies and accounts with ``enabled: false`` are excluded.
    All other unit sections (dashboards, return_commands, etc.) are passed
    through unchanged — only list-based sections support per-entry toggling.

    Parameters
    ----------
    config_path : str
        Path to units.yaml.  Defaults to ``config/units.yaml``.

    Returns
    -------
    dict
        ``{"strategies": [...enabled only...], "accounts": [...enabled only...],
           <other unit sections unchanged>}``

    Raises
    ------
    FileNotFoundError
        When *config_path* does not exist.
    """
    import yaml

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    units: Dict[str, Any] = dict(raw.get("units") or {})
    result: Dict[str, Any] = {}

    for section, value in units.items():
        if isinstance(value, list):
            result[section] = [
                entry for entry in value
                if isinstance(entry, dict) and entry.get("enabled", True)
            ]
        else:
            result[section] = value

    return result


def list_enabled_strategies(config_path: str = _DEFAULT_UNITS_YAML) -> List[str]:
    """Return names of enabled strategies from units.yaml."""
    units = load_enabled_units(config_path)
    return [
        s["name"] for s in (units.get("strategies") or [])
        if isinstance(s, dict) and s.get("name")
    ]
