"""S-013 M1 runtime status producer.

Writes ``runtime_logs/runtime_status.json`` on every pipeline tick so the
read-only ``GET /api/status`` endpoint (S-013 M2 PR #1) can serve current
bot state without poking the live process.

Atomic write semantics: render into a sibling ``.tmp`` then ``os.replace``
so a reader never sees a half-written file.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_PATH = _REPO_ROOT / "runtime_logs" / "runtime_status.json"
SCHEMA_VERSION = 1
_START_MONOTONIC = time.monotonic()


def _swallow_runtime_status(status: str, exc: BaseException, **ctx: Any) -> None:
    """Report a config-read failure that previously degraded silently.

    Per-fingerprint dedup in outcomes.report keeps a flapping config
    file from spamming the operator on every status read.
    """
    try:
        from src.runtime.outcomes import Level, report
        report(
            "runtime_status",
            status,
            level=Level.WARN,
            reason=f"{type(exc).__name__}: {exc}",
            **ctx,
        )
    except Exception:  # noqa: BLE001
        pass


def _resolve_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        # S-067 borderline: was silently `pass`. Keep best-effort
        # behaviour (the env-var fallback is intentional) but
        # debug-log so a misconfigured PATH or missing git binary
        # in a sandbox env is visible if someone goes looking. Debug
        # not warning because git absence in test envs is normal
        # noise we don't want flooding the operator's bot.log.
        logger.debug(
            "runtime_status: git_sha resolution failed: %s: %s",
            type(exc).__name__, exc,
        )
    return os.environ.get("GIT_SHA", "unknown")


def _read_strategy_names(strategies_yaml: Path) -> List[str]:
    try:
        import yaml
        with strategies_yaml.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        _swallow_runtime_status("strategies_yaml_read_failed", exc,
                                path=str(strategies_yaml))
        return []
    return [
        name
        for name, cfg in (raw.get("strategies") or {}).items()
        if (cfg or {}).get("enabled")
    ]


def _read_live_per_account(
    accounts_yaml: Path, overrides: Optional[Dict[str, bool]] = None
) -> Dict[str, bool]:
    try:
        import yaml
        with accounts_yaml.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        _swallow_runtime_status("accounts_yaml_read_failed", exc,
                                path=str(accounts_yaml))
        return {}
    overrides = overrides or {}
    # Per S-012 PR E2 / PM § 8 #4: every account starts dry by default.
    # `/accounts live <name>` flips it; the override dict is the source
    # of truth at runtime.
    return {
        name: not overrides.get(name, True)
        for name in (raw.get("accounts") or {}).keys()
    }


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    os.replace(tmp, path)


def build_status(
    now_utc: Optional[datetime] = None,
    start_monotonic: Optional[float] = None,
    strategies_yaml: Optional[Path] = None,
    accounts_yaml: Optional[Path] = None,
    dry_run_overrides: Optional[Dict[str, bool]] = None,
    git_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the runtime_status.json payload. Pure-function for tests."""
    now = now_utc or datetime.now(timezone.utc)
    start = start_monotonic if start_monotonic is not None else _START_MONOTONIC
    strategies_yaml = strategies_yaml or (_REPO_ROOT / "config" / "strategies.yaml")
    accounts_yaml = accounts_yaml or (_REPO_ROOT / "config" / "accounts.yaml")
    if dry_run_overrides is None:
        try:
            from src.units.accounts import get_dry_run_overrides
            dry_run_overrides = get_dry_run_overrides()
        except Exception as exc:  # noqa: BLE001
            # S-067: was silently `dry_run_overrides = {}`. A failure
            # here makes the runtime-status file misreport every
            # account as ``live`` (since the helper that translates
            # overrides flips dry/live per-account from the override
            # dict). Same risk class as PR #630
            # (``MONITOR_APPLY_TO_EXCHANGE`` survivor) where a
            # process-wide gate silently lost money. Pipe through the
            # existing ``_swallow_runtime_status`` helper so the
            # operator gets a deduplicated Telegram alert via
            # ``outcomes.report``.
            _swallow_runtime_status("dry_run_overrides_read_failed", exc)
            dry_run_overrides = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "bot_uptime_s": int(time.monotonic() - start),
        "live": _read_live_per_account(accounts_yaml, dry_run_overrides),
        "strategies": _read_strategy_names(strategies_yaml),
        "git_sha": git_sha if git_sha is not None else _resolve_git_sha(),
        "last_tick_utc": now.replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def write_status(path: Optional[Path] = None, **build_kwargs: Any) -> None:
    """Atomically write runtime status. Best-effort: never raises into the tick loop."""
    try:
        payload = build_status(**build_kwargs)
        _atomic_write_json(path or STATUS_PATH, payload)
    except Exception:
        logger.exception("runtime_status write failed")
