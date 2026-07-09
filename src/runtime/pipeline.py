from __future__ import annotations
from src.runtime.signal_writer import write_signal  # noqa: F401  (used in _write_ict_signals_from_meta tombstone)
# PR-9 / D1: signal/order helpers extracted to their canonical modules.
from src.runtime.signal_writer import _write_ict_signals_from_meta  # noqa: E402
from src.runtime.order_bridge import _signal_to_order_package  # noqa: E402
from src.runtime.runtime_flags import _centralized_allocator_enabled, is_strategy_paused  # noqa: E402 (D11)
from src.utils.signal_audit_logger import log_signal
from src.runtime.risk_counters import inject_runtime_counters, inject_per_strategy_counters
from src.news.news_pipeline import get_news_score
from src.news.news_client import is_active as news_is_active
from src.news.news_audit import log_news_decision
from src.news.news_symbols import query_for_tags
from src.news.news_events import event_risk_for_symbol
# PR-6: signal builder functions extracted to strategy_signal_builders.py.
# Re-exported here for back-compat (existing callers + tests import from pipeline).
from src.runtime.strategy_signal_builders import (  # noqa: E402
    fade_breakout_4h_signal_builder,
    fvg_range_15m_signal_builder,
    htf_pullback_trend_2h_signal_builder,
    mes_trend_long_1d_signal_builder,
    mgc_pullback_1d_signal_builder,
    mhg_pullback_1d_signal_builder,
    squeeze_breakout_4h_signal_builder,
    ict_scalp_signal_builder,
    trend_donchian_1h_signal_builder,
    trend_donchian_eth_signal_builder,
    trend_donchian_eth_prop_signal_builder,
    trend_donchian_sol_signal_builder,
    trend_donchian_sol_prop_signal_builder,
    trend_donchian_signal_builder,
    turtle_soup_signal_builder,
    vwap_signal_builder,
)

import os

HALT_FLAG_PATH = os.environ.get("HALT_FLAG_PATH", "/data/bot-data/trader_halt.flag")

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import logging  # noqa: E402
from typing import Any, Callable, Dict, Optional  # noqa: E402

from src.runtime.notify import send_to_operator  # noqa: E402
from src.runtime.outcomes import Level, report  # noqa: E402
from src.web.runtime_status import write_status  # noqa: E402
# PR-8 / D1: formatting helpers extracted to pipeline_result.py.
# PR-8 / D1: strategy-monocle DB-gate helpers extracted to strategy_monocle.py.
from src.runtime.strategy_monocle import (  # noqa: E402
    _refusal_cooldown_seconds,  # noqa: F401  (re-export: tests import from pipeline)
    _has_open_package_for_strategy,
    _recent_refusal_for_strategy,
    _same_bar_entry_for_strategy,
)

_OUTCOME_LEVEL_BY_STATUS: Dict[str, Level] = {
    # Happy / expected
    "submitted": Level.INFO,
    "multi_account_dispatched": Level.INFO,
    "dry_run": Level.INFO,
    "skipped": Level.INFO,
    "halted": Level.INFO,
    "news_veto": Level.INFO,
    "refused": Level.INFO,
    # Validation: bounded but a sign of upstream drift
    "failed_validation": Level.WARN,
    # Hard failures: page the operator
    "failed_exchange": Level.ERROR,
    "failed_dispatch": Level.ERROR,
    "error": Level.ERROR,
}


def _report_pipeline_outcome(result: Dict[str, Any], signal: Dict[str, Any]) -> None:
    """Translate the run_pipeline result dict into an outcomes.report() call.

    Never raises. Centralizes the status → level mapping so individual
    sites in the pipeline don't have to care about alerting.
    """
    try:
        status = str((result or {}).get("status") or "unknown")
        level = _OUTCOME_LEVEL_BY_STATUS.get(status, Level.ERROR)
        meta = (signal or {}).get("meta") or {}
        report(
            "pipeline_order",
            status,
            level=level,
            reason=(result or {}).get("reason"),
            symbol=(signal or {}).get("symbol"),
            side=(signal or {}).get("side"),
            qty=(signal or {}).get("qty"),
            strategy=meta.get("strategy_name"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("outcomes reporting failed")

logger = logging.getLogger(__name__)



def _multi_account_dispatch_enabled(settings: dict) -> bool:
    """Return True when pipeline signals should fan out to every account.

    Default flipped to **true** (post-CP-2026-05-02): the architecture is
    "strategy produces signal; each account decides whether to live-trade
    that signal." Per-account ``mode: live | dry_run`` in
    ``config/accounts.yaml`` (operator directive 2026-05-03) is the only
    dry/live toggle in the codebase — checked inside
    ``RiskManager.evaluate()``.

    Operator can still pin to the legacy single-client path by exporting
    ``MULTI_ACCOUNT_DISPATCH=false`` — used for single-account smoke
    deployments that don't want to load Coordinator.
    """
    raw = settings.get("MULTI_ACCOUNT_DISPATCH") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _signal_carries_full_sltp(signal: Dict[str, Any]) -> bool:
    """True only when the signal carries entry, sl, and tp at the top
    level (or under ``meta``). Same shape as the local
    ``_signal_packageable`` inside ``run_pipeline``; lifted to module
    scope so the G5 missing-sltp warning can re-use it without copying
    the predicate."""
    if not isinstance(signal, dict):
        return False
    _meta = signal.get("meta") or {}
    return (
        (signal.get("entry_price") or signal.get("price") or _meta.get("price")) is not None
        and (signal.get("stop_loss") or _meta.get("stop_loss") or _meta.get("sl")) is not None
        and (signal.get("take_profit") or _meta.get("take_profit") or _meta.get("tp")) is not None
    )


# turtle_soup_signal_builder and vwap_signal_builder are re-exported from
# src.runtime.strategy_signal_builders (PR-6 extraction).
#   turtle_soup_signal_builder → src/runtime/strategy_signal_builders.py
#   vwap_signal_builder        → src/runtime/strategy_signal_builders.py


# Ordered list of strategies tried in multiplexed mode; first actionable signal wins.
# Source of truth is config/strategies.yaml (S-007). Order in the YAML determines
# multiplexer priority. Falls back to the original hardcoded list if the registry
# cannot be loaded (e.g. missing pyyaml in a minimal deploy environment).
def _strategies_from_registry() -> list:
    try:
        from src.strategy_registry import load_strategies
        return [s["name"] for s in load_strategies()]
    except Exception as exc:
        logger.warning("pipeline: registry unavailable, using hardcoded STRATEGIES list: %s", exc)
        # S-012 PR C3: hardcoded fallback matches the production roster
        # in config/strategies.yaml after PR B1.
        return ["turtle_soup", "vwap"]


STRATEGIES = _strategies_from_registry()

# Per-strategy risk allocation was REMOVED 2026-06-29 (operator directive:
# sizing is the RiskManager's sole responsibility; a strategy carries no risk
# level — it only produces order packages). The old per-strategy risk
# multiplier (injected into the signal meta) is gone; trade-level size
# differentiation is now central + confidence-keyed inside
# ``RiskManager.position_size`` (``_confidence_scalar``). A leftover
# per-strategy risk meta value is ignored by the sizer. The
# ``strategy-risk-guard`` CI check forbids re-introducing a per-strategy risk
# field.

_STRATEGY_BUILDERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    "turtle_soup": turtle_soup_signal_builder,
    "vwap": vwap_signal_builder,
    # ict_scalp_5m is live since 2026-05-14 (PR #1156, operator-approved
    # post pre-live gate). The builder honours the YAML enabled flag as
    # single source of truth — flipping enabled=false short-circuits to
    # side="none" without code changes. Do not edit enabled here on the
    # basis of any stale-comment-driven claim; see config/strategies.yaml
    # § ict_scalp_5m STATUS block and the 2026-05-17 incident addendum.
    "ict_scalp_5m": ict_scalp_signal_builder,
    # trend_donchian — Donchian-breakout trend-follower going live on
    # bybit_2 (real money) per docs/sprint-plans/TREND-GOLIVE-PLAN-
    # 2026-05-23.md. Builder honours the YAML `enabled` flag as the
    # single source of truth.
    "trend_donchian": trend_donchian_signal_builder,
    # fade_breakout_4h — the trend-follower's mirror (fades failed
    # breakouts in chop). Wired execution:shadow (S9, 2026-05-24): runs +
    # logs on real ticks for data collection, never sends a live order.
    # Builder honours the YAML `enabled` flag.
    "fade_breakout_4h": fade_breakout_4h_signal_builder,
    # squeeze_breakout_4h — volatility-squeeze breakout, the validated
    # member-#3 candidate. Wired execution:shadow (S9, 2026-05-24) for
    # live data collection; never sends a live order. Honours `enabled`.
    "squeeze_breakout_4h": squeeze_breakout_4h_signal_builder,
    # htf_pullback_trend_2h — HTF-pullback trend-follower (overnight research
    # 2026-06-01). Wired execution:shadow for live data collection; never
    # sends a live order. Cleared net-of-fee + walk-forward + 3-fold + fee +
    # correlation. Honours the YAML `enabled` flag.
    "htf_pullback_trend_2h": htf_pullback_trend_2h_signal_builder,
    # trend_donchian_1h — faster-TF/wider-trail shadow A/B of the live 2h
    # trend_donchian (overnight research 2026-06-01). Distinct instance reusing
    # the trend_donchian unit via its own config; execution:shadow, never sends
    # a live order. Honours the YAML `enabled` flag.
    "trend_donchian_1h": trend_donchian_1h_signal_builder,
    # trend_donchian_sol / _eth — PROP-account alt variants (PB-20260616-004).
    # Reuse the trend_donchian unit on SOLUSDT / ETHUSDT, routed to the Breakout
    # manual-bridge account (ticket emit, no live exchange order). SOL execution:
    # live, ETH execution: shadow. Honour the YAML `enabled` flag.
    "trend_donchian_sol": trend_donchian_sol_signal_builder,
    "trend_donchian_eth": trend_donchian_eth_signal_builder,
    # SWAP-ROBUST prop exit variants (Unit C, Phase 0, 2026-06-29; DRAFT Tier-3).
    # Tightened-exit (trail_mult 3.5 / tp_r 6.0) prop-only siblings of
    # trend_donchian_sol/_eth on breakout_1; both execution: shadow (observe-only
    # soak until the prop EV/survival gate passes). Honour the YAML `enabled` flag.
    "trend_donchian_sol_prop": trend_donchian_sol_prop_signal_builder,
    "trend_donchian_eth_prop": trend_donchian_eth_prop_signal_builder,
    # mes_trend_long_1d — MES daily LONG-ONLY trend-follower (overnight research
    # 2026-06-01). BTC-uncorrelated equity-index diversifier on IBKR ib_paper;
    # reuses the trend_donchian unit, suppresses shorts. execution:shadow, never
    # sends a live order. Honours the YAML `enabled` flag.
    "mes_trend_long_1d": mes_trend_long_1d_signal_builder,
    # mgc_pullback_1d / mhg_pullback_1d — the WS-A metals sleeve (2026-06-02).
    # Micro Gold (MGC) + Micro Copper (MHG) daily HTF-pullback diversifiers on
    # IBKR ib_paper; both reuse the htf_pullback_trend_2h unit (trade both
    # directions — no long-only gate). execution: live (paper money; ib_paper
    # exists to test strategies so it executes). Honour the YAML `enabled` flag.
    "mgc_pullback_1d": mgc_pullback_1d_signal_builder,
    "mhg_pullback_1d": mhg_pullback_1d_signal_builder,
    # fvg_range_15m — FVG mean-reversion inside a confirmed STATIC horizontal
    # range (the range member the roster was missing; the deliberate opposite
    # of ict_scalp's directional FVG continuation). Wired execution:shadow
    # (2026-05-30) for live data collection; never sends a live order.
    # Honours the YAML `enabled` flag as the single source of truth.
    "fvg_range_15m": fvg_range_15m_signal_builder,
}


def monitor_unit_for(strategy_name: str) -> str:
    """Resolve a strategy name to the unit module that owns its ``monitor()``.

    Derived from the builder registry: an *aliased* strategy (a distinct
    config instance that REUSES a base unit via its signal builder — the WS-A
    metals + M15 equity/fx sleeves, ict_scalp_5m) carries a ``monitor_unit``
    attribute on its builder (declared next to the builder in
    ``strategy_signal_builders``); everything else is its own module. The
    order-monitor uses this so an aliased strategy's positions get the same
    active monitoring (break-even trail, thesis/level-cross exit, time-decay)
    as the base unit's own positions — without it they'd run on static SL/TP.
    The drift guard ``tests/test_strategy_monitor_unit_resolution.py`` fails
    CI if any registered strategy resolves to a module with no ``monitor()``.

    Roster note (BL-20260615-MGCNAKED): the IBKR/FX symbol sleeves
    (``mgc_trend_1h``, ``xauusd_trend_1h``, ``spy_trend_long_1d``,
    ``qqq_trend_long_1d``, ``gld_pullback_1d``, ``eth_pullback_2h``) live ONLY
    in the intent-layer roster (``intent_multiplexer``), not in the legacy
    ``_STRATEGY_BUILDERS`` below — signal generation runs through the intent
    layer (``MULTI_STRATEGY_INTENT_LAYER`` default on). Without consulting that
    superset roster, ``monitor_unit_for`` returned the strategy name verbatim
    for those sleeves, the order-monitor failed to import a same-name module
    (``No module named 'src.units.strategies.mgc_trend_1h'``), and their
    positions ran with NO active ``monitor()`` — only static SL/TP, which is
    how a netted IBKR MGC long drifted into a naked ``orphan_adopt``. We fall
    back to the intent-layer roster so the sleeves resolve to their owning unit
    (``trend_donchian`` / ``htf_pullback_trend_2h``) via the builder's
    ``monitor_unit`` tag.
    """
    builder = _STRATEGY_BUILDERS.get(strategy_name)
    if builder is None:
        # Superset roster: the intent layer carries the symbol sleeves that the
        # legacy multiplexer dict omits. Lazy import keeps the module-load order
        # cheap and avoids a circular import at pipeline import time.
        try:
            from src.runtime.intent_multiplexer import _resolve_builders
            builder = _resolve_builders().get(strategy_name)
        except Exception:  # noqa: BLE001 — fall back to same-name module
            builder = None
    return getattr(builder, "monitor_unit", strategy_name)


def multiplexed_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Loop STRATEGIES in order; return the first actionable signal.

    If a strategy raises an exception it is logged and skipped.
    Returns a side=none signal when no strategy fires.

    S-026 G1: signals carry no qty — sizing is the per-account
    RiskManager's job. Strategies carry no risk level (the per-strategy
    ``risk_pct`` multiplier was removed 2026-06-29); the RiskManager owns
    the per-trade size end-to-end (account basis × confidence scalar).
    """
    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    for strategy_name in STRATEGIES:
        if is_strategy_paused(strategy_name):
            logger.info("Multiplexer: '%s' paused via runtime flag — skipping", strategy_name)
            continue
        builder = _STRATEGY_BUILDERS.get(strategy_name)
        if builder is None:
            logger.warning("Multiplexer: unknown strategy '%s' — skipping", strategy_name)
            continue
        try:
            signal = builder(settings)
        except Exception as exc:
            logger.warning("Multiplexer: strategy '%s' raised %s — skipping", strategy_name, exc)
            report(
                "strategy_builder",
                "exception",
                level=Level.ERROR,
                reason=f"{type(exc).__name__}: {exc}",
                strategy=strategy_name,
            )
            continue

        if signal.get("side") in ("buy", "sell"):
            signal = dict(signal)
            # No per-strategy risk injection (removed 2026-06-29): sizing is
            # purely the RiskManager's account-level basis × confidence.
            signal["meta"] = dict(signal.get("meta") or {})
            logger.info(
                "Multiplexer: '%s' produced actionable signal", strategy_name,
            )
            return signal

        logger.info("Multiplexer: '%s' returned no actionable signal", strategy_name)

    logger.info("Multiplexer: no strategy fired — staying flat")
    return {"symbol": symbol, "side": "none",
            "meta": {"strategy_name": "multiplexed", "reason": "no_strategy_triggered"}}



def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """Pipeline adapter. Chooses strategy from STRATEGY env var.

    S-012 PR C5: roster is turtle_soup + vwap. Default is the multiplexer
    so unset / unknown values still iterate the active strategies.
    """
    logger.info("Pipeline start")

    # S-MLOPT-S13 (Phase 3.1): per-bar regime scoring. Fires once per tick so
    # every shadow-stage regime head logs predictions on its own
    # (symbol, timeframe) bar cadence — independent of whether a strategy
    # emits an actionable signal (closes MB-20260529-001). Observe-only: it
    # only writes shadow_predictions.jsonl, never the order path; dedup keeps
    # it to one record per closed bar; it never raises. Kill-switch:
    # REGIME_BAR_SCORING_DISABLED.
    try:
        from src.runtime.regime_bar_scoring import emit_regime_bar_predictions
        emit_regime_bar_predictions(settings)
    except Exception:  # noqa: BLE001 — observe-only hook must never break a tick
        logger.warning("per-bar regime scoring hook failed", exc_info=False)

    strategy_name = str(os.environ.get("STRATEGY", "multiplexed")).strip().lower()

    if signal_builder is not None:
        builder = signal_builder
    elif strategy_name in ("turtle_soup", "turtlesoup"):
        builder = turtle_soup_signal_builder
    elif strategy_name == "vwap":
        builder = vwap_signal_builder
    elif strategy_name in ("ict_scalp", "ict_scalp_5m"):
        # Opt-in via STRATEGY env var for diagnostics / single-strategy
        # backtests. The builder honours the YAML `enabled` flag; YAML
        # is live (enabled: true) since 2026-05-14 (PR #1156). Setting
        # STRATEGY=ict_scalp_5m alone is therefore sufficient to route
        # signals through the live order path; ensure that is intended
        # before using this env override outside backtest/diag contexts.
        builder = ict_scalp_signal_builder
    elif strategy_name in ("trend_donchian", "trend"):
        # Opt-in via STRATEGY env var for diagnostics / single-strategy
        # runs. The builder honours the YAML `enabled` flag; route signals
        # through the live order path only when that is intended.
        builder = trend_donchian_signal_builder
    elif strategy_name in ("multiplexed_intents", "multi_strategy_intents"):
        # Explicit opt-in via STRATEGY env var. Same effect as
        # setting MULTI_STRATEGY_INTENT_LAYER=true and leaving STRATEGY
        # at "multiplexed" — kept as an explicit alias for runbook
        # clarity ("STRATEGY=multiplexed_intents" reads better than a
        # separate flag).
        from src.runtime.intent_multiplexer import multiplexed_intent_signal_builder
        builder = multiplexed_intent_signal_builder
    else:
        # "multiplexed" or anything unknown → the intent-aware
        # multi-strategy multiplexer by DEFAULT. MULTI_STRATEGY_INTENT_LAYER
        # is **default ON** (flipped 2026-05-17, D-1; see
        # intent_multiplexer.intent_multiplexer_enabled which reads the env
        # default "true", and CLAUDE.md § Environment Variables). The legacy
        # first-wins multiplexer (multiplexed_signal_builder) is the rollback
        # path: export MULTI_STRATEGY_INTENT_LAYER=false to fall back to it
        # without a code change. See src/runtime/intent_multiplexer.py for the
        # contract.
        from src.runtime.intent_multiplexer import (
            intent_multiplexer_enabled,
            multiplexed_intent_signal_builder,
        )
        if intent_multiplexer_enabled(settings):
            builder = multiplexed_intent_signal_builder
            strategy_name = "multiplexed_intents"
        else:
            builder = multiplexed_signal_builder

    logger.info("Using strategy builder: %s", strategy_name)
    signal = builder(settings)
    _write_ict_signals_from_meta(signal, settings)

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", meta.get("entry_price", signal.get("price")))

        _strat_key = (meta.get("strategy_name") or strategy_name or "").lower()
        try:
            from src.strategy_registry import signal_prefixes as _sp
            _prefixes = _sp(_strat_key)
            _sig_type = _prefixes[0] if _prefixes else "trade_signal"
        except Exception:
            # Pre-S-007 fallback: preserves exact historical behaviour.
            _sig_type = (
                "ml_breakout" if _strat_key == "breakout_confirmation"
                else ("fvg" if meta.get("fvg") else "trade_signal")
            )

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type=_sig_type,
            direction="bullish" if signal.get("side") == "buy" else "bearish",
            price=float(price) if price is not None else None,
            timeframe=settings.get("TIMEFRAME", settings.get("timeframe", "unknown")),
            reason="Actionable pipeline signal",
            metadata=str(signal),
        )

    logger.info("Generated signal: %s", signal)

    if signal.get("side") not in ("buy", "sell"):
        logger.info("No actionable signal; skipping order placement.")
        result = {"status": "skipped", "reason": "no_signal", "signal": signal}
    elif os.path.exists(HALT_FLAG_PATH):
        logger.warning("Trader is HALTED — flag file present. Skipping order placement.")
        result = {"status": "halted", "reason": "halt_flag_active"}
    else:
        settings = inject_runtime_counters(settings, exchange_client)
        _strat_name = (signal.get("meta") or {}).get("strategy_name")
        if _strat_name:
            settings = inject_per_strategy_counters(settings, _strat_name)
        _sym = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
        _base = _sym.upper().split("/")[0]
        if _base.endswith("USDT"):
            _base = _base[:-4]
        _tags = list(dict.fromkeys(t for t in [_base, _sym] if t))
        news_result = get_news_score(settings, symbol_tags=_tags)

        # Stamp the news score onto the signal meta so it rides into pkg.meta
        # (order_bridge copies signal["meta"]) and the coordinator's reductive
        # news-influence sizing hook can read it without re-fetching. event_risk
        # comes from the economic-calendar source — how much an imminent
        # high-impact event could knock this trade off course (a consideration,
        # never a blackout; 0.0 when no event is in window).
        try:
            _event_risk, _event_meta = event_risk_for_symbol(_sym)
            signal.setdefault("meta", {})["news"] = {
                "adjustment": news_result.adjustment,
                "decision": news_result.decision,
                "item_count": news_result.item_count,
                "event_risk": _event_risk,
                "event": _event_meta,
            }
        except Exception:  # noqa: BLE001 — stamping must never affect the trade
            logger.debug("news meta stamp failed", exc_info=True)

        # Soak LOG (observe-only): record what the news layer decided on every
        # actionable signal once the layer is active, so we can validate the
        # decision against real trades. NB this logging is observe-only, but the
        # VETO below (line ~520) is NOT — when the source is active the veto
        # gates live money (per CLAUDE.md "selecting rss is the deliberate
        # activation"; NEWS_VETO_ENABLED default-on). The graduated influence
        # SIZING is the observe-until-opt-in half (NEWS_INFLUENCE_MODE, default off).
        try:
            if news_is_active(settings):
                log_news_decision(
                    result=news_result,
                    symbol=_sym,
                    side=signal.get("side"),
                    strategy=(signal.get("meta") or {}).get("strategy_name"),
                    query=query_for_tags(_tags),
                )
        except Exception:  # noqa: BLE001 — soak logging must never affect the trade
            logger.debug("news shadow-soak log failed", exc_info=True)

        if news_result.veto:
            logger.warning("news veto: %s", news_result.reason)
            result = {"status": "news_veto", "reason": news_result.reason, "signal": signal}
            _veto_msg = (
                f"\U0001f6ab News veto: {news_result.reason}\n"
                f"Symbol: {signal.get('symbol', '?')} | Side: {signal.get('side', '?')}"
                f" | Qty: {signal.get('qty', '?')}\n"
                f"Adj: {news_result.adjustment:.4f} | Items: {news_result.item_count}"
            )[:200]
            try:
                send_to_operator(_veto_msg, telegram_client=telegram_client)
            except Exception:
                logger.exception("news veto notify failed")
        else:
            logger.info(
                "news: decision=%s adj=%.4f items=%d reason=%s",
                news_result.decision,
                news_result.adjustment,
                news_result.item_count,
                news_result.reason[:80],
            )

            multi = _multi_account_dispatch_enabled(settings)

            # Operator directive 2026-05-03 — the per-account
            # ``RiskManager.dry_run`` flag is the only dry/live toggle
            # in the codebase. The legacy ``global_dry`` env-var check
            # was deleted; the multi-account fast-path now runs
            # whenever the signal carries full SL/TP, and each account
            # decides whether to live-trade it via its own RiskManager.
            #
            # G5 — the predicate moved to module scope as
            # ``_signal_carries_full_sltp`` so the missing-sltp warning
            # in the audit-log block uses the same definition.
            if multi and _signal_carries_full_sltp(signal):
                # S-026 G2: the multi-account dispatch fast-path is the
                # ONE sanctioned order path. Sizing is decided per-account
                # inside ``Coordinator.multi_account_execute`` via
                # ``RiskManager.position_size(pkg, balance)`` — the single
                # qty-deciding site post-G2. Halt-flag + news veto are
                # already checked above. (E1-F1: the ``else`` branch below
                # no longer places orders — it refuses, so there is no
                # divergent live path to fall through to.)

                # Strategy-monocle gate (one open package per strategy
                # globally, regardless of how many accounts follow it).
                # Per the operator directive 2026-05-03: a strategy
                # that already has an open package focuses on
                # *monitoring + updating* that package until SL/TP
                # hits or the strategy decides to close. Pre-fix every
                # actionable tick stacked a new package, so VWAP
                # accumulated 10+ open packages with the operator's
                # accounts unable to keep up.
                _gate_strategy = (
                    (signal.get("meta") or {}).get("strategy_name")
                    or signal.get("strategy")
                )
                _existing_open = _has_open_package_for_strategy(
                    _gate_strategy, signal.get("symbol")
                )
                if _existing_open is not None:
                    logger.info(
                        "strategy_monocle: skipping dispatch — strategy=%s "
                        "already has open package %s",
                        _gate_strategy, _existing_open,
                    )
                    result = {
                        "status": "skipped",
                        "reason": "open_package_exists",
                        "strategy": _gate_strategy,
                        "open_package_id": _existing_open,
                        "signal": signal,
                    }
                    _report_pipeline_outcome(result, signal)
                    return result
                # Bar-close debounce — one entry attempt per CLOSED bar
                # (PERF-20260601-001). The open-package gate above only blocks
                # while a package is *open*; when one closes mid-bar (the
                # reconciler records an exchange-side SL/TP fire) the gate frees
                # and the strategy re-fires its still-valid breakout on the next
                # tick — within the SAME bar. On the 2 h trend_donchian this
                # stacked 9 packages in ~1 h on 2026-06-01 and flooded the
                # journal with ``intent_noop`` rejection rows (the intent layer
                # no-ops the duplicate while a net position is held), skewing
                # per-strategy stats. Suppress a second actionable dispatch for
                # the same strategy+symbol inside the same timeframe bucket as
                # the package it already created this bar. Kill-switch:
                # ``STRATEGY_BAR_DEBOUNCE_DISABLED``.
                _same_bar = _same_bar_entry_for_strategy(
                    _gate_strategy, symbol=signal.get("symbol")
                )
                if _same_bar is not None:
                    logger.info(
                        "strategy_monocle: skipping dispatch — strategy=%s "
                        "already acted this bar (bar=%ds, last_pkg=%s @ %s)",
                        _gate_strategy, _same_bar["bar_seconds"],
                        _same_bar["order_package_id"], _same_bar["last_created_at"],
                    )
                    try:
                        log_signal({
                            "event": "bar_debounce_blocked",
                            "strategy": _gate_strategy,
                            "symbol": signal.get("symbol"),
                            "side": signal.get("side"),
                            "bar_seconds": _same_bar["bar_seconds"],
                            "last_package_id": _same_bar["order_package_id"],
                            "last_created_at": _same_bar["last_created_at"],
                        })
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "strategy_monocle: bar-debounce audit emit failed",
                        )
                    result = {
                        "status": "skipped",
                        "reason": "same_bar_reentry_debounce",
                        "strategy": _gate_strategy,
                        "last_package_id": _same_bar["order_package_id"],
                        "bar_seconds": _same_bar["bar_seconds"],
                        "signal": signal,
                    }
                    _report_pipeline_outcome(result, signal)
                    return result
                # Refusal cooldown — second strategy_monocle gate. Prevents
                # the dispatcher from re-firing the same signal every tick
                # when the most recent attempt was internally refused
                # (``sized_qty=0`` from RiskManager → log_rejection_to_journal
                # ``status='rejected'``). 2026-05-10 produced 20 such rows
                # in 1 h on bybit_2/vwap because the open-package gate above
                # only catches outstanding live positions, not refused
                # ones. Cooldown defaults to 300 s (~one 5 m candle); the
                # most common transient cause of refusal is Bybit V5
                # returning ``availableToBorrow=0`` (S-056 / S-058) and
                # repopulating on the exchange's cadence rather than ours.
                # Operator override: ``STRATEGY_REFUSAL_COOLDOWN_SECONDS``.
                _recent_refusal = _recent_refusal_for_strategy(
                    _gate_strategy, symbol=signal.get("symbol")
                )
                if _recent_refusal is not None:
                    logger.info(
                        "strategy_monocle: skipping dispatch — strategy=%s "
                        "refused %.0fs ago (cooldown=%ds, last_pkg=%s)",
                        _gate_strategy,
                        _recent_refusal["age_seconds"],
                        _recent_refusal["cooldown_seconds"],
                        _recent_refusal["order_package_id"],
                    )
                    # Land a dedicated audit row so the operator can
                    # reconstruct cooldown cadence without grepping the
                    # info-level pipeline.log. Best-effort — never let
                    # an audit failure bypass the gate.
                    try:
                        log_signal({
                            "event": "cooldown_blocked",
                            "strategy": _gate_strategy,
                            "symbol": signal.get("symbol"),
                            "side": signal.get("side"),
                            "age_seconds": _recent_refusal["age_seconds"],
                            "cooldown_seconds": _recent_refusal["cooldown_seconds"],
                            "last_refused_package_id": _recent_refusal["order_package_id"],
                        })
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "strategy_monocle: cooldown audit emit failed",
                        )
                    result = {
                        "status": "skipped",
                        "reason": "recent_refusal_cooldown",
                        "strategy": _gate_strategy,
                        "last_refused_package_id": _recent_refusal["order_package_id"],
                        "cooldown_age_seconds": _recent_refusal["age_seconds"],
                        "signal": signal,
                    }
                    _report_pipeline_outcome(result, signal)
                    return result
                try:
                    from src.core.coordinator import Coordinator
                    coord = Coordinator()
                    _sig_pkg = signal.get("signal_package")
                    _sized_qty: dict = {}
                    if (
                        _centralized_allocator_enabled(settings)
                        and _sig_pkg is not None
                        and getattr(_sig_pkg, "is_actionable", False)
                    ):
                        # S7: typed dispatch path — allocator computes qty;
                        # multi_account_execute_typed handles per-account
                        # dispatch. Per-account RiskManager still runs.
                        _bal = float(
                            settings.get("SHADOW_BALANCE_USDT")
                            or os.environ.get("SHADOW_BALANCE_USDT")
                            or 10_000
                        )
                        _alloc_pkgs = coord.build_order_packages(
                            [_sig_pkg], {"balance": _bal}
                        )
                        if _alloc_pkgs:
                            multi_results = coord.multi_account_execute_typed(
                                _alloc_pkgs
                            )
                            logger.info(
                                "CENTRALIZED_ALLOCATOR typed dispatch: "
                                "strategy=%s symbol=%s side=%s pkgs=%d",
                                _sig_pkg.strategy_id,
                                _sig_pkg.symbol,
                                _sig_pkg.side,
                                len(_alloc_pkgs),
                            )
                        else:
                            # Allocator produced nothing — fall back to legacy path.
                            pkg = _signal_to_order_package(signal, settings)
                            multi_results = coord.multi_account_execute(pkg)
                            _sized_qty = (pkg.meta or {}).get(
                                "sized_qty_by_account", {}
                            )
                    else:
                        pkg = _signal_to_order_package(signal, settings)
                        multi_results = coord.multi_account_execute(pkg)
                        _sized_qty = (pkg.meta or {}).get(
                            "sized_qty_by_account", {}
                        )
                    result = {
                        "status": "multi_account_dispatched",
                        "multi_account_results": multi_results,
                        "order": signal,
                        "sized_qty_by_account": _sized_qty,
                    }
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "multi-account dispatch failed: %s", exc,
                    )
                    result = {
                        "status": "failed_dispatch",
                        "reason": f"multi_account_execute: {exc}",
                        "order": signal,
                    }
            else:
                # E1-F1 (full-system audit 2026-07-09): REFUSE — never
                # place an order outside the one sanctioned path.
                #
                # Reached when either:
                #   * MULTI_ACCOUNT_DISPATCH is pinned off by the operator, or
                #   * an actionable signal is missing entry/sl/tp.
                #
                # The old behaviour here sized a hardcoded placeholder qty
                # (1.0) and called ``safe_place_order`` — which, with the
                # real Bybit adapter injected by ``src/main.py``, would send
                # a NAKED, un-sized, SL/TP-less ~1-unit market order straight
                # to the exchange. That bypassed both the per-account
                # ``RiskManager.position_size`` AND the SL/TP the strategy is
                # supposed to carry — a live-money bypass of the one
                # sanctioned order path (``Coordinator.multi_account_execute``).
                # It was latent (builders currently populate SL/TP and the
                # dispatch flag defaults on) but nothing *guaranteed* it, so
                # the bug class stays reachable until the divergent live path
                # is removed outright.
                #
                # There is no per-account risk context on this path (no
                # balance to size against), so there is no correct way to
                # place the order here. Refuse it explicitly and journal the
                # cause: a ``multi_account_dispatch_disabled`` refusal is an
                # operator misconfiguration; an ``actionable_signal_missing_sltp``
                # refusal is a caught upstream builder bug (the existing
                # ``signal_missing_sltp`` WARN below fires alongside it).
                _refuse_reason = (
                    "multi_account_dispatch_disabled"
                    if not multi
                    else "actionable_signal_missing_sltp"
                )
                logger.warning(
                    "pipeline: refusing order — no sanctioned sizing path "
                    "(reason=%s symbol=%s side=%s). The legacy single-client "
                    "placement was removed (E1-F1); the only order path is "
                    "Coordinator.multi_account_execute with per-account sizing.",
                    _refuse_reason, signal.get("symbol"), signal.get("side"),
                )
                result = {
                    "status": "refused",
                    "reason": _refuse_reason,
                    "signal": signal,
                }

    _report_pipeline_outcome(result, signal)

    # S-012 PR E4 + S-026 G4: include strategy attribution so the audit
    # log answers "which strategy fired this tick" for every line.
    # Source priority:
    #   1. signal.meta.strategy_name (set by every builder in this module)
    #   2. top-level signal["strategy"]
    #   3. settings["STRATEGY"]
    #   4. env STRATEGY
    #   5. final default — "multiplexed" (matches the actual production
    #      builder when STRATEGY is unset/multiplexed) — *not* "unknown",
    #      because the operator's hourly report counts "unknown" as a
    #      separate bucket and a missing label is uninformative noise.
    #      "unknown" was the silent default before BUG-033; the audit
    #      log now lands a meaningful name for every actionable tick.
    #
    # G5 (CP-2026-05-02-09): the same value also feeds the Telegram
    # "Pipeline result" message.
    _meta = signal.get("meta") or {}
    _strategy = (
        _meta.get("strategy_name")
        or signal.get("strategy")
        or (settings.get("STRATEGY") if isinstance(settings, dict) else None)
        or os.environ.get("STRATEGY")
        or "multiplexed"
    )

    # BUG-033 (CP-2026-05-02-22): the operator reported the hourly
    # summary showing actionable signals attributed to "unknown" even
    # though "Strategies (today)" correctly listed them under their
    # real names. Post-G1 the multiplexer preserves meta correctly, so
    # the remaining leak path is unclear. Land a one-shot diagnostic
    # warning that fires when an actionable signal still resolves to
    # the safety-default attribution — captures the signal keys, meta
    # keys, and which fallback level matched. The next hourly cycle
    # tells the operator (via journalctl) exactly which path produces
    # an under-attributed signal so a follow-up PR can fix it at the
    # source. Delete this block once a real cause is identified.
    if (
        signal.get("side") in ("buy", "sell")
        and (
            _meta.get("strategy_name") in (None, "")
            and signal.get("strategy") in (None, "")
        )
    ):
        try:
            _settings_keys = (
                list(settings.keys()) if isinstance(settings, dict) else []
            )
            logger.warning(
                "audit: actionable signal lacks meta.strategy_name + "
                "top-level strategy; resolved=%r via fallback. "
                "signal_keys=%s meta_keys=%s settings_has_STRATEGY=%s "
                "env_has_STRATEGY=%s",
                _strategy,
                list(signal.keys()),
                list(_meta.keys()),
                "STRATEGY" in _settings_keys,
                "STRATEGY" in os.environ,
            )
        except Exception:  # noqa: BLE001
            logger.exception("audit: BUG-033 diagnostic warning failed")

    # G5 (CP-2026-05-02-09): when an actionable signal reaches the
    # validator without entry/sl/tp populated at the top level, the
    # multi-account dispatch fast-path skips it and the order block
    # REFUSES it (E1-F1: ``status:refused`` reason
    # ``actionable_signal_missing_sltp`` — the legacy naked-order
    # placement was removed). Log the smoking-gun so journalctl
    # identifies the offending strategy — a missing-SL/TP actionable
    # signal is an upstream builder bug, not a routine skip.
    # S-026 G1: the qty>0 gate dropped — strategies no longer emit qty
    # (sizing is the per-account RiskManager's job in G2).
    if (
        signal.get("side") in ("buy", "sell")
        and not _signal_carries_full_sltp(signal)
    ):
        logger.warning(
            "pipeline: actionable %s signal lacks entry/sl/tp at top level "
            "— refused (no sanctioned sizing path). signal=%s",
            _strategy, signal,
        )
        try:
            report(
                "pipeline",
                "signal_missing_sltp",
                level=Level.WARN,
                strategy=_strategy,
                symbol=signal.get("symbol"),
                side=signal.get("side"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("pipeline: report() for signal_missing_sltp failed")

    try:
        log_signal(
            {
                "event": "pipeline_result",
                "strategy": _strategy,
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "qty": signal.get("qty"),
                # Carry the structural pattern + confidence the strategy
                # emitter set on the signal. Without these, the dashboard
                # /api/bot/signals consumer can't tell which ICT pattern
                # fired or how confident the model was — see
                # ict-trading-bot#556 + ict-trader-dashboard#5.
                "pattern": signal.get("signal_type") or signal.get("pattern"),
                "confidence": signal.get("confidence"),
                "price": signal.get("entry") or signal.get("entry_price") or signal.get("price"),
                "status": result.get("status"),
                "reason": result.get("reason"),
            }
        )
    except Exception as _audit_exc:  # noqa: BLE001
        logger.exception("pipeline audit log_signal failed")
        # Audit data loss is operationally relevant — surface it.
        # WARN, not ERROR, because this is a write to a JSONL we own;
        # if it fails repeatedly the disk-free health check or hourly
        # report will escalate.
        report(
            "audit_log",
            "write_failed",
            level=Level.WARN,
            reason=f"{type(_audit_exc).__name__}: {_audit_exc}",
        )

    status = result.get("status", "unknown")
    reason = result.get("reason")
    symbol = signal.get("symbol", "?")
    side = signal.get("side", "?")
    qty = signal.get("qty", "?")

    # G5 — the leading line keeps the canonical
    # ``Pipeline result: status=... | strategy=... | symbol=... |
    # side=... | qty=...`` format so journalctl greps and the existing
    # audit consumers stay stable. Section bodies (collapsable in HTML
    # clients) carry the deeper detail the operator asked for —
    # strategy attribution, order package, why-it-failed remediation.
    header = (
        f"Pipeline result: status={status} | strategy={_strategy} "
        f"| symbol={symbol} | side={side} | qty={qty}"
    )
    if reason:
        header += f" | reason={reason}"

    # Per-tick operator push is OFF (2026-06-04 reporting-cleanup sprint).
    # Even the gated path here was too noisy: every rejected/refused/error
    # tick still hit Telegram + FCM, drowning the actually-actionable
    # events. The operator's notification contract is now:
    #
    #   - Trade lifecycle (open/update/close) → fired from
    #     ``Database._fire_trade_{opened,updated,closed}_event``.
    #   - Hourly summary → ``scripts/send_hourly_now.py`` + the in-process
    #     scheduler (kind: HOURLY_SUMMARY).
    #   - Warnings → watchdogs / health-check (kind: WARNING).
    #   - Workflow updates → ops workflows post-completion
    #     (kind: WORKFLOW_UPDATE).
    #
    # The audit log (log_signal above), the runtime_status write below,
    # and the logger.info line still record EVERY tick — diagnostics
    # are unaffected, only the operator-facing push is silenced.
    logger.info(
        "Pipeline result (operator push suppressed): %s | %s",
        header,
        result,
    )

    write_status()

    return {
        "signal": signal,
        "order_result": result,
    }


# Back-compat shim — the canonical implementation moved to
# src.runtime.market_data._build_exchange_client (S-033 M1 refactor).
# Existing callers and tests that reference pipeline._build_killzone_exchange
# keep working unchanged.
def _build_killzone_exchange(settings: dict):
    from src.runtime.market_data import _build_exchange_client
    return _build_exchange_client(settings)
