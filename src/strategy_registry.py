"""YAML-driven strategy registry (S-007 PR #113; S-012 PR C4 single-process).

Single source of truth for which strategies exist, where the model artifact
lives (if any), and which signal_type substrings identify each strategy's
rows in the signals DB.

S-012 PR C4: per-strategy systemd services are gone (PM § 8 #1). Every
strategy now runs inside ``ict-trader-live`` so ``service_name()`` returns
``"ict-trader-live"`` for every name and the YAMLs no longer carry a
``service:`` field. The function and field are retained for backwards
compatibility with consumers that still ask; both can be deleted in a
future sprint once all callers migrate.

Public API
----------
load_strategies()     -> list[dict]    one dict per strategy; keys: name, service, model, signal_prefixes
model_path(name)      -> str | None   absolute path to .joblib, or None
service_name(name)    -> str          systemd unit stem; always "ict-trader-live" post-S-012 PR C4
signal_prefixes(name) -> list[str]    signal_type substrings for DB attribution, e.g. ["ml_breakout"]
"""
from __future__ import annotations

import os
from typing import Any

import yaml

from src.utils.paths import repo_root as _repo_root

_REPO_ROOT = _repo_root()
_YAML_PATH = os.path.join(_REPO_ROOT, "config", "strategies.yaml")
_MODELS_DIR = os.path.join(_REPO_ROOT, "models")

# S-012 PR C4: every strategy runs in the single live trader process.
_DEFAULT_TRADER_SERVICE = "ict-trader-live"

_cache: list[dict] | None = None

# Valid strategy-level execution gate values (S9). "live" = eligible to
# execute; "shadow" = run + log order packages but never send a live
# order (data-only). Default is "live" (permissive — omitting the field
# never strands capability).
_VALID_EXECUTION = ("live", "shadow")


def _norm_execution(value: Any) -> str:
    """Normalise a strategy's ``execution`` field to ``live`` / ``shadow``.

    Unknown / missing values fall back to ``live`` (the permissive
    default) rather than raising, so a typo can never silently strand a
    strategy into a non-executing state — the failure mode this gate is
    explicitly designed to avoid.
    """
    v = str(value or "live").strip().lower()
    return v if v in _VALID_EXECUTION else "live"


def _load_yaml(path: str = _YAML_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_strategies(path: str = _YAML_PATH) -> list[dict]:
    """Return one dict per strategy defined in config/strategies.yaml.

    Each dict has: ``name`` (str), ``service`` (str), ``model`` (str | None).
    Results are cached after the first successful load; pass an explicit
    *path* to bypass the cache (useful in tests).
    """
    global _cache
    if path == _YAML_PATH and _cache is not None:
        return _cache

    data = _load_yaml(path)
    raw = data.get("strategies") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"strategies.yaml: expected mapping under 'strategies', got {type(raw)}")

    result = []
    for name, cfg in raw.items():
        cfg = cfg or {}
        raw_prefixes = cfg.get("signal_prefixes") or []
        # S-012 PR C4: default service is ict-trader-live (single-process).
        # YAML may still set an explicit service for backwards-compatible
        # synthetic test fixtures; production YAMLs drop the field entirely.
        result.append({
            "name": str(name),
            "service": str(cfg.get("service") or _DEFAULT_TRADER_SERVICE),
            "model": cfg.get("model") or None,
            "signal_prefixes": [str(p) for p in raw_prefixes] if raw_prefixes else [],
            # Strategy-level execution gate (S9). "live" (default) →
            # order packages are eligible to execute on accounts that
            # route the strategy; "shadow" → the strategy still RUNS and
            # LOGS its order packages everywhere (data collection) but
            # never sends a live order. Permissive default (live), so
            # omitting it strands nothing — a strategy is only demoted to
            # data-only by an explicit `execution: shadow`. Enforced in
            # Coordinator.multi_account_execute (folded into effective_dry).
            "execution": _norm_execution(cfg.get("execution")),
        })

    if path == _YAML_PATH:
        _cache = result
    return result


def _strategy_cfg(name: str, path: str = _YAML_PATH) -> dict:
    strategies = load_strategies(path)
    for s in strategies:
        if s["name"] == name:
            return s
    raise KeyError(f"Strategy '{name}' not found in registry")


def model_path(name: str, path: str = _YAML_PATH) -> str | None:
    """Return the absolute path to the model artifact for *name*, or None.

    The path is ``<repo_root>/models/<model>`` where ``model`` is the value
    in strategies.yaml.  Returns None when the strategy has no model.
    """
    cfg = _strategy_cfg(name, path)
    model_file = cfg.get("model")
    if not model_file:
        return None
    return os.path.join(_MODELS_DIR, model_file)


def service_name(name: str, path: str = _YAML_PATH) -> str:
    """Return the systemd service stem for *name* (e.g. 'ict-trader-breakout')."""
    return _strategy_cfg(name, path)["service"]


def signal_prefixes(name: str, path: str = _YAML_PATH) -> list[str]:
    """Return signal_type substrings used to attribute DB rows to *name*.

    Used by data_loaders to filter the signals table and by pipeline to pick
    the primary signal_type when writing a trade signal.  Returns an empty
    list when no prefixes are configured.
    """
    return list(_strategy_cfg(name, path).get("signal_prefixes") or [])


def execution_mode(name: str, path: str = _YAML_PATH) -> str:
    """Return the strategy-level execution gate for *name* (S9).

    ``"live"`` (default) → the strategy's order packages are eligible to
    execute on the accounts that route it. ``"shadow"`` → the strategy
    runs and LOGS its order packages everywhere but never sends a live
    order (data-only). Unknown strategies resolve to ``"live"`` (the
    permissive default) so a routing/config slip never silently parks a
    strategy in a non-executing state.

    Enforced in ``Coordinator.multi_account_execute`` by folding
    ``execution == "shadow"`` into the per-account ``effective_dry``
    resolution — the same place the per-account ``mode: dry_run`` gate is
    applied. Reuses the existing, tested dry-run short-circuit; adds no
    new order-submission code path.
    """
    try:
        return _norm_execution(_strategy_cfg(name, path).get("execution"))
    except KeyError:
        return "live"
