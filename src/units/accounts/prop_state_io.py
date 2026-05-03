"""Per-account prop-state persistence (Velotrade phase-2b).

Phase-1 + phase-2 left the prop counters (``cumulative_pnl_pct``,
``active_days``, ``entry_date``) as **in-process only** state seeded
from the YAML ``prop_state:`` block on each ``load_accounts()``
call. That meant a trader restart between trading days reset the
mission progress to zero — the operator had to manually update the
YAML to keep the counters accurate.

This module ships the persistence layer:

- :func:`write_prop_state` does an atomic JSON write of one account's
  counters to ``runtime_state/prop_state.json`` (per-account section).
- :func:`load_prop_state` reads the per-account section back, returning
  ``None`` when the file or the section is absent.
- :func:`set_prop_state_path` lets tests redirect the file to a
  ``tmp_path`` without monkey-patching internals.

Contract (operator-facing):

- The JSON file is the **live** source of truth. Loaders prefer it
  over the YAML ``prop_state:`` block when present.
- The YAML ``prop_state:`` block is the **fallback seed** used when
  the JSON file is absent (e.g. fresh trader install, file cleared
  between phases).
- Writes are best-effort — a write failure logs a warning and does
  NOT raise into the order path. The in-process counters keep
  advancing; the next successful write catches up.

Hard rules respected:

- No new dependencies (stdlib JSON + os.replace atomic write).
- The on-disk file lives outside the repo (gitignored
  ``runtime_state/`` directory) so it never lands in commits.
- Per-account isolation — the file is keyed by account name; one
  account's reset doesn't clobber another's progress.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PROP_STATE_PATH = _REPO_ROOT / "runtime_state" / "prop_state.json"

# Module-level override so callers can redirect without environment
# manipulation. ``None`` falls back to ``PROP_STATE_PATH`` env var, then
# to the default repo-root path.
_PROP_STATE_PATH_OVERRIDE: Optional[Path] = None


def set_prop_state_path(path: Optional[Path]) -> None:
    """Redirect the prop-state file to *path* (or reset when None)."""
    global _PROP_STATE_PATH_OVERRIDE
    _PROP_STATE_PATH_OVERRIDE = path


def get_prop_state_path() -> Path:
    """Return the currently active prop-state file path.

    Resolution order: explicit override (set via
    :func:`set_prop_state_path`) > ``PROP_STATE_PATH`` env var >
    repo-root default ``runtime_state/prop_state.json``.
    """
    if _PROP_STATE_PATH_OVERRIDE is not None:
        return _PROP_STATE_PATH_OVERRIDE
    env = os.environ.get("PROP_STATE_PATH")
    if env:
        return Path(env)
    return _DEFAULT_PROP_STATE_PATH


def _read_full_state() -> Dict[str, Any]:
    path = get_prop_state_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.warning(
                "prop_state_io: %s is not a JSON object — ignoring", path,
            )
            return {}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "prop_state_io: failed to read %s (%s) — using empty state",
            path, exc,
        )
        return {}


def load_prop_state(account_name: str) -> Optional[Dict[str, Any]]:
    """Return the persisted prop-state dict for *account_name*, or None.

    Returns ``None`` when the file doesn't exist or has no entry for
    the account — caller should fall back to the YAML seed.
    """
    if not account_name:
        return None
    full = _read_full_state()
    section = full.get(account_name)
    if not isinstance(section, dict):
        return None
    return dict(section)


def write_prop_state(account_name: str, state: Dict[str, Any]) -> bool:
    """Atomically write *state* for *account_name* to the prop-state file.

    Best-effort: returns ``True`` on success, ``False`` on any
    failure (logged at WARN — never raises). The full file is
    rewritten on each call (the file stays small — one section per
    prop account, ~100 bytes each).

    *state* should contain the canonical fields:
    ``cumulative_pnl_pct`` (float), ``active_days`` (int),
    ``entry_date`` (ISO date string or null). Extra keys are
    preserved on disk but ignored by the loader.
    """
    if not account_name:
        return False
    path = get_prop_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        full = _read_full_state()
        full[account_name] = dict(state)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(full, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "prop_state_io: write failed for account=%s (path=%s): %s",
            account_name, path, exc,
        )
        return False
