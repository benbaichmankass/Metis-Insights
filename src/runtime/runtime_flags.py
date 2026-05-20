"""Runtime flag reader for sentinel files in runtime_flags/.

A "flag" is a file whose existence signals a runtime state. Creating and
removing these files is the operator's interface; this module is the read
side. The pipeline (and any other consumer) calls the helpers here rather
than sprinkling os.path.exists() calls.

Current flag conventions:
  runtime_flags/send_hourly_demo  — force-send the hourly report (consumed on use)
  runtime_flags/pause_<name>      — pause signal generation for strategy <name>
                                    without restarting the process

The halt flag (/tmp/trader_halt.flag) predates this module. It lives in
/tmp so it survives a fresh repo clone and is NOT managed here.

Operator quick-reference:
  Pause a strategy:   touch runtime_flags/pause_vwap
  Resume a strategy:  rm runtime_flags/pause_vwap
  List paused:        ls runtime_flags/pause_*
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def flags_dir() -> Path:
    """Absolute path to the runtime_flags/ directory (repo root level)."""
    return Path(__file__).resolve().parent.parent.parent / "runtime_flags"


def is_strategy_paused(strategy_name: str) -> bool:
    """Return True when ``runtime_flags/pause_<strategy_name>`` exists.

    The flag is a plain sentinel file — its content is ignored. Absence of
    the flags/ directory itself is treated as no flags set (safe default).
    """
    flag = flags_dir() / f"pause_{strategy_name}"
    exists = flag.exists()
    if exists:
        logger.info("runtime_flags: strategy '%s' is paused (flag: %s)", strategy_name, flag)
    return exists


def list_paused_strategies() -> list[str]:
    """Return sorted names of all currently paused strategies.

    Scans runtime_flags/ for files named ``pause_*`` and strips the prefix.
    Returns an empty list when the directory is absent or contains no pause
    flags.
    """
    d = flags_dir()
    if not d.is_dir():
        return []
    prefix = "pause_"
    return sorted(
        p.name[len(prefix):]
        for p in d.iterdir()
        if p.is_file() and p.name.startswith(prefix)
    )


def _centralized_allocator_enabled(settings: dict) -> bool:
    """Return True when the typed allocator path should shadow the dispatch.

    Feature flag for S5 (M11 multi-strategy architecture). Default is
    **false** — the live pipeline uses the existing path unchanged.
    Operator opt-in: export CENTRALIZED_ALLOCATOR=true (or set it in
    the settings dict). Shadow mode only: dispatch is unchanged until S6.
    """
    raw = settings.get("CENTRALIZED_ALLOCATOR") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("CENTRALIZED_ALLOCATOR", "false")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _advisory_mode_enabled(settings: dict) -> bool:
    """Return True when advisory-stage model scores should be routed to the
    coordinator advisory hook.

    Feature flag for S10 (M11 — ML decision-layer advisory hooks). Default
    is **false** — advisory scores are computed by `with_shadow_preds_advisory`
    but the coordinator hook is not called. The live order path is completely
    unaffected in either state; the hook only logs scores.

    Operator opt-in: export ADVISORY_MODE=true (or set in the settings dict).
    The shadow → advisory promotion gate remains the operator's explicit
    approval step; this flag only enables the score-logging side-channel.
    """
    raw = settings.get("ADVISORY_MODE") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("ADVISORY_MODE", "false")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}
