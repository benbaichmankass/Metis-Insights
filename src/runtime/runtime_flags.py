"""Runtime flag reader for sentinel files in runtime_flags/.

A "flag" is a file whose existence signals a runtime state. Creating and
removing these files is the operator's interface; this module is the read
side. The pipeline (and any other consumer) calls the helpers here rather
than sprinkling os.path.exists() calls.

Current flag conventions:
  runtime_flags/pause_<name>      — pause signal generation for strategy <name>
                                    without restarting the process

The old ``send_hourly_demo`` flag was retired with the hourly-producer
consolidation (TELEGRAM-SPEC.md § 4.1): the hourly report is now driven
solely by ``ict-hourly-snapshot.timer`` → ``scripts/send_hourly_now.py``.
To force a send now, run that script (or start the unit) — it always
dispatches and is flock-guarded against double-fires.

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


# NOTE: ``_advisory_mode_enabled`` (the ``ADVISORY_MODE`` env gate) was removed
# 2026-06-13. Advisory influence is now gated by the model's deployment STAGE
# alone (advisory / limited_live / live_approved), with the per-strategy
# ``advisory_policy`` as permissive config (default ``annotate``). The redundant
# default-off env switch was the "third gate" the Prime Directive forbids; to
# turn a model's influence off, demote it to ``shadow``.


def _news_influence_mode(settings: dict) -> str:
    """Return the news-influence sizing mode: ``off`` | ``annotate`` | ``downsize``.

    Feature flag for the M9 graduated "act" layer (`src/runtime/news_sizing.py`).
    Default **off** — the live order path acts only on the news veto; the
    reductive downsize hook is inert. Operator opt-in: export
    ``NEWS_INFLUENCE_MODE=downsize`` (or set it in the settings dict). Unknown
    values degrade to ``off`` (fail-safe — never silently downsizes on a typo).
    """
    raw = settings.get("NEWS_INFLUENCE_MODE") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("NEWS_INFLUENCE_MODE", "off")
    mode = str(raw).strip().lower()
    return mode if mode in {"off", "annotate", "downsize"} else "off"


def _regime_ml_verdict_mode(settings: dict | None = None) -> str:
    """Return the regime ML-vol-verdict mode: ``off`` | ``shadow`` | ``use``.

    Feature flag for Design A (``docs/research/A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md``),
    mirroring ``_news_influence_mode``. Default **off** — deploying the code is
    a behaviour no-op (the gate emits no new audit row and runs zero ML work).

    - ``off``     (default) — no ``regime_ml_vol_shadow`` row, zero overhead.
    - ``shadow``  — Phase 1: compute the advisory head's ``vol_regime`` per
      candidate and emit a ``regime_ml_vol_shadow`` audit row comparing it
      against the frozen label; the gate DECISION still uses the frozen
      ``intent.vol_regime`` (observe-only, ``enforced: false``).
    - ``use``     — Phase 2 placeholder: resolves like ``shadow`` for now (still
      emits the audit row, still uses the frozen label). **TODO(Phase 2):**
      thread the non-unknown ML ``vol_regime`` into ``would_gate`` instead of
      ``intent.vol_regime`` — a separate operator-gated PR.

    Unknown values degrade to ``off`` (fail-safe — never silently acts on a typo).
    Order-routing-affecting → Tier-3 to flip past ``off`` on the VM.
    """
    raw = settings.get("REGIME_ML_VERDICT_MODE") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("REGIME_ML_VERDICT_MODE", "off")
    mode = str(raw).strip().lower()
    return mode if mode in {"off", "shadow", "use"} else "off"


_DEFAULT_ML_VOL_VERDICT_THRESHOLD = 0.5


def _ml_vol_verdict_threshold(settings: dict | None = None) -> float:
    """Return the ``P(volatile) >= τ`` threshold for the ML vol verdict.

    Reads ``ML_VOL_VERDICT_THRESHOLD`` (settings → env → default ``0.5``). A
    non-numeric value falls back to the default so a typo can never strand the
    verdict. Order-routing-affecting → Tier-3 to change on the VM (Design A
    guardrails). ``ml_vol_regime`` maps ``P(volatile) >= τ`` → ``volatile``
    else ``calm``.
    """
    raw = settings.get("ML_VOL_VERDICT_THRESHOLD") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("ML_VOL_VERDICT_THRESHOLD")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_ML_VOL_VERDICT_THRESHOLD
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ML_VOL_VERDICT_THRESHOLD


# NOTE: the conviction-sizing **annotator** (`conviction_sizing.annotate_conviction_sizing`
# / `compute_conviction_sizing`) has **no** flag. It ships as always-on
# advisory/observe-only — it computes the would-be conviction size and logs it on
# every order but NEVER changes qty, exactly like the P1 `meta.conviction` stamp.
# A default-off gate in front of the *annotator* would be the stranding trap the
# Prime Directive forbids, so the annotator stays flagless.
#
# The flags below gate a SEPARATE, NEW apply path
# (`conviction_sizing.apply_conviction_sizing`, Design B — graduate conviction
# from soak to live, 2026-06-27). They do NOT gate the annotator. Reconciliation
# of the 2026-06-16 flag rejection (operator-blessed 2026-06-27): the rejection
# was about gating an observe-only annotator; this flag gates a genuine
# reductive/symmetric size INFLUENCE — exactly the role ``NEWS_INFLUENCE_MODE``
# plays (a tri-state ``*_MODE``, not a default-off ``*_ENABLED``, so it passes the
# env-gate guard). The annotator soak keeps running unchanged regardless of mode.


def _conviction_sizing_mode(settings: dict | None = None) -> str:
    """Return the conviction-sizing apply mode: ``off`` | ``annotate`` | ``apply``.

    Feature flag for Design B's NEW apply path
    (`src/runtime/conviction_sizing.py::apply_conviction_sizing`), mirroring
    ``_news_influence_mode``. Default **off** — deploying the code is a
    behaviour no-op (the apply path returns ``sized_qty`` unchanged; the
    flagless annotator soak still runs separately).

    - ``off``      (default) — the apply path is inert; qty unchanged.
    - ``annotate`` — compute the would-be **composed** conviction size, stamp it
      on the real package (``meta.conviction_apply_decision``), return UNCHANGED.
    - ``apply``    — replace ``sized_qty`` with the conviction-driven size,
      hard-bounded by the 2% budget + margin cap, sub-mode by
      ``CONVICTION_SIZING_DIRECTION``.

    Unknown values degrade to ``off`` (fail-safe — never silently resizes on a
    typo). Order-routing-affecting → Tier-3 to flip past ``off`` on the VM.
    """
    raw = settings.get("CONVICTION_SIZING_MODE") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("CONVICTION_SIZING_MODE", "off")
    mode = str(raw).strip().lower()
    return mode if mode in {"off", "annotate", "apply"} else "off"


def _conviction_sizing_accounts(settings: dict | None = None) -> list[str]:
    """Return the conviction-sizing account allowlist (comma-separated names).

    An **empty** list means "all accounts" (no narrowing). Used by
    ``apply_conviction_sizing`` to stage the apply path on a single demo account
    (e.g. ``bybit_1``) before lifting it to a real account. Reads
    ``CONVICTION_SIZING_ACCOUNTS`` (settings → env → empty). Whitespace around
    each name is stripped; blanks are dropped.
    """
    raw = settings.get("CONVICTION_SIZING_ACCOUNTS") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("CONVICTION_SIZING_ACCOUNTS", "")
    return [name.strip() for name in str(raw).split(",") if name.strip()]


def _conviction_sizing_direction(settings: dict | None = None) -> str:
    """Return the conviction-sizing direction sub-mode: ``reductive`` | ``symmetric``.

    Only consulted when ``CONVICTION_SIZING_MODE=apply``. Default **reductive**
    (``final = min(conviction_qty, sized_qty)`` — never larger than the
    RiskManager-sized qty; redundant with the advisory/news reducers but the
    safe first apply step). ``symmetric`` lets conviction *scale up* off the min
    lot, hard-bounded by the 2% risk budget + margin cap already enforced in
    ``compute_conviction_sizing`` (conviction=1.0 reaches exactly the 2% budget,
    never more). Reads ``CONVICTION_SIZING_DIRECTION`` (settings → env →
    default). Unknown values degrade to ``reductive`` (fail-safe — never silently
    enlarges on a typo). Tier-3 to flip to ``symmetric`` on the VM.
    """
    raw = settings.get("CONVICTION_SIZING_DIRECTION") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("CONVICTION_SIZING_DIRECTION", "reductive")
    direction = str(raw).strip().lower()
    return direction if direction in {"reductive", "symmetric"} else "reductive"
