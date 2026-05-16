"""Single source of truth for parsing ``config/accounts.yaml``.

Returns ``Dict[account_id, raw_cfg_dict]`` from the YAML's top-level
``accounts:`` block. Every read-only consumer of ``accounts.yaml``
(dashboard routers, runtime status writer, ops scripts, reconciler
helpers) MUST use ``load_accounts_dict()`` instead of calling
``yaml.safe_load`` on the file directly. The CI guard at
``scripts/check_canonical_config_loaders.py`` fails the build if a
new ``yaml.safe_load`` against ``accounts.yaml`` shows up outside
this module.

The production object-builder ``src.units.accounts.load_accounts``
(which wraps each cfg into a ``TradingAccount`` and applies
executor-side validation) is unaffected — it is the second
allowed reader and lives outside this module for historical
reasons (it predates the schema-duplication problem).

Background — why this module exists
-----------------------------------
Before its introduction, eight independent hand-rolled parsers
re-derived the dict-shape view of ``accounts.yaml``. One of them
(``scripts/ops/backfill_orphan_pnl.py``) iterated the wrong shape
(``for entry in data.get("accounts") or []`` — treating the dict
as a list of entries), which silently returned an empty cfg map
on every live run and produced 0 recoveries against the 14-orphan
backlog on 2026-05-16. Centralising the dict reader here eliminates
the bug class at the root rather than patching each parser
individually.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"


def _resolve_path(path: Optional[Union[Path, str]]) -> Path:
    """Resolution order: ``ACCOUNTS_YAML_PATH`` env var > arg > default."""
    env = os.environ.get("ACCOUNTS_YAML_PATH")
    if env:
        return Path(env)
    if path is not None:
        return Path(path)
    return DEFAULT_ACCOUNTS_YAML


def load_accounts_dict(
    path: Optional[Union[Path, str]] = None,
    errors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return ``{account_id: cfg_dict}`` from ``accounts.yaml``.

    Schema: the file's top-level ``accounts:`` key is a dict keyed
    by account_id (``bybit_1:``, ``bybit_2:``, …). Each value is the
    raw cfg dict as written in YAML — no normalisation, no defaults
    applied here. Consumers that need normalisation layer it on top.

    Returns an empty dict on any read/parse failure (logged at
    ``WARNING``) so consumers can fall back gracefully rather than
    crashing the dashboard / runtime-status writer / ops script.

    Pass an ``errors`` list to capture parse failures for surfacing
    to the operator (the dashboard's ``/api/bot/config`` payload uses
    this to render a "config corrupt" banner instead of silently
    showing zero accounts). Each entry is
    ``{"path": str, "error": str}`` matching the shape
    ``bot_config._read_yaml`` produces.
    """
    yaml_path = _resolve_path(path)
    if not yaml_path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        logger.warning("load_accounts_dict: PyYAML not installed")
        if errors is not None:
            errors.append({"path": str(yaml_path), "error": "PyYAML not installed"})
        return {}
    try:
        with yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "load_accounts_dict: parse failed for %s: %s",
            yaml_path, exc,
        )
        if errors is not None:
            errors.append({
                "path": str(yaml_path),
                "error": f"{type(exc).__name__}: {exc}",
            })
        return {}
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): dict(cfg)
        for name, cfg in raw.items()
        if isinstance(cfg, dict)
    }
