"""Trading-mode flag normalisation — single source of truth.

Background (BUG-031):
    Two layers historically disagreed on what counted as "live":

      - src/runtime/validation.py wanted literal "true" (lowercase).
      - src/runtime/orders.py wanted truthy in {"true","1","yes","on"}.
      - src/runtime/validation.py also expected MODE="LIVE" (uppercase).
      - The operator's natural-language interpretation was MODE="live"
        and ALLOW_LIVE_TRADING="live", which neither layer accepted.
        The pipeline emitted "ALLOW_LIVE_TRADING=true is required for
        live submission" even when the operator believed they had
        enabled live trading.

This module centralises the normalisation so every consumer agrees:

* ``is_live_truthy(value)`` — the canonical "is this value live?"
  predicate. Accepts true / 1 / yes / on / live (case-insensitive).
* ``is_dry_truthy(value)`` — the canonical "is this value dry-run?"
  predicate. Accepts true / 1 / yes / on / dry / dry_run / paper.
* ``allow_live_trading()`` / ``is_dry_run()`` — read the env vars
  with **live as the default** (per the operator's rule that the
  system should default-run live; safety is enforced by the risk
  manager + halt flag, not by an opt-in flag).
* ``LIVE_DEFAULTS`` — the canonical default values
  (``ALLOW_LIVE_TRADING=true``, ``DRY_RUN=false``).
"""
from __future__ import annotations

import os
from typing import Any

# Single source of truth for what counts as "live" / "dry" string-wise.
_LIVE_TRUTHY = {"true", "1", "yes", "on", "live"}
_DRY_TRUTHY = {"true", "1", "yes", "on", "dry", "dry_run", "dry-run", "paper"}

# Canonical defaults. The system default is live; safety is enforced by
# the risk manager (per-account caps) + the kill-switch /halt flag.
LIVE_DEFAULTS = {
    "ALLOW_LIVE_TRADING": "true",
    "DRY_RUN": "false",
}


def is_live_truthy(value: Any) -> bool:
    """Return True if *value* should be treated as 'live'.

    Accepts boolean True, the string "true"/"1"/"yes"/"on"/"live"
    (case-insensitive), and the integer 1. Everything else is False.
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in _LIVE_TRUTHY


def is_dry_truthy(value: Any) -> bool:
    """Return True if *value* should be treated as 'dry-run'.

    Accepts boolean True, the string "true"/"1"/"yes"/"on"/"dry"/
    "dry_run"/"dry-run"/"paper" (case-insensitive), and the integer 1.
    Everything else is False.
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in _DRY_TRUTHY


def allow_live_trading() -> bool:
    """Read ``ALLOW_LIVE_TRADING`` from env. **Defaults to True.**"""
    raw = os.environ.get("ALLOW_LIVE_TRADING", LIVE_DEFAULTS["ALLOW_LIVE_TRADING"])
    return is_live_truthy(raw)


def is_dry_run() -> bool:
    """Read ``DRY_RUN`` from env. **Defaults to False (live).**"""
    raw = os.environ.get("DRY_RUN", LIVE_DEFAULTS["DRY_RUN"])
    return is_dry_truthy(raw)
