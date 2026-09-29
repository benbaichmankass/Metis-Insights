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

# Login URL used when an entry omits ``login_url``, so switching an account's
# terminal is the one line ``platform: <name>`` (PROP-TERM, 2026-09-28). The
# dxtrade URL is MEASURED (config/prop_platforms.yaml header); the
# breakout_terminal one is the dashboard the operator logs in at, from which
# "Open Terminal" is reached (spec S23) — its login flow is NOT MEASURED.
DEFAULT_LOGIN_URLS = {
    "dxtrade": "https://wss.breakoutprop.com/",
    "breakout_terminal": "https://app.breakoutprop.com/",
}


def load_platform_config(account_id: str, path: Optional[Path] = None,
                         section: str = "accounts") -> Dict[str, Any]:
    """Return the ``<section>.<account_id>`` block (``accounts`` for a traded
    account, ``probes`` for a read-only terminal probe); raise if absent or
    unknown. ``login_url`` falls back to :data:`DEFAULT_LOGIN_URLS`."""
    p = Path(path) if path else PLATFORMS_PATH
    data = yaml.safe_load(p.read_text()) or {}
    entry = (data.get(section) or {}).get(account_id)
    if not isinstance(entry, dict):
        raise KeyError(f"{account_id!r} has no entry under {section!r} in {p.name}")
    entry = dict(entry)
    platform = str(entry.get("platform") or "").strip()
    if platform not in KNOWN_PLATFORMS:
        raise ValueError(
            f"{account_id!r}: unknown platform {platform!r} (known: {', '.join(KNOWN_PLATFORMS)})")
    entry.setdefault("login_url", DEFAULT_LOGIN_URLS[platform])
    # A leftover URL of the OTHER terminal would drive the wrong page: a
    # half-done switch fails loudly here instead of at the login.
    for other, other_url in DEFAULT_LOGIN_URLS.items():
        if other != platform and str(entry["login_url"]).rstrip("/") == other_url.rstrip("/"):
            raise ValueError(f"{account_id!r}: platform {platform!r} but login_url is the {other} URL "
                             f"({other_url}); drop login_url to use the {platform} default")
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
