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

Canonical-path enforcement (2026-05-12, post-incident):
  ``DATA_DIR`` is expected to be an absolute path on every deployment
  that uses the OCI block-storage mount. The canonical value is
  ``/data/bot-data`` (matching the systemd drop-ins under
  ``deploy/*.service.d/data-dir.conf``). A relative ``DATA_DIR``
  resolves to a repo-anchored path, which on the live VM produces a
  WRITER-vs-READER split between consumers — the trader writes to
  ``/home/ubuntu/ict-trading-bot/data/runtime_logs/`` while the
  systemd-managed reader processes look at ``/data/bot-data/...``.
  This module emits a CRITICAL log on every consumer process startup
  when ``DATA_DIR`` is set and relative, so the misalignment is
  visible in journalctl. The trader still starts (Prime Directive
  — see docs/CLAUDE-RULES-CANONICAL.md § Prime Directive); the
  ``scripts/ops/fix_data_dir.sh`` operator-action wrapper is the
  remediation wire.
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


# ── Runtime data roots ────────────────────────────────────────────
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

# Module-level guard so the relative-DATA_DIR alert fires exactly once
# per process — not once per _resolve_root call.
_RELATIVE_DATA_DIR_ALERTED = False

# The canonical absolute path the systemd drop-ins declare
# (deploy/*.service.d/data-dir.conf). Used by the alert message so the
# operator gets a copy-paste-ready fix.
_CANONICAL_DATA_DIR_HINT = "/data/bot-data"


def _swallow_paths_warning(status: str, **ctx) -> None:
    """Report a path-resolver anomaly through the existing
    outcomes.report pipeline so the operator gets a Telegram alert
    via the per-fingerprint-deduped channel.

    Best-effort: a failure here never breaks path resolution; the
    canonical log line below is the primary signal.
    """
    try:
        from src.runtime.outcomes import Level, report
        report(
            "paths_resolver",
            status,
            level=Level.WARN,
            **ctx,
        )
    except Exception:  # noqa: BLE001
        pass


def _alert_on_relative_data_dir(umbrella: str, resolved: Path) -> None:
    """Emit a one-shot CRITICAL log + outcomes ping when DATA_DIR is
    relative.

    The 2026-05-12 silent-flip incident traced back to .env carrying
    ``DATA_DIR=data/`` (relative). The trader resolved it to
    ``<repo>/data/runtime_logs/`` while the systemd drop-in declares
    the canonical absolute path is ``/data/bot-data``. Readers and
    writers ended up on different paths — a split-brain that
    presented as 'heartbeat-writer silent failure' and an apparent
    'bybit_2 silent flip'.

    This alert is the structural prevention: every consumer process
    that starts with a relative DATA_DIR now leaves a CRITICAL line
    in journalctl and queues a Telegram ping. The remediation wire
    is the ``fix-data-dir`` operator-action (see
    ``scripts/ops/fix_data_dir.sh``).
    """
    global _RELATIVE_DATA_DIR_ALERTED
    if _RELATIVE_DATA_DIR_ALERTED:
        return
    _RELATIVE_DATA_DIR_ALERTED = True
    logger.critical(
        "paths: DATA_DIR=%r is RELATIVE. Resolving to %s (anchored to "
        "repo_root). This is almost certainly wrong on the live VM where "
        "systemd drop-ins declare DATA_DIR=%s. Reader-vs-writer split-brain "
        "is the likely failure mode. Fix: dispatch the fix-data-dir "
        "operator-action (scripts/ops/fix_data_dir.sh) which strips the "
        ".env override so the systemd value wins.",
        umbrella, resolved, _CANONICAL_DATA_DIR_HINT,
    )
    _swallow_paths_warning(
        "data_dir_relative",
        umbrella=umbrella,
        resolved=str(resolved),
        canonical_hint=_CANONICAL_DATA_DIR_HINT,
    )


def _alert_on_data_dir_mismatch(umbrella: str) -> None:
    """Emit a CRITICAL log + outcomes ping when DATA_DIR is absolute
    but doesn't match the canonical drop-in value.

    An absolute override is fine if the operator chose it deliberately
    (e.g. test deployments on a different volume); but on the live VM
    a mismatch typically means the .env carries a stale value from a
    prior migration. Same remediation wire as the relative case.
    """
    global _RELATIVE_DATA_DIR_ALERTED  # share the dedupe latch
    if _RELATIVE_DATA_DIR_ALERTED:
        return
    _RELATIVE_DATA_DIR_ALERTED = True
    logger.warning(
        "paths: DATA_DIR=%r is absolute but differs from the canonical "
        "systemd-declared value %s. This may be intentional (test deploy, "
        "alternative mount) — but on the live VM it usually means the .env "
        "carries a stale value. If unexpected, dispatch the fix-data-dir "
        "operator-action to strip the .env override.",
        umbrella, _CANONICAL_DATA_DIR_HINT,
    )
    _swallow_paths_warning(
        "data_dir_non_canonical",
        umbrella=umbrella,
        canonical_hint=_CANONICAL_DATA_DIR_HINT,
    )


def _resolve_root(subdir: str) -> Path:
    """Resolve a logical root by name, honoring env overrides.

    See module docstring for the resolution order. Always returns a
    directory that exists (or raises if no candidate is writable).
    """
    env_name = _ENV_PER_ROOT.get(subdir)
    if env_name and (override := os.environ.get(env_name)):
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            # Same anchor-to-repo_root rationale as the umbrella branch
            # below — a relative per-root override would be CWD-dependent.
            candidate = Path(repo_root()) / candidate
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    umbrella = os.environ.get(_ENV_UMBRELLA)
    if umbrella:
        # 2026-05-11 incident: the live VM's .env had ``DATA_DIR=data/``
        # (a relative path). Without the is_absolute() check, this used
        # to return ``Path("data/runtime_logs")`` — a relative path
        # whose actual location depended on each consumer's CWD at the
        # moment of read/write. Trader writes used the trader's CWD;
        # web-api reads used the web-api's CWD; status-check diagnostic
        # used the operator-action wrapper's CWD. Result: writer-vs-
        # reader path divergence even though both consumers called the
        # SAME ``runtime_logs_dir()`` helper. Anchor relative umbrella
        # paths to repo_root so the resolved path is absolute and
        # process-CWD-independent.
        #
        # 2026-05-12 incident (this fix): anchoring made the path
        # absolute but it was still the WRONG absolute path —
        # ``<repo>/data/runtime_logs/`` instead of the canonical
        # ``/data/bot-data/runtime_logs/`` the systemd drop-ins
        # declare. Reader-vs-writer split-brain continued because
        # consumers driven by the systemd drop-in's DATA_DIR (e.g.
        # health-snapshot collector, watchdog) looked at the
        # canonical path while consumers reading the .env relative
        # value resolved here. The alert below makes the
        # misalignment loud; the fix-data-dir operator-action is
        # the remediation wire.
        umbrella_root = Path(umbrella).expanduser()
        if not umbrella_root.is_absolute():
            _alert_on_relative_data_dir(
                umbrella, Path(repo_root()) / umbrella_root,
            )
            umbrella_root = Path(repo_root()) / umbrella_root
        elif str(umbrella_root) != _CANONICAL_DATA_DIR_HINT:
            _alert_on_data_dir_mismatch(umbrella)
        candidate = umbrella_root / subdir
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


_ENV_TRADE_JOURNAL_DB = "TRADE_JOURNAL_DB"
_TRADE_JOURNAL_DB_BASENAME = "trade_journal.db"


def trade_journal_db_path() -> str:
    """Return the canonical absolute path to the trade-journal SQLite DB.

    This is the ONE resolver every Python caller must use instead of the
    historical ``os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"``
    idiom. That bare-CWD fallback is what produced the stray duplicate
    journals on the live VM (``/home/ubuntu/ict-trading-bot/trade_journal.db``
    and ``src/bot/trade_journal.db``): any process that started without the
    systemd ``TRADE_JOURNAL_DB`` env wrote a fresh DB relative to its CWD.
    The shell side already has the equivalent ``runtime_db_path`` in
    ``scripts/ops/_lib.sh``; this is its Python twin.

    Resolution order (most authoritative first):
      1. ``TRADE_JOURNAL_DB`` env — exact path. The live services pin this
         to ``/data/bot-data/trade_journal.db`` via the systemd drop-in.
      2. ``$DATA_DIR/trade_journal.db`` — the umbrella data root (canonical
         ``/data/bot-data`` on the OCI block-storage mount).
      3. ``<repo_root>/trade_journal.db`` — last-resort dev/test fallback,
         anchored to the repo root (NEVER the process CWD).

    Always returns an absolute path. Never a bare relative basename.
    """
    env = os.environ.get(_ENV_TRADE_JOURNAL_DB)
    if env:
        return str(Path(env).expanduser())

    umbrella = os.environ.get(_ENV_UMBRELLA)
    if umbrella:
        umbrella_root = Path(umbrella).expanduser()
        if not umbrella_root.is_absolute():
            # Same anchor-to-repo_root rationale as _resolve_root: a
            # relative DATA_DIR must not resolve against the process CWD.
            _alert_on_relative_data_dir(
                umbrella, Path(repo_root()) / umbrella_root / _TRADE_JOURNAL_DB_BASENAME,
            )
            umbrella_root = Path(repo_root()) / umbrella_root
        return str(umbrella_root / _TRADE_JOURNAL_DB_BASENAME)

    return str(Path(repo_root()) / _TRADE_JOURNAL_DB_BASENAME)


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
