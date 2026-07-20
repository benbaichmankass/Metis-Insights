"""Registry-root change fingerprint for the per-process predictor caches.

The live trader caches resolved predictors per process
(``regime_bar_scoring._PREDICTOR_CACHE``,
``ml_vol_verdict._ADVISORY_SPEC_CACHE``). Before 2026-07-20 those caches
only rotated on a process restart — a registry STAGE change (a promotion)
that leaves the resolved model-id UNION unchanged kept serving the old
``ShadowPredictor`` objects with their old stages until the next deploy
bounced the process (the M25 BTC/SOL execution needed an explicit
``restart-bot-service`` purely for this). The fingerprint is the cheap
invalidation signal: the max mtime over the registry root's per-model
``*.json`` files (plus the directory itself, so a file ADD/REMOVE that
leaves every surviving mtime alone still registers). The trainer-mirror
publish rewrites exactly those files, so a promotion propagates to the live
process on its next tick with no restart.

Fail-permissive: any filesystem error returns ``-1.0`` — a STABLE value —
so a transient hiccup retains the currently cached predictors instead of
churning artifact loads on a live tick.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def registry_fingerprint(root: Any) -> float:
    """Max st_mtime over ``root`` itself and its ``*.json`` files.

    ``-1.0`` on any error (missing root, permission, race) — stable, never
    raises, so callers can embed it in a cache key unconditionally.
    """
    try:
        p = Path(root)
        newest = p.stat().st_mtime
        for f in p.glob("*.json"):
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue  # racing writer/unlink — the dir mtime covers it
            if mt > newest:
                newest = mt
        return newest
    except Exception:  # noqa: BLE001 — fail-permissive by contract
        return -1.0
