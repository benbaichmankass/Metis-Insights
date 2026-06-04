"""Per-bar regime scoring (S-MLOPT-S13 / Phase 3.1).

Closes ``MB-20260529-001``. Today shadow regime predictions fire **only**
when a strategy emits an actionable buy/sell signal (see
``src.runtime.strategy_signal_builders._emit_shadow_preds``), so the strong
regime heads — the 1h BTC head and the MES heads, whose strategies rarely or
never fire — accrue **zero** shadow track record and can never earn a
``shadow -> advisory`` promotion on order-influencing evidence. The S9
range-vol (yz) heads promoted to ``shadow`` on 2026-06-04 hit the same wall.

This module scores every ``shadow``-stage **regime** head on its **own**
``(symbol, timeframe)`` bar cadence, independent of any signal. Each tick it:

1. discovers the ``shadow``-stage models from the registry (the same
   auto-wire source the signal-time path uses);
2. keeps only the **regime** heads (those carrying a frozen ``regime_spec``);
3. fetches each head's own market candles (BTC -> Bybit, MES -> IBKR, routed
   by ``connector_for_symbol``);
4. computes the live ``vol_bucket`` against the edges frozen in the model
   state — reusing ``src.runtime.regime_shadow.feature_row_for_predictor`` so
   the per-bar feature row is **identical** to the signal-time one; and
5. logs a shadow prediction to the same
   ``runtime_logs/shadow_predictions.jsonl`` the signal-time path writes, so
   ``/api/bot/shadow/*``, ``/api/bot/trades/scores`` and the ``gate-check``
   ``shadow_soak`` criterion pick it up unchanged.

**Observe-only by construction.** It only calls ``ShadowPredictor.predict``
(which appends to the audit log); there is no code path from here to an order
package or the risk manager — same WS7 contract as the signal-time shadow
emitter.

**Write-rate control.** Each regime head is scored at most **once per closed
bar** (dedup by ``(model_id, last_bar_timestamp)`` in a per-process cache), so
calling this every tick never floods the log between bars.

**Never raises.** Any failure is logged and the tick proceeds — the per-bar
path must not be able to break the trading loop.

**Kill-switch.** Set ``REGIME_BAR_SCORING_DISABLED`` truthy to turn the path
off without a redeploy (mirrors ``SIGNAL_DUAL_WRITE_DISABLED``). Default off ->
per-bar scoring on.

**Feature-parity caveat (Phase 4.2, not S13).** The live regime feature row
carries ``vol_bucket`` + one vol value; the heads also train on range-vol
estimators, log-return lags and time features, which are absent live and become
NaN (LightGBM handles NaN natively). For the yz heads the frozen
``vol_feature_column`` is ``yang_zhang_vol`` but the live value is close-to-close
vol. This gap is **pre-existing and shared with the signal-time path / the v2
heads** — S13 deliberately reuses the same computation so per-bar == signal-time.
Closing the parity gap is train/serve parity (Phase 4.2 / ``MB-20260604-005``)
and must land before any head is promoted ``shadow -> advisory`` on this
evidence.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

# Per-process dedup cache: model_id -> last scored bar timestamp. A head is
# scored once per new closed bar; a process restart (every deploy) clears it.
_REGIME_BAR_SEEN: dict[str, Any] = {}

# Per-process predictor cache, keyed by (registry_root, tuple(model_ids)) so a
# config reload or registry promotion that changes the resolved shadow set gets
# a fresh resolution — mirrors the signal-builder cache semantics.
_PREDICTOR_CACHE: dict = {}


def regime_bar_scoring_enabled() -> bool:
    """True unless ``REGIME_BAR_SCORING_DISABLED`` is truthy.

    Default-on so a newly-promoted shadow regime head starts accruing a
    per-bar track record without any extra wiring; flip the env var on the
    live VM to disable without a redeploy.
    """
    raw = str(os.environ.get("REGIME_BAR_SCORING_DISABLED", "")).strip().lower()  # allow-silent: observe-only shadow-logging kill-switch, default-ON (inverse of the BUG-039 default-OFF capability gate); never touches the live/dry order path (RiskManager.dry_run stays the only execution switch)
    return raw not in ("1", "true", "yes", "on")


def _last_bar_timestamp(candles_df: Any) -> Any:
    """Return the most recent bar's ``timestamp`` for dedup, or ``None``.

    Duck-typed like ``regime_shadow.closes_from_candles`` so it tolerates a
    pandas DataFrame (live path) or a list-of-dict rows (test path) without a
    hard pandas dependency.
    """
    if candles_df is None:
        return None
    try:
        col = candles_df["timestamp"]
    except Exception:  # noqa: BLE001 — not subscriptable / no column
        return None
    try:
        values = col.tolist()  # pandas Series / numpy array
    except AttributeError:
        try:
            values = list(col)
        except TypeError:
            return None
    if not values:
        return None
    return values[-1]


def _resolve_regime_predictors(registry_root: Any, log_path: Any) -> list:
    """Resolve the cached list of shadow-stage predictors from the registry.

    Returns the full shadow set (regime + non-regime); the caller filters to
    regime heads. Never raises — any failure yields an empty list.
    """
    from pathlib import Path

    from ml.registry.model_registry import ModelRegistry
    from ml.shadow.factory import (
        discover_shadow_stage_model_ids,
        resolve_predictors,
    )

    registry = ModelRegistry(Path(registry_root))
    ids = discover_shadow_stage_model_ids(registry)
    if not ids:
        return []
    cache_key = (str(registry_root), tuple(ids))
    cached = _PREDICTOR_CACHE.get(cache_key)
    if cached is None:
        cached = resolve_predictors(list(ids), registry, log_path=Path(log_path))
        _PREDICTOR_CACHE[cache_key] = cached
    return cached


def emit_regime_bar_predictions(
    settings: Mapping[str, Any] | None = None,
    *,
    registry_root: Any = None,
    log_path: Any = None,
    predictors: list | None = None,
    fetch_fn: Callable[[str, str], Any] | None = None,
    seen: dict | None = None,
) -> int:
    """Score every shadow-stage regime head on its own bar cadence.

    Parameters
    ----------
    settings:
        Pipeline settings — used only to route the per-symbol candle
        connector (``connector_for_symbol``). May be ``None``.
    registry_root, log_path:
        Override the registry root / shadow-prediction log path. Default to
        the same locations the signal-time path uses
        (``ml.shadow.factory.DEFAULT_REGISTRY_ROOT`` and
        ``runtime_logs_dir()/shadow_predictions.jsonl``).
    predictors:
        Injected predictor list (tests). When ``None`` the shadow set is
        resolved + cached from the registry.
    fetch_fn:
        Injected ``(symbol, timeframe) -> candles`` fetcher (tests). When
        ``None`` the live ``connector_for_symbol`` + ``fetch_candles`` path is
        used.
    seen:
        Injected dedup cache (tests). When ``None`` the module-level
        ``_REGIME_BAR_SEEN`` is used.

    Returns
    -------
    int
        The number of regime predictions written this call (heads skipped by
        dedup, market/vol mismatch, or fetch failure are not counted).
    """
    if not regime_bar_scoring_enabled():
        return 0
    try:
        from src.runtime.regime_shadow import (
            closes_from_candles,
            feature_row_for_predictor,
            regime_spec_of,
        )
        from src.runtime.shadow_adapter import with_shadow_preds

        if predictors is None:
            from pathlib import Path

            from ml.shadow.factory import DEFAULT_REGISTRY_ROOT
            from src.utils.paths import runtime_logs_dir

            root = registry_root or DEFAULT_REGISTRY_ROOT
            lp = (
                Path(log_path)
                if log_path is not None
                else runtime_logs_dir() / "shadow_predictions.jsonl"
            )
            predictors = _resolve_regime_predictors(root, lp)
        if not predictors:
            return 0

        if fetch_fn is None:
            fetch_fn = _live_fetch_fn(settings or {})
        cache = _REGIME_BAR_SEEN if seen is None else seen

        written = 0
        for predictor in predictors:
            spec = regime_spec_of(predictor)
            if spec is None:
                continue  # non-regime model — scored on the signal-time path
            symbol = str(spec.get("symbol") or "")
            timeframe = str(spec.get("timeframe") or "")
            if not symbol or not timeframe:
                continue
            try:
                candles_df = fetch_fn(symbol, timeframe)
            except Exception:  # noqa: BLE001 — degrade, never break the tick
                logger.warning(
                    "regime_bar: candle fetch failed for %s %s",
                    symbol, timeframe, exc_info=False,
                )
                continue
            if candles_df is None:
                continue
            model_id = getattr(predictor, "model_id", None) or id(predictor)
            bar_ts = _last_bar_timestamp(candles_df)
            if bar_ts is not None and cache.get(model_id) == bar_ts:
                continue  # already scored this closed bar — write-rate control
            closes = closes_from_candles(candles_df)
            base_row = {
                "symbol": symbol,
                "timeframe": timeframe,
                "event_source": "per_bar",
            }
            row = feature_row_for_predictor(
                predictor, base_row, closes=closes,
                symbol=symbol, timeframe=timeframe,
            )
            if row is None:
                continue  # mismatch / uncomputable vol — skip, don't log noise
            # One predictor per call preserves with_shadow_preds' per-model
            # try/except isolation + ShadowPredictor type-check. The carrier
            # ({}) is returned unchanged — there is no decision here.
            with_shadow_preds({}, predictors=[predictor], feature_row=row)
            cache[model_id] = bar_ts
            written += 1
        return written
    except Exception:  # noqa: BLE001 — the per-bar path must never break a tick
        logger.warning("regime_bar: per-bar regime scoring failed", exc_info=False)
        return 0


def _live_fetch_fn(settings: Mapping[str, Any]) -> Callable[[str, str], Any]:
    """Build the live ``(symbol, timeframe) -> candles`` fetcher.

    Routes each symbol to the exchange the strategies trade it on
    (``connector_for_symbol``: BTC -> Bybit, MES -> IBKR) and fetches enough
    bars to cover the vol window.
    """
    from src.runtime.market_data import connector_for_symbol, fetch_candles

    def _fetch(symbol: str, timeframe: str) -> Any:
        client = connector_for_symbol(symbol, dict(settings))
        return fetch_candles(
            symbol, timeframe, exchange_client=client, limit=120,
        )

    return _fetch
