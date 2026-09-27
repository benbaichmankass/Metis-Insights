"""Prop-terminal platform adapters, selected by ``config/prop_platforms.yaml``.

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.prop.platform.base import (  # noqa: F401  (re-exported)
    AccountSnapshot,
    FeasibilityError,
    Position,
    PropPlatformAdapter,
    WorkingOrder,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORMS_PATH = _REPO_ROOT / "config" / "prop_platforms.yaml"

KNOWN_PLATFORMS = ("dxtrade", "breakout_terminal")


def load_platform_config(account_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the ``accounts.<account_id>`` block; raise if absent or unknown."""
    p = Path(path) if path else PLATFORMS_PATH
    data = yaml.safe_load(p.read_text()) or {}
    entry = (data.get("accounts") or {}).get(account_id)
    if not isinstance(entry, dict):
        raise KeyError(f"{account_id!r} has no entry in {p.name}")
    platform = str(entry.get("platform") or "").strip()
    if platform not in KNOWN_PLATFORMS:
        raise ValueError(
            f"{account_id!r}: unknown platform {platform!r} (known: {', '.join(KNOWN_PLATFORMS)})")
    url = str(entry.get("login_url") or "")
    if not url.startswith("https://"):
        raise ValueError(f"{account_id!r}: login_url must be an https:// URL (got {url!r})")
    return entry


def adapter_for_platform(platform: str) -> PropPlatformAdapter:
    if platform == "dxtrade":
        from src.prop.platform.dxtrade import DXtradeAdapter
        return DXtradeAdapter()
    if platform == "breakout_terminal":
        from src.prop.platform.breakout_terminal import BreakoutTerminalAdapter
        return BreakoutTerminalAdapter()
    raise ValueError(f"unknown platform {platform!r}")


def get_adapter(account_id: str, path: Optional[Path] = None) -> PropPlatformAdapter:
    return adapter_for_platform(load_platform_config(account_id, path)["platform"])
