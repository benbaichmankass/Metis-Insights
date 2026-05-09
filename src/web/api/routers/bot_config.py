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
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"
_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"
_RUNTIME_STATUS_JSON = _REPO_ROOT / "runtime_logs" / "runtime_status.json"
_HALT_FLAG_PATH = "/tmp/trader_halt.flag"

# Account fields the endpoint is allowed to surface. Anything outside
# this set (notably ``api_key_env`` / ``api_secret_env``) is dropped.
_ACCOUNT_PUBLIC_FIELDS = frozenset({
    "type", "exchange", "market_type", "strategies", "risk", "enabled",
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


def _read_yaml(path: Path) -> Dict[str, Any]:
    """Best-effort YAML read. Missing / unreadable / malformed → ``{}``.

    The endpoint must never 500 on a missing config — the operator
    needs visibility precisely when something is off.
    """
    if not path.exists():
        return {}
    try:
        import yaml
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


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
    except (OSError, json.JSONDecodeError):
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

    accounts_raw = _read_yaml(a_path)
    strategies_raw = _read_yaml(s_path)
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
        for name, cfg in (accounts_raw.get("accounts") or {}).items()
    ]

    strategies = _redact_recursive(strategies_raw.get("strategies") or {})

    return {
        "as_of": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "trading_mode": {
            "halted": os.path.exists(h_path),
            "live_per_account": live_per_account,
            "note": (
                "yaml_mode is the static config; live_per_account is the "
                "pipeline's runtime view (Telegram /accounts dry|live "
                "overrides land here). Empty when the pipeline hasn't "
                "written a status snapshot yet."
            ),
        },
        "accounts": accounts,
        "strategies": strategies,
    }


@router.get("/config")
async def get_bot_config() -> Dict[str, Any]:
    return build_config()
