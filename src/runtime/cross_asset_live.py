"""Live cross-asset (peer-asset) feature computation (S-CROSS-ASSET-PROBE D2a).

Computes the ``xa_*`` peer-asset feature block at score time for a target symbol,
so the cross-asset regime head (``eth-regime-1h-lgbm-xasset-v1``) sees the SAME
features live that it trained on. The math is **not** re-implemented here — it
reuses the offline pure function
``ml.datasets.cross_asset_features.compute_cross_asset_feature_rows`` verbatim, so
live == train by construction.

**Observe-only.** This only conditions a shadow-stage regime head's feature row;
nothing here touches the order path. Wired into the per-bar regime scorer
(`src/runtime/regime_bar_scoring.py`), which is itself observe-only.

**Fail-permissive + bounded.** A peer fetch/compute error returns ``None`` (the
caller then merges nothing → the head scores its xa columns as NaN, which the
LightGBM predictor handles as missing — the honest degraded state, NOT a
fabricated all-zero vector the model never saw in training). Peers are fetched
with the SAME injected fetcher the regime scorer uses, only on the ticks the
scorer already fetches the target (the wall-clock fetch gate decides that), so no
extra fetch cadence is introduced.

**Kill switch:** ``CROSS_ASSET_LIVE_DISABLED`` truthy → the peer config resolves
empty and every head degrades to NaN xa (i.e. the feature merge is a no-op).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Sequence

from ml.datasets.cross_asset_features import (
    CROSS_ASSET_FEATURE_COLUMNS,
    N_PEER_SLOTS,
    compute_cross_asset_feature_rows,
)


def cross_asset_live_disabled() -> bool:
    """``CROSS_ASSET_LIVE_DISABLED`` truthy → skip live cross-asset features."""
    raw = os.environ.get("CROSS_ASSET_LIVE_DISABLED", "").strip().lower()  # allow-silent: observe-only shadow-feature kill-switch, default-ON (inverse of the BUG-039 default-OFF capability gate); only conditions a shadow regime head's features, never the live/dry order path (RiskManager.dry_run stays the only execution switch)
    return raw in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# peer config (config/cross_asset.yaml) — cached, mtime-refreshed
# --------------------------------------------------------------------------- #
_cfg_lock = threading.Lock()
_cfg_cache: dict[str, list[str]] | None = None
_cfg_mtime: float | None = None


def _config_path() -> str:
    try:
        from src.utils.paths import repo_root

        return os.path.join(repo_root(), "config", "cross_asset.yaml")
    except Exception:
        return ""


def load_peer_config() -> dict[str, list[str]]:
    """``{target_symbol: [peer1, peer2, …]}`` from config/cross_asset.yaml.

    Cached + mtime-refreshed. Empty on absence/parse-error OR when the kill
    switch is set (so every head degrades to NaN xa). Fail-permissive.
    """
    global _cfg_cache, _cfg_mtime
    if cross_asset_live_disabled():
        return {}
    path = _config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {} if _cfg_cache is None else _cfg_cache
    with _cfg_lock:
        if _cfg_cache is None or mtime != _cfg_mtime:
            _cfg_cache = _parse_config(path)
            _cfg_mtime = mtime
        return _cfg_cache


def _parse_config(path: str) -> dict[str, list[str]]:
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for target, peers in raw.items():
            if isinstance(peers, (list, tuple)):
                cleaned = [str(p) for p in peers if p][:N_PEER_SLOTS]
                if cleaned:
                    out[str(target)] = cleaned
    return out


def peers_for(symbol: str) -> list[str]:
    """Configured peer symbols for ``symbol`` (≤ N_PEER_SLOTS), or ``[]``."""
    return list(load_peer_config().get(str(symbol), []))


def head_wants_cross_asset(predictor: Any) -> bool:
    """True if ``predictor``'s model trained on any ``xa_*`` feature column.

    Digs to the wrapped LightGBM predictor's frozen feature list. Fail-permissive
    (any introspection error → False, so a head we can't classify is simply not
    fed cross-asset features rather than crashing the scorer).
    """
    try:
        wrapped = predictor
        # Unwrap the ShadowPredictor / any thin wrapper exposing `.wrapped`.
        for attr in ("wrapped", "_wrapped"):
            inner = getattr(wrapped, attr, None)
            if inner is not None:
                wrapped = inner
        cols = getattr(wrapped, "_feature_columns", None) or getattr(
            wrapped, "feature_columns", None) or []
        return any(str(c).startswith("xa_") for c in cols)
    except Exception:
        return False


def _candles_to_rows(candles_df: Any) -> list[dict[str, Any]]:
    """market_raw-shaped ``[{ts, close}, …]`` from a candles frame.

    Reuses the regime-shadow column extractor for duck-typed frames. Pairs each
    close with its bar timestamp so the pure cross-asset fn can align peers onto
    the target grid by ts. Best-effort: returns ``[]`` on any extraction error.
    """
    try:
        from src.runtime.regime_shadow import _column_floats

        closes = _column_floats(candles_df, "close")
        ts = _timestamps(candles_df)
        m = min(len(closes), len(ts))
        if m == 0:
            return []
        return [{"ts": ts[i], "close": closes[i]} for i in range(m)]
    except Exception:
        return []


def _timestamps(candles_df: Any) -> list[str]:
    """Best-effort ISO timestamps aligned to the close series.

    Accepts a pandas-like frame with a ``time``/``timestamp``/``date`` column or
    a DatetimeIndex. Returns ``str`` per bar; empty on failure.
    """
    try:
        for col in ("time", "timestamp", "date", "ts"):
            if hasattr(candles_df, "columns") and col in getattr(
                    candles_df, "columns", []):
                return [str(v) for v in candles_df[col].tolist()]
        idx = getattr(candles_df, "index", None)
        if idx is not None:
            return [str(v) for v in list(idx)]
    except Exception:
        return []
    return []


def compute_live_cross_asset_row(
    symbol: str,
    timeframe: str,
    target_candles: Any,
    fetch_fn: Callable[[str, str], Any],
    *,
    vol_window_n: int = 20,
    beta_window_n: int = 50,
) -> dict[str, float] | None:
    """The current bar's ``xa_*`` feature dict for ``symbol``, or ``None``.

    Fetches each configured peer's candles via ``fetch_fn`` (the SAME injected
    fetcher the regime scorer uses), builds market_raw-shaped rows for the target
    + peers, and runs the offline pure cross-asset fn. Returns the LAST (current)
    row's xa columns (sans ``ts``). ``None`` when the kill switch is set, no peers
    are configured, the target rows are unusable, or no peer produced data — the
    caller then leaves the head's xa columns missing (NaN), the honest degraded
    state.
    """
    peers = peers_for(symbol)
    if not peers:
        return None
    target_rows = _candles_to_rows(target_candles)
    if not target_rows:
        return None
    peer_rows_by_slot: list[list[dict[str, Any]]] = []
    any_peer = False
    for peer in peers[:N_PEER_SLOTS]:
        try:
            pc = fetch_fn(peer, timeframe)
        except Exception:
            pc = None
        rows = _candles_to_rows(pc) if pc is not None else []
        if rows:
            any_peer = True
        peer_rows_by_slot.append(rows)
    if not any_peer:
        return None
    try:
        out_rows = compute_cross_asset_feature_rows(
            target_rows, peer_rows_by_slot,
            vol_window_n=vol_window_n, beta_window_n=beta_window_n,
        )
    except Exception:
        return None
    if not out_rows:
        return None
    last = out_rows[-1]
    return {c: float(last[c]) for c in CROSS_ASSET_FEATURE_COLUMNS if c in last}


def group_needs_cross_asset(symbol: str, predictors: Sequence[Any]) -> bool:
    """True if ``symbol`` has configured peers AND a head in the group wants xa.

    The cheap pre-check the scorer uses to decide whether to fetch peers for a
    ``(symbol, timeframe)`` group at all.
    """
    if not peers_for(symbol):
        return False
    return any(head_wants_cross_asset(p) for p in predictors)


__all__ = [
    "cross_asset_live_disabled",
    "load_peer_config",
    "peers_for",
    "head_wants_cross_asset",
    "group_needs_cross_asset",
    "compute_live_cross_asset_row",
]
