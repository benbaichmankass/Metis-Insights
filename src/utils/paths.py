"""Canonical repo-root and runtime-data path resolver.

Two responsibilities:

1. ``repo_root()`` — walk up from this file until a marker is found
   (.git, pyproject.toml, or requirements.txt — first match wins).
   The result is cached after the first call.

   Why this exists: ad-hoc ``os.path.abspath(os.path.join(__file__, "..", ".."))``
   calculations with hard-coded ``..`` counts drifted every time a module
   moved to a new depth (see BUG-037, BUG-024).

2. ``data_dir`` / ``runtime_logs_dir`` / ``runtime_state_dir`` /
   ``artifacts_dir`` — centralized resolvers for the four logical
   runtime-data roots. Each is **opt-in via env var**; when the env is
   unset the helper falls back to the repo-relative subdirectory,
   preserving historical behavior on the live VM.

   Resolution order for every helper:
     1. Per-root env override (e.g. ``RUNTIME_LOGS_DIR``) → exact path.
     2. ``DATA_DIR`` umbrella env → ``$DATA_DIR/<subdir>``.
     3. Repo-relative ``<repo>/<subdir>`` if the repo tree is writable.
     4. Last-resort user fallback ``~/.ict-trading-bot/<subdir>`` (only
        reached when the repo tree is read-only, e.g. a pip-installed
        copy).

   All four helpers ``mkdir(parents=True, exist_ok=True)`` their result
   before returning, so callers can write immediately.

   See ``docs/architecture/oci-block-storage.md`` for the migration
   contract and ``docs/runbooks/mounted-storage.md`` for the ops
   procedure.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

_MARKERS = (".git", "pyproject.toml", "requirements.txt")

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def repo_root() -> str:
    """Return the absolute path to the repository root.

    Walks up from this file until it finds a directory that contains one
    of the marker files/dirs listed in ``_MARKERS``. Raises ``RuntimeError``
    if the root cannot be found (e.g. the file was moved outside the repo).
    """
    current = os.path.dirname(os.path.abspath(__file__))
    while True:
        for marker in _MARKERS:
            if os.path.exists(os.path.join(current, marker)):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(
                "repo_root(): could not locate repo root from "
                f"{os.path.dirname(os.path.abspath(__file__))}. "
                f"Looked for markers: {_MARKERS}"
            )
        current = parent


# ── Runtime data roots ─────────────────────────────────────────────────
#
# Four logical roots in use today (see audit in PR description):
#
#   <repo>/data/           — CSVs, the historical trades.db, candles
#   <repo>/runtime_logs/   — heartbeat, status, audit jsonls, diagnostics
#   <repo>/runtime_state/  — durable per-account state, fills sqlite
#   <repo>/artifacts/      — health snapshots
#
# When ``DATA_DIR`` is set (e.g. to ``/data/bot-data`` on the OCI
# block volume), each helper returns ``$DATA_DIR/<subdir>`` instead of
# the repo-relative path. Per-root overrides win over ``DATA_DIR``.

_USER_FALLBACK = Path.home() / ".ict-trading-bot"
_ENV_UMBRELLA = "DATA_DIR"
_ENV_PER_ROOT = {
    "data": "DATA_SUBDIR_DATA",  # rarely needed; "data" subdir override
    "runtime_logs": "RUNTIME_LOGS_DIR",
    "runtime_state": "RUNTIME_STATE_DIR",
    "artifacts": "ARTIFACTS_DIR",
}


def _resolve_root(subdir: str) -> Path:
    """Resolve a logical root by name, honoring env overrides.

    See module docstring for the resolution order. Always returns a
    directory that exists (or raises if no candidate is writable).
    """
    env_name = _ENV_PER_ROOT.get(subdir)
    if env_name and (override := os.environ.get(env_name)):
        candidate = Path(override).expanduser()
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    umbrella = os.environ.get(_ENV_UMBRELLA)
    if umbrella:
        candidate = Path(umbrella).expanduser() / subdir
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    repo_candidate = Path(repo_root()) / subdir
    try:
        repo_candidate.mkdir(parents=True, exist_ok=True)
        return repo_candidate
    except (OSError, PermissionError) as exc:
        logger.warning(
            "paths: repo-relative %s not writable (%s); "
            "falling back to %s. Set DATA_DIR to silence this.",
            repo_candidate, exc, _USER_FALLBACK / subdir,
        )

    fallback = _USER_FALLBACK / subdir
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def data_dir() -> Path:
    """Return the ``data/`` root (CSVs, candle dumps, the trades.db)."""
    return _resolve_root("data")


def runtime_logs_dir() -> Path:
    """Return the ``runtime_logs/`` root (heartbeat, jsonl audit, status)."""
    return _resolve_root("runtime_logs")


def runtime_state_dir() -> Path:
    """Return the ``runtime_state/`` root (prop_state.json, fills sqlite)."""
    return _resolve_root("runtime_state")


def artifacts_dir() -> Path:
    """Return the ``artifacts/`` root (health snapshots, generated bundles)."""
    return _resolve_root("artifacts")


def describe_roots() -> dict[str, str]:
    """Return a debug map of the resolved roots and their env source.

    Used by ``scripts/print_runtime_profile.py`` and the preflight
    script to surface the active configuration without writing anything.
    """
    out: dict[str, str] = {}
    for sub in ("data", "runtime_logs", "runtime_state", "artifacts"):
        per_root_env = _ENV_PER_ROOT.get(sub)
        if per_root_env and os.environ.get(per_root_env):
            source = f"env:{per_root_env}"
        elif os.environ.get(_ENV_UMBRELLA):
            source = f"env:{_ENV_UMBRELLA}"
        else:
            source = "repo-relative"
        out[sub] = f"{_resolve_root(sub)} ({source})"
    return out
