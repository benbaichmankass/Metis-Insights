"""YAML-driven strategy registry (S-007 PR #113).

Single source of truth for which strategies exist, which systemd service
runs each one, where the model artifact lives (if any), and which
signal_type substrings identify each strategy's rows in the signals DB.

Public API
----------
load_strategies()     -> list[dict]    one dict per strategy; keys: name, service, model, signal_prefixes
model_path(name)      -> str | None   absolute path to .joblib, or None
service_name(name)    -> str          systemd unit stem, e.g. "ict-trader-breakout"
signal_prefixes(name) -> list[str]    signal_type substrings for DB attribution, e.g. ["ml_breakout"]
"""
from __future__ import annotations

import os
from typing import Any

import yaml

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_YAML_PATH = os.path.join(_REPO_ROOT, "config", "strategies.yaml")
_MODELS_DIR = os.path.join(_REPO_ROOT, "models")

_cache: list[dict] | None = None


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
        result.append({
            "name": str(name),
            "service": str(cfg.get("service") or f"ict-trader-{name}"),
            "model": cfg.get("model") or None,
            "signal_prefixes": [str(p) for p in raw_prefixes] if raw_prefixes else [],
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
