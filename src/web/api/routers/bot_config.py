"""S-064 — GET /api/bot/config.

Read-only view of the bot's effective configuration for the
dashboard's Settings tab. Tier 1 (no session — see
``docs/api-tier-policy.md``).

Reads ``config/accounts.yaml`` and ``config/strategies.yaml`` directly
from disk and overlays the live per-account dry/live state from
``runtime_logs/runtime_status.json`` (which the pipeline writes per
tick — see ``src/web/runtime_status.py``). The web API process and
the pipeline process are separate; the runtime-status file is the
only signal the API has into Telegram-driven runtime overrides.

**Secret handling.** Allowlist for accounts (we only surface fields
in ``_ACCOUNT_PUBLIC_FIELDS``) and recursive denylist for
strategies (any key matching ``_SECRET_KEY_PATTERN`` is dropped at
any depth). The endpoint never echoes ``api_key_env`` /
``api_secret_env`` field *values* — even though those are env-var
names, not secrets, surfacing them tells an attacker exactly which
env vars to look for. The endpoint also never returns the
``runtime_logs/`` path or any DB path; the operator can read those
from the architecture doc instead.

**S-067:** when a YAML file is missing or malformed the endpoint
still returns 200 (per the operator-needs-visibility intent) but
now surfaces the per-file failure as a top-level
``config_load_errors`` array. Pre-S-067 the failure was silently
swallowed and the Settings tab rendered as "no strategies / no
accounts configured".
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"
_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"
# Aligned with the writer in src/web/runtime_status.py:26 — both go
# through runtime_logs_dir() so DATA_DIR / RUNTIME_LOGS_DIR overrides
# apply consistently. Hardcoding here masked the runtime-status drift
# on 2026-05-11 (Settings tab showed stale per-account mode).
_RUNTIME_STATUS_JSON = runtime_logs_dir() / "runtime_status.json"
_HALT_FLAG_PATH = "/tmp/trader_halt.flag"

# Account fields the endpoint is allowed to surface. Anything outside
# this set (notably ``api_key_env`` / ``api_secret_env``) is dropped.
# ``symbols`` (2026-06-11): the per-account instrument list is the
# canonical "what does the system trade" enumeration — consumers
# (Streamlit dashboard, Android app) derive their symbol selectors from
# it instead of hardcoding, so a new instrument shows up without an app
# change.
_ACCOUNT_PUBLIC_FIELDS = frozenset({
    "type", "exchange", "market_type", "strategies", "symbols", "risk",
    "enabled",
    # account_class (2026-06-15): the paper/real funding category. Consumers
    # (dashboard, Android) read the category from here. Not secret-bearing.
    "account_class",
})

# Substrings that mark a key as secret-bearing at any depth. Case-
# insensitive substring match. Belt-and-braces — strategy params are
# not expected to carry these, but if a future strategy starts using
# an env-keyed credential we redact rather than leak.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|token|password|passwd|"
    r"signing[_-]?key|hash|credential)",
    re.IGNORECASE,
)


def _redact_recursive(value: Any) -> Any:
    """Walk a YAML-derived value, dropping any key matching the secret
    pattern. Lists are walked element-wise; scalars pass through."""
    if isinstance(value, dict):
        return {
            k: _redact_recursive(v)
            for k, v in value.items()
            if not _SECRET_KEY_PATTERN.search(str(k))
        }
    if isinstance(value, list):
        return [_redact_recursive(v) for v in value]
    return value


def _read_yaml(
    path: Path,
    errors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Best-effort YAML read. Missing file → ``{}`` silently (a
    fresh install or test env with no config files is a normal
    case; not an error). Unreadable / malformed YAML → ``{}`` plus
    a warning log + an entry in *errors* if provided.

    The endpoint must never 500 on a config-load failure (the
    operator needs visibility precisely when something is off), but
    pre-S-067 it also dropped the failure invisibly, so a malformed
    yaml looked identical to "no strategies configured". This helper
    now logs and surfaces the failure when given a collector list.
    """
    if not path.exists():
        return {}
    try:
        import yaml
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        # S-067: was silently `return {}`. Now logs + surfaces.
        logger.warning(
            "bot_config: yaml read failed for %s: %s: %s",
            path, type(exc).__name__, exc,
        )
        if errors is not None:
            errors.append({
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            })
        return {}
    if isinstance(raw, dict):
        return raw
    # Loaded successfully but not a dict (e.g. a top-level list /
    # scalar) — unusable here. Treat as a load error too.
    if errors is not None:
        errors.append({
            "path": str(path),
            "error": f"yaml top-level is {type(raw).__name__}, expected dict",
        })
    return {}


def _read_runtime_live_state() -> Dict[str, bool]:
    """Pull the per-account live/dry state the pipeline wrote.

    Returns ``{}`` if the runtime-status file is missing / unreadable
    or doesn't carry a ``live`` key (older pipeline build).
    """
    if not _RUNTIME_STATUS_JSON.exists():
        return {}
    try:
        with _RUNTIME_STATUS_JSON.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # S-067 borderline: was silently `return {}`. Keep the
        # empty-dict shape (caller branches on emptiness) but log
        # so a corrupt runtime_status.json surfaces in bot.log.
        logger.warning(
            "bot_config: runtime_status read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return {}
    live = raw.get("live") if isinstance(raw, dict) else None
    if not isinstance(live, dict):
        return {}
    return {str(k): bool(v) for k, v in live.items()}


def _public_account(name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Pull only the allowlisted fields. Coerces None → safe defaults."""
    if not isinstance(cfg, dict):
        cfg = {}
    out: Dict[str, Any] = {"id": name, "yaml_mode": str(cfg.get("mode", "live")).lower()}
    for field in _ACCOUNT_PUBLIC_FIELDS:
        if field in cfg:
            out[field] = cfg[field]
    # Default `enabled` to True so the dashboard doesn't have to carry
    # the implicit-default rule.
    out.setdefault("enabled", True)
    return out


def build_config(
    accounts_yaml: Optional[Path] = None,
    strategies_yaml: Optional[Path] = None,
    runtime_status_json: Optional[Path] = None,
    halt_flag_path: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compose the response. Pure-ish (file I/O isolated); easy to test."""
    global _ACCOUNTS_YAML, _STRATEGIES_YAML, _RUNTIME_STATUS_JSON, _HALT_FLAG_PATH
    a_path = accounts_yaml or _ACCOUNTS_YAML
    s_path = strategies_yaml or _STRATEGIES_YAML
    r_path = runtime_status_json or _RUNTIME_STATUS_JSON
    h_path = halt_flag_path if halt_flag_path is not None else _HALT_FLAG_PATH
    now = now_utc or datetime.now(timezone.utc)

    # S-067: collect per-file load errors so the dashboard can surface
    # "config corrupt" instead of silently rendering an empty Settings
    # tab. Failures still degrade non-fatally — the rest of the
    # payload populates from whatever loaded successfully.
    config_load_errors: List[Dict[str, Any]] = []

    from src.config.accounts_loader import load_accounts_dict
    accounts_dict = load_accounts_dict(a_path, errors=config_load_errors)
    strategies_raw = _read_yaml(s_path, errors=config_load_errors)
    # Override the module-level paths so the runtime-status reader
    # picks up monkeypatched paths in tests.
    saved = _RUNTIME_STATUS_JSON
    try:
        _RUNTIME_STATUS_JSON = r_path
        live_per_account = _read_runtime_live_state()
    finally:
        _RUNTIME_STATUS_JSON = saved

    accounts: List[Dict[str, Any]] = [
        _public_account(name, cfg)
        for name, cfg in accounts_dict.items()
    ]

    strategies = _redact_recursive(strategies_raw.get("strategies") or {})

    return {
        "as_of": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "trading_mode": {
            "halted": os.path.exists(h_path),
            "live_per_account": live_per_account,
            "note": (
                "yaml_mode is the static config; live_per_account is the "
                "pipeline's runtime view (per-account mode flips via the "
                "set-account-mode system-action land here — the only "
                "sanctioned mode-write wire). Empty when the pipeline "
                "hasn't written a status snapshot yet."
            ),
        },
        "accounts": accounts,
        "strategies": strategies,
        "config_load_errors": config_load_errors,
    }


@router.get("/config")
async def get_bot_config() -> Dict[str, Any]:
    return build_config()
