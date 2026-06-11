"""Intent-aware multiplexer — collects intents from ALL enabled strategies
and aggregates them into one BTC/USDT target per tick.

Replaces the first-wins multiplexer in
``src/runtime/pipeline.py::multiplexed_signal_builder`` when the
``MULTI_STRATEGY_INTENT_LAYER`` env var (or the equivalent settings key)
is enabled. The legacy builder is preserved as the default so this PR
does not change live behaviour on its own — the operator opts in at
their cadence.

Pipeline contract
-----------------
Same shape as ``multiplexed_signal_builder``: returns a dict with
``{symbol, side, price, stop_loss, take_profit, meta}`` (or a side="none"
no-op when no strategy fires). The downstream pipeline / dispatcher /
risk gates are unchanged.

Scope (matches ``intents.py``)
------------------------------
- BTC/USDT only.
- Strategies in scope: turtle_soup, vwap. Adding a third strategy means
  registering its signal builder via ``register_intent_builder()`` (or
  letting the registry-driven loader pick it up — see
  ``_default_intent_builders``) and adding a priority entry in
  ``intents.DEFAULT_PRIORITIES``.

Risk-layer invariant
--------------------
This module decides **what** to do (which strategy's entry / what
target). It does not decide **how much** — the per-account RiskManager
in ``src/units/accounts/risk.py`` still owns sizing and the daily-loss /
margin / position-size caps. The aggregated signal carries a
``meta["aggregated_target_qty"]`` hint (the winning intent's target) but
the RiskManager is free to ignore or trim it.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from src.runtime.intents import (
    DEFAULT_PRIORITIES,
    DesiredPosition,
    StrategyIntent,
    aggregate_intents,
    intent_from_signal,
)
from src.runtime.runtime_flags import is_strategy_paused
from src.runtime.strategy_signal_builders import (
    fade_breakout_4h_signal_builder,
    fvg_range_15m_signal_builder,
    htf_pullback_trend_2h_signal_builder,
    mes_trend_long_1d_signal_builder,
    xauusd_trend_1h_signal_builder,
    spy_trend_long_1d_signal_builder,
    qqq_trend_long_1d_signal_builder,
    gld_pullback_1d_signal_builder,
    eth_pullback_2h_signal_builder,
    mgc_pullback_1d_signal_builder,
    mhg_pullback_1d_signal_builder,
    squeeze_breakout_4h_signal_builder,
    ict_scalp_signal_builder,
    trend_donchian_1h_signal_builder,
    trend_donchian_signal_builder,
    turtle_soup_signal_builder,
    vwap_signal_builder,
)

logger = logging.getLogger(__name__)


# Signature: builder(settings: dict) -> pipeline-shape signal dict
IntentBuilder = Callable[[dict], Dict[str, Any]]


def _default_intent_builders() -> Dict[str, IntentBuilder]:
    """Strategy name → signal builder for the in-scope strategies.

    Mirrors ``pipeline._STRATEGY_BUILDERS`` to keep the rosters in sync.
    Adding ICT scalping (or any future strategy) means appending a row
    here AND registering it via the registry-driven loader. The
    intent-layer aggregator itself does not change.
    """
    return {
        "turtle_soup": turtle_soup_signal_builder,
        "vwap": vwap_signal_builder,
        # ict_scalp_5m: live since 2026-05-14 (PR #1156, operator-
        # approved post pre-live gate). The builder honours the YAML
        # `enabled` flag as single source of truth; this registration
        # plumbs it through the intent layer when enabled.
        "ict_scalp_5m": ict_scalp_signal_builder,
        # trend_donchian: Donchian-breakout trend-follower going live on
        # bybit_2 (real money) per the 2026-05-23 go-live plan. Builder
        # honours the YAML `enabled` flag; priority 20 (below the others)
        # so a wiring slip can't override the established roster.
        "trend_donchian": trend_donchian_signal_builder,
        # fade_breakout_4h: the trend-follower's mirror, wired
        # execution:shadow (S9, 2026-05-24) for live data collection —
        # never sends a live order. Builder honours the YAML `enabled`
        # flag; priority 10 (lowest on the roster).
        "fade_breakout_4h": fade_breakout_4h_signal_builder,
        # squeeze_breakout_4h: validated member-#3, execution:shadow
        # (S9, 2026-05-24) for live data collection; priority 5 (floor).
        "squeeze_breakout_4h": squeeze_breakout_4h_signal_builder,
        # fvg_range_15m: the range member (FVG mean-reversion in a confirmed
        # static range), execution:shadow (2026-05-30) for live data
        # collection; priority 3 (new floor) so a wiring slip can't override
        # an established member. Builder honours the YAML `enabled` flag.
        "fvg_range_15m": fvg_range_15m_signal_builder,
        # htf_pullback_trend_2h: overnight-research HTF-pullback trend-follower,
        # execution:shadow (2026-06-01) for live data collection; priority 2
        # (new floor) so a wiring slip can't override an established member.
        # Builder honours the YAML `enabled` flag.
        "htf_pullback_trend_2h": htf_pullback_trend_2h_signal_builder,
        # trend_donchian_1h: faster-TF/wider-trail shadow A/B of the live 2h
        # trend_donchian, execution:shadow (2026-06-01) for live data
        # collection; priority 1 (new floor) so a wiring slip can't override an
        # established member. Builder honours the YAML `enabled` flag.
        "trend_donchian_1h": trend_donchian_1h_signal_builder,
        # mes_trend_long_1d: MES daily long-only equity-index diversifier on
        # IBKR ib_paper, execution:shadow (2026-06-01); priority 0 (new floor).
        # Builder honours the YAML `enabled` flag.
        "mes_trend_long_1d": mes_trend_long_1d_signal_builder,
        # xauusd_trend_1h: gold 1h trend-follower on oanda_practice (M15
        # Phase 3) — the Phase-0 sweep's strongest cell. Sole strategy on
        # its (symbol, account) so arbitration never fires.
        "xauusd_trend_1h": xauusd_trend_1h_signal_builder,
        # M15 Phase 4 buildout — the daily ETF futures-replacements on
        # alpaca_paper (paper money): SPY/QQQ long-only trend (MES mirror)
        # + GLD pullback (MGC mirror). Each is the sole strategy on its
        # symbol, so arbitration never fires.
        "spy_trend_long_1d": spy_trend_long_1d_signal_builder,
        "qqq_trend_long_1d": qqq_trend_long_1d_signal_builder,
        "gld_pullback_1d": gld_pullback_1d_signal_builder,
        # eth_pullback_2h — M15 WS-C alt sleeve (2026-06-11): ETH/USDT 2h
        # HTF-pullback on bybit_1 (demo, paper money), reusing the
        # htf_pullback_trend_2h unit at the live BTC params. Sole strategy
        # on its symbol, so arbitration never fires.
        "eth_pullback_2h": eth_pullback_2h_signal_builder,
        # mgc_pullback_1d / mhg_pullback_1d: the WS-A metals sleeve (2026-06-02).
        # Micro Gold + Micro Copper daily HTF-pullback diversifiers on IBKR
        # ib_paper, execution: live (paper money). Each runs ALONE on its own
        # symbol so its priority never arbitrates. Builders honour the YAML
        # `enabled` flag.
        "mgc_pullback_1d": mgc_pullback_1d_signal_builder,
        "mhg_pullback_1d": mhg_pullback_1d_signal_builder,
    }


# Process-level registry so tests and downstream consumers can plug
# additional builders in without monkeypatching. Reset between tests if
# isolation is required.
_REGISTERED_BUILDERS: Dict[str, IntentBuilder] = {}


def register_intent_builder(strategy: str, builder: IntentBuilder) -> None:
    """Register an additional strategy → builder mapping.

    Used by tests to demonstrate that a future strategy (e.g.
    ``ict_scalp``) can plug into the same interface without code
    changes in the aggregator. In production, the default registry
    (``_default_intent_builders``) is sufficient.
    """
    _REGISTERED_BUILDERS[strategy] = builder


def clear_registered_intent_builders() -> None:
    """Test helper — drops process-level registrations."""
    _REGISTERED_BUILDERS.clear()


def _resolve_builders() -> Dict[str, IntentBuilder]:
    merged = dict(_default_intent_builders())
    merged.update(_REGISTERED_BUILDERS)
    return merged


def _strategies_from_registry() -> List[str]:
    """Same source-of-truth as the legacy multiplexer: config/strategies.yaml."""
    try:
        from src.strategy_registry import load_strategies
        return [s["name"] for s in load_strategies()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intent_multiplexer: registry unavailable (%s) — falling back to "
            "hardcoded ['turtle_soup', 'vwap']",
            exc,
        )
        return ["turtle_soup", "vwap"]


def _strategy_symbol_scope() -> Dict[str, set]:
    """``{strategy_name: {declared symbols}}`` from config/strategies.yaml.

    Per-strategy symbol scope (2026-06-02, WS-A metals sleeve). A strategy
    only trades the symbols its ``symbols:`` list declares. Strategies with
    no ``symbols`` are OMITTED here so the call site treats them permissively
    (single-symbol / legacy / test setups dispatch exactly as before).
    Permissive on a config-load failure (returns ``{}`` → every strategy
    falls through). Symbols are normalised (upper, ``/`` stripped) to match
    ``intents.SUPPORTED_SYMBOLS``.
    """
    try:
        from src.units.strategies import load_strategy_config
        cfg = load_strategy_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intent_multiplexer: strategy-symbol scope unavailable (%s) — "
            "per-strategy symbol gate disabled this tick (permissive)",
            exc,
        )
        return {}
    scope: Dict[str, set] = {}
    for name, c in (cfg.items() if isinstance(cfg, dict) else []):
        syms = (c or {}).get("symbols")
        if syms:
            scope[name] = {str(s).upper().replace("/", "") for s in syms}
    return scope


def _collect_intents(
    settings: dict,
    *,
    builders: Dict[str, IntentBuilder],
    strategies: List[str],
    target_qty_hint: float,
) -> List[StrategyIntent]:
    """Run every enabled strategy and collect its intent for this tick.

    A strategy that returns ``side="none"`` contributes no intent. A
    strategy that raises is logged + skipped (same isolation contract
    the legacy multiplexer uses). A strategy whose declared ``symbols:``
    do not include this tick's symbol is skipped BEFORE evaluation — see
    ``_strategy_symbol_scope`` — so e.g. ``mgc_pullback_1d`` (gold) never
    evaluates/emits on MES or copper, and a higher-priority crypto strategy
    can't win a metal's aggregation only to be undeliverable. The gate is
    at emission, not dispatch, precisely so it can't suppress the
    legitimate per-symbol owner.
    """
    intents: List[StrategyIntent] = []
    now = time.time()
    tick_symbol = str(
        settings.get("SYMBOL") or settings.get("symbol") or ""
    ).upper().replace("/", "")
    symbol_scope = _strategy_symbol_scope()
    for name in strategies:
        if is_strategy_paused(name):
            logger.info(
                "intent_multiplexer: '%s' paused via runtime flag — skipping",
                name,
            )
            continue
        # Per-strategy symbol scope: skip a strategy that does not declare
        # this tick's symbol. Permissive when the strategy declares no
        # ``symbols`` (absent from ``symbol_scope``) or the tick symbol is
        # unknown — so single-symbol accounts (bybit_2 = BTCUSDT) are
        # unaffected.
        declared = symbol_scope.get(name)
        if tick_symbol and declared and tick_symbol not in declared:
            logger.debug(
                "intent_multiplexer: '%s' not configured for symbol=%s "
                "(trades %s) — skipping",
                name, tick_symbol, sorted(declared),
            )
            continue
        builder = builders.get(name)
        if builder is None:
            logger.warning(
                "intent_multiplexer: unknown strategy '%s' (no builder registered) "
                "— skipping",
                name,
            )
            continue
        try:
            signal = builder(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "intent_multiplexer: strategy '%s' raised %s — skipping this tick",
                name, exc,
            )
            continue

        intent = intent_from_signal(
            signal,
            strategy=name,
            target_qty=target_qty_hint,
            priority=DEFAULT_PRIORITIES.get(name),
            timestamp=now,
        )
        if intent is None:
            logger.info(
                "intent_multiplexer: '%s' returned no actionable signal",
                name,
            )
            continue
        logger.info(
            "intent_multiplexer: '%s' emitted intent side=%s target_qty=%.6f "
            "priority=%d",
            name, intent.side, intent.target_qty, intent.effective_priority(),
        )
        intents.append(intent)
    return intents


def _desired_to_pipeline_signal(
    desired: DesiredPosition,
    *,
    symbol: str,
    settings: dict,
) -> Dict[str, Any]:
    """Render a ``DesiredPosition`` into the pipeline-shape signal dict.

    A "flat" / no-vote outcome becomes a ``side="none"`` no-op so the
    pipeline's existing skip path picks it up cleanly.
    """
    if desired.side == "flat" or desired.winning_intent is None:
        return {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "multiplexed_intents",
                "reason": desired.reason,
                "aggregation": desired.meta,
            },
        }

    winning = desired.winning_intent
    side = "buy" if desired.side == "long" else "sell"
    contributing = sorted({i.strategy for i in desired.contributing_intents})

    # Carry through the winning intent's entry/sl/tp + the contributing
    # strategies so downstream consumers (signal_writer, audit log,
    # OrderPackage builder) have everything they need.
    meta = dict(winning.meta or {})
    meta.update({
        # The winning strategy's own meta probably already set
        # strategy_name — overwrite with the aggregator's label so the
        # operator can tell this signal flowed through the intent
        # layer.
        "strategy_name": winning.strategy,
        "aggregated_via": "multi_strategy_intent_layer",
        "aggregated_target_qty": desired.target_qty,
        "aggregation": desired.meta,
        "contributing_strategies": contributing,
        "aggregation_reason": desired.reason,
        "winning_priority": winning.effective_priority(),
    })
    # Preserve the per-strategy risk allocation that the legacy
    # multiplexer recorded — downstream sizing reads
    # ``meta["strategy_risk_pct"]`` per S-026 G2. Pull it from the
    # winning strategy's signal meta so we never accidentally apply the
    # losing strategy's allocation.
    if "strategy_risk_pct" not in meta:
        try:
            from src.runtime.pipeline import STRATEGY_RISK_PCT
            meta["strategy_risk_pct"] = float(
                STRATEGY_RISK_PCT.get(winning.strategy, 1.0)
            )
        except Exception:  # noqa: BLE001
            meta["strategy_risk_pct"] = 1.0

    return {
        "symbol": symbol,
        "side": side,
        "price": winning.entry,
        "entry_price": winning.entry,
        "stop_loss": winning.sl,
        "take_profit": winning.tp,
        "confidence": winning.confidence,
        "meta": meta,
    }


def intent_multiplexer_enabled(settings: dict) -> bool:
    """Operator opt-out for the intent-aware multiplexer.

    D-1: default flipped to **on** (2026-05-17). The legacy first-wins
    multiplexer let a turtle_soup signal on the same tick globally
    suppress a vwap signal, so vwap never reached bybit_2 (its only
    trading account) when both fired together. Intent layer produces
    one DesiredPosition per strategy; each routes to its assigned
    account per ``account.strategies``.

    Set ``MULTI_STRATEGY_INTENT_LAYER=false`` (env or settings dict)
    to roll back to the legacy path without a code change.
    """
    raw = (
        settings.get("MULTI_STRATEGY_INTENT_LAYER")
        if isinstance(settings, dict)
        else None
    )
    if raw is None:
        raw = os.environ.get("MULTI_STRATEGY_INTENT_LAYER", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def multiplexed_intent_signal_builder(
    settings: dict,
    *,
    builders: Optional[Dict[str, IntentBuilder]] = None,
    strategies: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Intent-aware replacement for ``multiplexed_signal_builder``.

    Runs every enabled strategy, collects intents, aggregates them,
    and returns ONE pipeline-shape signal dict — the same shape the
    legacy first-wins multiplexer returned, so downstream consumers
    don't notice the swap.

    Parameters
    ----------
    settings : dict
        Pipeline settings (same as the legacy multiplexer).
    builders : dict, optional
        Override the strategy → builder map. Tests use this to inject a
        third strategy (e.g. ``ict_scalp``) and verify the aggregator
        accepts it without code changes.
    strategies : list, optional
        Override the strategy order. Defaults to the registry order.

    Returns
    -------
    dict
        Pipeline signal: ``{symbol, side, price, stop_loss, take_profit,
        meta}``. ``side="none"`` when no strategy votes for a position.
    """
    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    resolved_builders = builders if builders is not None else _resolve_builders()
    resolved_strategies = strategies if strategies is not None else _strategies_from_registry()

    # target_qty=0 — the aggregator carries entry/sl/tp through to the
    # downstream OrderPackage but the actual per-account qty is settled
    # by RiskManager.position_size. The intent layer never decides qty.
    intents = _collect_intents(
        settings,
        builders=resolved_builders,
        strategies=resolved_strategies,
        target_qty_hint=0.0,
    )

    if not intents:
        logger.info("intent_multiplexer: no strategy emitted an intent — staying flat")
        return {
            "symbol": symbol,
            "side": "none",
            "meta": {
                "strategy_name": "multiplexed_intents",
                "reason": "no_intents_emitted",
            },
        }

    desired = aggregate_intents(intents, symbol=symbol)
    logger.info(
        "intent_multiplexer: aggregated %d intents → side=%s target_qty=%s "
        "reason=%s",
        len(intents), desired.side, desired.target_qty, desired.reason,
    )
    return _desired_to_pipeline_signal(desired, symbol=symbol, settings=settings)
