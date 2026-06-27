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

**Per-tick cost control (MB-20260609-001).** On the 2-core live VM the
unoptimised path saturated CPU and starved the trader loop into a ~30-40 min
wedge on 2026-06-09. Two changes here keep the per-tick cost bounded:

1. **Predictors are grouped by ``(symbol, timeframe)``** — multiple shadow
   regime heads on the same market (e.g. ``btc-regime-5m-v2`` +
   ``btc-regime-5m-lgbm-yz-v1``) share ONE network fetch and ONE shared
   ``closes`` view per tick instead of fetching the same Bybit/IBKR candles
   per head. The per-head call into ``feature_row_for_predictor`` is still
   independent (each head has its own frozen ``edges``/``labels``/
   ``vol_feature_column``) but it runs over already-fetched data.
2. **Wall-clock fetch gate** — per ``(symbol, timeframe)``, the network
   fetch is skipped until ``bar_duration - 30 s`` have elapsed since the
   last fetch (``_BAR_SECONDS`` + ``_FETCH_GATE_BUFFER_S``). A 1h-cadence
   head is therefore fetched ~1×/hour instead of 60×; a 5m-cadence head
   ~1×/5min instead of 5×. A failed fetch does NOT arm the gate, so a
   transient exchange blip doesn't silence a head for a full bar duration.

The exchange client is also cached per ``emit_regime_bar_predictions`` call
inside ``_live_fetch_fn`` so multiple timeframes on the same symbol share
one connector (the IB path's connect-probe + circuit-breaker is real cost).

**Never raises.** Any failure is logged and the tick proceeds — the per-bar
path must not be able to break the trading loop.

**Kill-switch.** Set ``REGIME_BAR_SCORING_DISABLED`` truthy to turn the path
off without a redeploy (mirrors ``SIGNAL_DUAL_WRITE_DISABLED``). Default off ->
per-bar scoring on.

**Feature parity — CLOSED (S-MLOPT-S17 / ``MB-20260604-005``).** The live
regime feature row is now the FULL ``market_features`` superset the heads
train on: ``vol_bucket`` + ``rolling_log_return_vol`` + the four range-vol
estimators (parkinson/garman_klass/rogers_satchell/yang_zhang) + ``log_return``
+ ``log_return_lag_1/2`` + ``hour_of_day``/``dayofweek``, built live from the
fetched OHLC by ``regime_shadow.feature_row_for_predictor`` (both this per-bar
caller AND the signal-time caller pass ``candles_df``). The estimators are the
SAME pure fns as the offline builder (``ml.datasets.volatility_estimators``)
over the same ``vol_window_n`` — verified live (diag #4511): the yz heads' live
``yang_zhang_vol`` is populated, not NaN. The earlier "estimators absent → NaN /
yz served close-to-close vol" caveat was the PRE-S17 state and is no longer true.

**Open calibration gap (MB-20260625-001 / MB-20260623-001) — the real
shadow→advisory gate.** Feature parity being closed did NOT make the BTC yz
heads non-degenerate live: they lock to ``vol_bucket=vol_b0`` while their
v2/baseline siblings bucket the same bar to b1/b2 (btc-1h @ yang_zhang_vol
0.006493 → yz b0 / v2 b2 on one bar). The yz ``vol_bucket_edges`` are frozen
quantiles of the *training-period* ``yang_zhang_vol`` (recency-weighted
``half_life_days: 60``), which sits above the current calm-regime live values →
the most important categorical feature is near-constant → the head saturates
(~0.92-0.98). The fix is a re-freeze of the yz edges on the live-consistent
distribution (then re-gate); the parity-landing condition above is satisfied,
the edge calibration is the new gate before any yz head is (re-)promoted
``shadow -> advisory``.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

# Per-process dedup cache: model_id -> last scored bar timestamp. A head is
# scored once per new closed bar; a process restart (every deploy) clears it.
_REGIME_BAR_SEEN: dict[str, Any] = {}

# Per-process wall-clock fetch gate: (symbol, timeframe) -> last fetch epoch s.
# A second tick within the same bar window short-circuits BEFORE the network
# fetch — the dominant CPU cost on the 2-core live VM (MB-20260609-001 / the
# 2026-06-09 wedge). Cleared on process restart like _REGIME_BAR_SEEN.
_REGIME_FETCH_WALL: dict[tuple[str, str], float] = {}

# Per-process predictor cache, keyed by (registry_root, tuple(model_ids)) so a
# config reload or registry promotion that changes the resolved shadow set gets
# a fresh resolution — mirrors the signal-builder cache semantics.
_PREDICTOR_CACHE: dict = {}

# Nominal seconds-per-bar for the timeframes the live regime heads use. The
# wall-clock fetch gate uses ``duration - _FETCH_GATE_BUFFER_S`` so the next
# fetch lands a few seconds before the bar actually closes (catching the new
# closed bar on its first eligible tick). Unknown timeframes fall through the
# gate (return True → fetch every tick) — the old behaviour, so a new TF can't
# silently strand a head.
_BAR_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
}
_FETCH_GATE_BUFFER_S: float = 30.0


def regime_bar_scoring_enabled() -> bool:
    """True unless ``REGIME_BAR_SCORING_DISABLED`` is truthy.

    Default-on so a newly-promoted shadow regime head starts accruing a
    per-bar track record without any extra wiring; flip the env var on the
    live VM to disable without a redeploy.
    """
    raw = str(os.environ.get("REGIME_BAR_SCORING_DISABLED", "")).strip().lower()  # allow-silent: observe-only shadow-logging kill-switch, default-ON (inverse of the BUG-039 default-OFF capability gate); never touches the live/dry order path (RiskManager.dry_run stays the only execution switch)
    return raw not in ("1", "true", "yes", "on")


# Per-tick wall-clock budget (seconds) for the whole emit_regime_bar_predictions
# call. The fetch gate + dedup caches are PER-PROCESS and EMPTY on a fresh
# restart, so the FIRST tick after a restart would otherwise fetch every
# (symbol, timeframe) group (incl. blocking IBKR fetches for MES) AND score
# every shadow head synchronously on the main loop — a single mega-tick that
# pegs the 2-core live VM and freezes the heartbeat (BL-20260609-001 / the
# 2026-06-10 cold-start wedge). The budget caps how long one call may run:
# once exceeded, remaining groups are deferred to the next tick (their fetch
# gate is NOT armed and their heads are NOT marked seen, so they are picked up
# whole next tick), spreading the cold-start burst across ticks instead of
# stalling the loop. Default 6 s; ``0`` disables the budget (unlimited).
_DEFAULT_BUDGET_S: float = 6.0


def _budget_seconds() -> float:
    """Per-tick wall-clock budget from ``REGIME_BAR_SCORING_BUDGET_S``.

    Default ``_DEFAULT_BUDGET_S``; ``0`` (or negative) means unlimited — the
    pre-budget behaviour. A non-numeric value falls back to the default so a
    typo can never strand the budget.
    """
    raw = os.environ.get("REGIME_BAR_SCORING_BUDGET_S")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_BUDGET_S
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BUDGET_S
    return val if val > 0 else 0.0


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


def _bar_seconds(timeframe: str) -> int | None:
    """Nominal seconds per bar for ``timeframe`` (lowercase lookup)."""
    return _BAR_SECONDS.get(str(timeframe or "").strip().lower())


def _should_fetch_now(
    symbol: str,
    timeframe: str,
    wall_cache: dict[tuple[str, str], float],
    now: float,
) -> bool:
    """Wall-clock fetch gate — True when a fresh fetch is due for ``(symbol, timeframe)``.

    The dominant CPU cost in per-bar scoring is the per-tick network fetch
    (``connector_for_symbol`` + ``get_ohlcv``), not the LGBM inference: rebuilt
    every 60 s for every shadow regime head before this gate landed
    (MB-20260609-001 / the 2026-06-09 wedge). After a successful fetch at wall
    time ``T``, this returns ``False`` until ``T + dur - buffer`` — so a 1h
    head is fetched once per hour instead of 60 times, and a 5m head once per
    ~5 min instead of 5 times. An unknown timeframe (no entry in
    ``_BAR_SECONDS``) returns ``True`` so a newly-added cadence cannot strand
    a head silently.
    """
    dur = _bar_seconds(timeframe)
    if dur is None:
        return True  # unknown cadence — fall back to per-tick fetch
    last = wall_cache.get((symbol, timeframe))
    if last is None:
        return True
    min_interval = max(0.0, float(dur) - _FETCH_GATE_BUFFER_S)
    return (now - last) >= min_interval


def _discover_advisory_stage_model_ids(registry: Any) -> list:
    """Return the ``advisory``-stage model_ids from the registry.

    Parallel to ``discover_shadow_stage_model_ids`` (which keeps only
    ``shadow``) — the advisory heads are NOT in that set, so the per-bar
    scorer never saw them before Design A. Filtering on the canonical stage
    (``ml.manifest.canonical_stage``) so a legacy alias still resolves. Never
    raises — any failure yields an empty list.
    """
    from ml.manifest import canonical_stage

    ids: list[str] = []
    try:
        for entry in registry.list():
            try:
                stage = canonical_stage(entry.target_deployment_stage)
            except Exception:  # noqa: BLE001 — skip an unrecognized stage row
                continue
            if stage == "advisory":
                ids.append(entry.model_id)
    except Exception:  # noqa: BLE001 — degrade to no advisory heads
        return []
    return sorted(ids)


def _resolve_regime_predictors(registry_root: Any, log_path: Any) -> list:
    """Resolve the cached list of shadow- AND advisory-stage predictors.

    Returns the full influence set (regime + non-regime, ``shadow`` +
    ``advisory``); the caller filters to regime heads. The advisory heads are
    included so Design A's per-bar publish path scores them too (the shadow-log
    write is unchanged; advisory heads additionally publish ``P(volatile)`` for
    ``ml_vol_verdict``). Never raises — any failure yields an empty list.
    """
    from pathlib import Path

    from ml.registry.model_registry import ModelRegistry
    from ml.shadow.factory import (
        discover_shadow_stage_model_ids,
        resolve_predictors,
    )

    registry = ModelRegistry(Path(registry_root))
    shadow_ids = discover_shadow_stage_model_ids(registry)
    advisory_ids = _discover_advisory_stage_model_ids(registry)
    # Union, de-duped + stable-ordered so the cache key is deterministic.
    ids = sorted(set(shadow_ids) | set(advisory_ids))
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
    wall_cache: dict[tuple[str, str], float] | None = None,
    now: Callable[[], float] | None = None,
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
    wall_cache:
        Injected wall-clock fetch cache (tests). When ``None`` the
        module-level ``_REGIME_FETCH_WALL`` is used. The gate skips the
        per-(symbol, timeframe) network fetch between bar closes — the
        2026-06-09 CPU-wedge fix (MB-20260609-001).
    now:
        Wall-clock source (tests). When ``None`` ``time.time`` is used.

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
        wcache = _REGIME_FETCH_WALL if wall_cache is None else wall_cache
        now_fn = time.time if now is None else now

        # Group predictors by (symbol, timeframe) — multiple shadow regime
        # heads on the same market share one network fetch + one parity
        # feature build per tick. Pre-grouping the old per-predictor loop
        # repeated both N times (the 2026-06-09 wedge driver). Skips
        # non-regime / mis-specified heads early so the grouping is clean.
        groups: dict[tuple[str, str], list] = {}
        for predictor in predictors:
            spec = regime_spec_of(predictor)
            if spec is None:
                continue  # non-regime model — scored on the signal-time path
            symbol = str(spec.get("symbol") or "")
            timeframe = str(spec.get("timeframe") or "")
            if not symbol or not timeframe:
                continue
            groups.setdefault((symbol, timeframe), []).append(predictor)

        written = 0
        budget_s = _budget_seconds()
        tick_start = float(now_fn())
        deferred = 0
        for (symbol, timeframe), group in groups.items():
            current_now = float(now_fn())
            # Per-tick wall-clock budget — defer the rest of the cold-start
            # burst to the next tick rather than stall the main loop (and
            # freeze the heartbeat) doing every group in one mega-tick. Checked
            # at group granularity so a deferred group is never half-scored:
            # its fetch gate stays un-armed + its heads stay unseen, so the
            # whole group is picked up on a later tick. (BL-20260609-001
            # cold-start fix.)
            if budget_s > 0.0 and (current_now - tick_start) >= budget_s:
                deferred = 1
                break
            # Wall-clock fetch gate: skip the (expensive) network fetch when
            # the previous fetch landed less than ``bar_duration - buffer``
            # seconds ago. ``_BAR_SECONDS`` maps the timeframes the regime
            # heads use; an unknown TF is treated permissively (always fetch).
            if not _should_fetch_now(symbol, timeframe, wcache, current_now):
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
            # Mark fetch wall-clock only AFTER a candle frame is in hand, so
            # a transient fetch failure does not delay the next retry.
            wcache[(symbol, timeframe)] = current_now
            bar_ts = _last_bar_timestamp(candles_df)
            # Pre-compute the shared closes view once per (symbol, timeframe)
            # — the per-head ``feature_row_for_predictor`` re-derives the OHLC
            # subset for its bucket-by-edges step, but the inputs are stable
            # across heads on the same market.
            closes = closes_from_candles(candles_df)
            base_row = {
                "symbol": symbol,
                "timeframe": timeframe,
                "event_source": "per_bar",
            }
            # Cross-asset peer-feature block (S-CROSS-ASSET-PROBE D2a): computed
            # ONCE per (symbol, timeframe) group, only when a head in the group
            # trained on xa_* columns AND the symbol has configured peers. Reuses
            # the same gated ``fetch_fn`` (peers ride the target's fetch cadence).
            # Fail-permissive → None, leaving the head's xa columns missing (NaN)
            # — the honest degraded state the LightGBM head handles. Observe-only.
            cross_asset_row = None
            try:
                from src.runtime.cross_asset_live import (
                    compute_live_cross_asset_row,
                    group_needs_cross_asset,
                )

                if group_needs_cross_asset(symbol, group):
                    cross_asset_row = compute_live_cross_asset_row(
                        symbol, timeframe, candles_df, fetch_fn,
                    )
            except Exception:  # noqa: BLE001 — never break the tick
                cross_asset_row = None
            for predictor in group:
                model_id = getattr(predictor, "model_id", None) or id(predictor)
                if bar_ts is not None and cache.get(model_id) == bar_ts:
                    continue  # already scored this closed bar — write-rate control
                row = feature_row_for_predictor(
                    predictor, base_row, closes=closes,
                    symbol=symbol, timeframe=timeframe, candles_df=candles_df,
                    cross_asset_row=cross_asset_row,
                )
                if row is None:
                    continue  # mismatch / uncomputable vol — skip, don't log noise
                # One predictor per call preserves with_shadow_preds' per-model
                # try/except isolation + ShadowPredictor type-check. The carrier
                # ({}) is returned unchanged — there is no decision here.
                with_shadow_preds({}, predictors=[predictor], feature_row=row)
                # Design A: advisory regime heads ALSO publish their per-bar
                # P(volatile) into the ml_vol_verdict cache so the decision path
                # reads it without a fetch. Reuses the row already built above —
                # no extra fetch, no extra feature build. Observe-only +
                # fail-permissive: a publish failure never breaks the tick.
                _maybe_publish_p_volatile(predictor, row, bar_ts)
                cache[model_id] = bar_ts
                written += 1
        if deferred:
            logger.info(
                "regime_bar: per-tick budget %.1fs reached after %d preds; "
                "remaining groups deferred to next tick",
                budget_s, written,
            )
        return written
    except Exception:  # noqa: BLE001 — the per-bar path must never break a tick
        logger.warning("regime_bar: per-bar regime scoring failed", exc_info=False)
        return 0


# Stage on which a head's per-bar P(volatile) is published for ml_vol_verdict
# (Design A). Shadow-stage heads keep their existing shadow-log-only behaviour;
# only advisory heads additionally publish into the verdict cache.
_PUBLISH_STAGE = "advisory"

# The 2-class regime label whose probability the vol verdict thresholds.
_VOLATILE_CLASS = "volatile"


def _maybe_publish_p_volatile(predictor: Any, row: Mapping[str, Any], bar_ts: Any) -> None:
    """Publish an ADVISORY regime head's per-bar ``P(volatile)`` (Design A).

    Reuses the already-built ``row`` — no extra fetch / feature build — to read
    the ``volatile`` class probability off ``predictor.wrapped.predict_proba``
    and publish it into ``ml_vol_verdict``'s in-process cache. A shadow-stage
    head is skipped (it only writes the shadow log, as before). Observe-only +
    fail-permissive: any failure (no ``predict_proba``, non-advisory stage,
    import error) is swallowed — the per-bar tick must never break.
    """
    try:
        stage = getattr(predictor, "stage", None)
        if stage != _PUBLISH_STAGE:
            return
        wrapped = getattr(predictor, "wrapped", None) or predictor
        proba_fn = getattr(wrapped, "predict_proba", None)
        if proba_fn is None:
            return
        proba = proba_fn(row)
        if not proba:
            return
        p_vol = proba.get(_VOLATILE_CLASS)
        p_vol = float(p_vol) if p_vol is not None else None
        model_id = getattr(predictor, "model_id", None)
        if not model_id:
            return
        from src.runtime.regime.ml_vol_verdict import publish_p_volatile

        publish_p_volatile(model_id, bar_ts, p_vol)
    except Exception:  # noqa: BLE001 — observe-only publish, never break a tick
        return


def _live_fetch_fn(settings: Mapping[str, Any]) -> Callable[[str, str], Any]:
    """Build the live ``(symbol, timeframe) -> candles`` fetcher.

    Routes each symbol to the exchange the strategies trade it on
    (``connector_for_symbol``: BTC -> Bybit, MES -> IBKR) and fetches enough
    bars to cover the vol window. The exchange client is cached **per call to
    ``emit_regime_bar_predictions``** so multiple timeframes on the same symbol
    (e.g. BTCUSDT 5m / 15m / 1h) share one connector — ``connector_for_symbol``
    instantiates a fresh exchange client every call otherwise, which is a real
    cost on the IB path (probe + circuit-breaker check).
    """
    from src.runtime.market_data import connector_for_symbol, fetch_candles

    routed = dict(settings)
    client_cache: dict[str, Any] = {}

    def _fetch(symbol: str, timeframe: str) -> Any:
        client = client_cache.get(symbol)
        if client is None:
            client = connector_for_symbol(symbol, routed)
            client_cache[symbol] = client
        return fetch_candles(
            symbol, timeframe, exchange_client=client, limit=120,
        )

    return _fetch
