"""Intent-aware multiplexer — collects intents from ALL enabled strategies
and aggregates them into one BTC/USDT target per tick.

Replaces the first-wins multiplexer in
``src/runtime/pipeline.py::multiplexed_signal_builder``. Gated by the
``MULTI_STRATEGY_INTENT_LAYER`` env var (or the equivalent settings key),
which now **defaults on** — this is the live intent-aggregation path.
The legacy first-wins builder is preserved as the rollback (set
``MULTI_STRATEGY_INTENT_LAYER=false`` to revert without a redeploy).

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

from src.core.signal_contract import SignalPackage
from src.runtime.intents import (
    DEFAULT_PRIORITIES,
    DesiredPosition,
    StrategyIntent,
    aggregate_intents,
    intent_from_signal,
)
from src.runtime.runtime_flags import is_strategy_paused
from src.runtime.strategy_monocle import (
    _bar_debounce_disabled,
    _strategy_timeframe_seconds,
)
from src.runtime.strategy_signal_builders import (
    fade_breakout_4h_signal_builder,
    fvg_range_15m_signal_builder,
    htf_pullback_trend_2h_signal_builder,
    mes_trend_long_1d_signal_builder,
    xauusd_trend_1h_signal_builder,
    mgc_trend_1h_signal_builder,
    spy_trend_long_1d_signal_builder,
    qqq_trend_long_1d_signal_builder,
    tqqq_trend_long_1d_signal_builder,
    qld_trend_long_1d_signal_builder,
    iwm_trend_long_1d_signal_builder,
    splg_trend_long_1d_signal_builder,
    scha_trend_long_1d_signal_builder,
    gld_pullback_1d_signal_builder,
    iaum_pullback_1d_signal_builder,
    slv_pullback_1d_signal_builder,
    gdx_pullback_1d_signal_builder,
    tlt_pullback_1d_signal_builder,
    ief_pullback_1d_signal_builder,
    gld_pullback_1h_signal_builder,
    slv_trend_1h_signal_builder,
    spy_pullback_1h_signal_builder,
    qqq_pullback_1h_signal_builder,
    tlt_pullback_1h_signal_builder,
    uso_trend_1h_signal_builder,
    eth_pullback_2h_signal_builder,
    eth_pullback_prop_2h_signal_builder,
    sol_pullback_2h_signal_builder,
    xrp_pullback_2h_signal_builder,
    ada_pullback_2h_signal_builder,
    avax_pullback_2h_signal_builder,
    mgc_pullback_1d_signal_builder,
    mhg_pullback_1d_signal_builder,
    squeeze_breakout_4h_signal_builder,
    ict_scalp_signal_builder,
    ict_scalp_sol_5m_signal_builder,
    ict_scalp_xrp_5m_signal_builder,
    ict_scalp_avax_5m_signal_builder,
    ict_scalp_xrp_15m_signal_builder,
    ict_scalp_eth_15m_signal_builder,
    ict_scalp_sol_15m_signal_builder,
    trend_donchian_1h_signal_builder,
    trend_donchian_eth_signal_builder,
    trend_donchian_sol_signal_builder,
    trend_donchian_eth_prop_signal_builder,
    trend_donchian_sol_prop_signal_builder,
    trend_donchian_eth_4h_signal_builder,
    trend_donchian_sol_4h_signal_builder,
    trend_donchian_xrp_4h_signal_builder,
    trend_donchian_ada_4h_signal_builder,
    trend_donchian_avax_4h_signal_builder,
    trend_donchian_signal_builder,
    turtle_soup_signal_builder,
    vwap_signal_builder,
)

logger = logging.getLogger(__name__)


# Signature: builder(settings: dict) -> pipeline-shape signal dict
IntentBuilder = Callable[[dict], Dict[str, Any]]


def _default_intent_builders() -> Dict[str, IntentBuilder]:
    """Strategy name → signal builder — the AUTHORITATIVE roster.

    This is the full, authoritative strategy roster the live order path uses
    (``MULTI_STRATEGY_INTENT_LAYER`` default on). ``pipeline._STRATEGY_BUILDERS``
    is a legacy SUBSET (the pre-intent-layer path) — it is NOT a mirror to keep
    in sync, and this dict must never be trimmed to match it. Where the two
    diverge, THIS superset wins: ``pipeline.monitor_unit_for`` looks up the
    legacy dict first and then FALLS BACK to this roster (``_resolve_builders``)
    for the symbol sleeves the legacy dict omits — a fallback added to fix a
    naked-orphan money-loss bug (BL-20260615-MGCNAKED), where a sleeve missing
    here ran with no active ``monitor()`` and drifted into a naked orphan.
    Adding a strategy means appending a row here (and, if it should also run on
    the legacy path, in ``pipeline._STRATEGY_BUILDERS``). The intent-layer
    aggregator itself does not change.
    """
    return {
        "turtle_soup": turtle_soup_signal_builder,
        "vwap": vwap_signal_builder,
        # ict_scalp_5m: live since 2026-05-14 (PR #1156, operator-
        # approved post pre-live gate). The builder honours the YAML
        # `enabled` flag as single source of truth; this registration
        # plumbs it through the intent layer when enabled.
        "ict_scalp_5m": ict_scalp_signal_builder,
        # M27 P0 Batch-1 alt variants (2026-07-21, Tier-3 operator-approved
        # promotion to bybit_1 demo soak) — SOL/AVAX pass UNGATED on their own
        # k-fold evidence; XRP's pass is gate-load-bearing, enforced via the
        # strategy-local off_cells filter in _ict_scalp_variant_builder (no
        # live regime head exists yet for these symbols, so the global
        # regime_policy.yaml path would be a no-op). Evidence:
        # docs/research/M27-P0-crypto-findings-2026-07-20.md.
        "ict_scalp_sol_5m": ict_scalp_sol_5m_signal_builder,
        "ict_scalp_xrp_5m": ict_scalp_xrp_5m_signal_builder,
        "ict_scalp_avax_5m": ict_scalp_avax_5m_signal_builder,
        # M27 P1 15m alt legs (2026-07-22, Tier-3 operator-approved paper-soak
        # promotion to bybit_1) — XRP/ETH/SOL 15m ict_scalp, cleared the net-of-
        # fee anchored k-fold gate (baseline >=3/4 folds). Ungated (no off_cells);
        # reuse the generic _ict_scalp_variant_builder (reads 15m from config).
        # Evidence: docs/research/M27-P1-15m-findings-2026-07-22.md.
        "ict_scalp_xrp_15m": ict_scalp_xrp_15m_signal_builder,
        "ict_scalp_eth_15m": ict_scalp_eth_15m_signal_builder,
        "ict_scalp_sol_15m": ict_scalp_sol_15m_signal_builder,
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
        # trend_donchian_sol / _eth: PROP-account alt variants (PB-20260616-004)
        # on the Breakout manual-bridge account. SOL live, ETH shadow. Each is
        # sole on its (symbol, prop-account) so arbitration never fires. Builders
        # honour the YAML `enabled` flag.
        "trend_donchian_sol": trend_donchian_sol_signal_builder,
        "trend_donchian_eth": trend_donchian_eth_signal_builder,
        # SWAP-ROBUST prop exit variants (Unit C, Phase 0, 2026-06-29; DRAFT
        # Tier-3): tightened-exit (trail_mult 3.5 / tp_r 6.0) prop-only siblings
        # of trend_donchian_sol/_eth, routed to breakout_1 as execution: shadow
        # (observe-only soak until the prop EV/survival gate passes). Each is
        # sole on its (symbol, prop-account) so arbitration never fires.
        "trend_donchian_sol_prop": trend_donchian_sol_prop_signal_builder,
        "trend_donchian_eth_prop": trend_donchian_eth_prop_signal_builder,
        # trend_4h alt cells (2026-06-18): five symbol-pinned trend_donchian
        # instances on the 4h candle (ETH/SOL/XRP/ADA/AVAX), routed to bybit_1
        # (Bybit demo — paper money) for decision/ML soak. WS-C k-fold classed
        # them `paper_ready` (net-of-fee positive + 2x-fee headroom; fail only
        # the strict every-fold gate — SRQ-20260618-001). Demo-only, both-sides
        # (no long_only); NOT live-money-ready. Each is sole on its (symbol,
        # account) so arbitration never fires. Builders honour the YAML
        # `enabled` flag.
        "trend_donchian_eth_4h": trend_donchian_eth_4h_signal_builder,
        "trend_donchian_sol_4h": trend_donchian_sol_4h_signal_builder,
        "trend_donchian_xrp_4h": trend_donchian_xrp_4h_signal_builder,
        "trend_donchian_ada_4h": trend_donchian_ada_4h_signal_builder,
        "trend_donchian_avax_4h": trend_donchian_avax_4h_signal_builder,
        # mes_trend_long_1d: MES daily long-only equity-index diversifier on
        # IBKR ib_paper, execution:shadow (2026-06-01); priority 0 (new floor).
        # Builder honours the YAML `enabled` flag.
        "mes_trend_long_1d": mes_trend_long_1d_signal_builder,
        # xauusd_trend_1h: gold 1h trend-follower on oanda_practice (M15
        # Phase 3) — the Phase-0 sweep's strongest cell. Sole strategy on
        # its (symbol, account) so arbitration never fires.
        "xauusd_trend_1h": xauusd_trend_1h_signal_builder,
        # mgc_trend_1h: the IBKR sibling of xauusd_trend_1h — same gold edge,
        # MGC micro-gold futures on ib_paper (paper money). Sole trend strategy
        # on (MGC, ib_paper); arbitrates only vs mgc_pullback_1d (different
        # timeframe/style) via the multiplexer. Backtest: OOS +32R @ 1.5bps.
        "mgc_trend_1h": mgc_trend_1h_signal_builder,
        # M15 Phase 4 buildout — the daily ETF futures-replacements on
        # alpaca_paper (paper money): SPY/QQQ long-only trend (MES mirror)
        # + GLD pullback (MGC mirror). Each is the sole strategy on its
        # symbol, so arbitration never fires.
        "spy_trend_long_1d": spy_trend_long_1d_signal_builder,
        "qqq_trend_long_1d": qqq_trend_long_1d_signal_builder,
        # Leveraged Nasdaq-100 ETF trend cells (2026-06-30, Tier-3) — TQQQ (3x)
        # + QLD (2x), siblings of qqq_trend_long_1d reusing the trend_donchian
        # unit. paper_ready, beat/matched the QQQ cell net-of-decay
        # (docs/research/leveraged-etf-research-2026-06-30.md). alpaca_paper soak.
        "tqqq_trend_long_1d": tqqq_trend_long_1d_signal_builder,
        "qld_trend_long_1d": qld_trend_long_1d_signal_builder,
        "gld_pullback_1d": gld_pullback_1d_signal_builder,
        # ETF-breadth daily sweep (2026-06-20) — three new cells in the daily
        # ETF family on alpaca_paper (paper money): IWM small-cap long-only
        # trend (spy/qqq sibling) + TLT/IEF bond pullback (gld sibling). IWM
        # was the only live_ready/every-fold cell; TLT/IEF are paper_ready +
        # fee-robust (pooled book Sharpe 3.88). Each is the sole strategy on
        # its symbol, so arbitration never fires.
        "iwm_trend_long_1d": iwm_trend_long_1d_signal_builder,
        # Cheap-share proxy cells (paper soak) — SPLG (~$84 SPY proxy) +
        # SCHA (~$35 IWM proxy) long-only trend (trend_donchian) + IAUM
        # (~$41 GLD proxy) bidirectional pullback (htf_pullback_trend_2h).
        "splg_trend_long_1d": splg_trend_long_1d_signal_builder,
        "scha_trend_long_1d": scha_trend_long_1d_signal_builder,
        "iaum_pullback_1d": iaum_pullback_1d_signal_builder,
        "tlt_pullback_1d": tlt_pullback_1d_signal_builder,
        "ief_pullback_1d": ief_pullback_1d_signal_builder,
        # SLV/GDX daily pullback — gold/silver-complex siblings of gld_pullback_1d,
        # routed to alpaca_paper + alpaca_live (Tier-3 approved 2026-06-27; builders
        # wired 2026-06-28 — they were enabled+live in YAML but had no builder, so
        # they were inert. Sole strategy on SLV/GDX, so arbitration never fires).
        "slv_pullback_1d": slv_pullback_1d_signal_builder,
        "gdx_pullback_1d": gdx_pullback_1d_signal_builder,
        # Intraday ETF pilot (2026-06-20 § 0e, Tier-3) — the first INTRADAY (1h)
        # cells in the ETF family on alpaca_paper (paper money): GLD 1h
        # bidirectional pullback (gld_pullback_1d sibling) + SLV 1h
        # BIDIRECTIONAL Donchian trend (spy clone but both-sides — silver trends
        # down too). Rollout A pilot for the intraday ETF-breadth sleeve. Each is
        # the sole strategy on its (symbol, timeframe), so arbitration never fires.
        "gld_pullback_1h": gld_pullback_1h_signal_builder,
        "slv_trend_1h": slv_trend_1h_signal_builder,
        # Intraday ETF rollout 2b (2026-06-20 § 0e, Tier-3) — completes the
        # intraday ETF-breadth sleeve (6 cells total with the pilot): SPY/QQQ/TLT
        # 1h BIDIRECTIONAL pullback (gld_pullback_1h siblings) + USO 1h LONG-ONLY
        # Donchian trend (the both-sides variant was REJECTED in the sweep, so
        # USO is long-only). Each is the sole strategy on its (symbol, timeframe),
        # so arbitration never fires.
        "spy_pullback_1h": spy_pullback_1h_signal_builder,
        "qqq_pullback_1h": qqq_pullback_1h_signal_builder,
        "tlt_pullback_1h": tlt_pullback_1h_signal_builder,
        "uso_trend_1h": uso_trend_1h_signal_builder,
        # eth_pullback_2h — M15 WS-C alt sleeve (2026-06-11): ETH/USDT 2h
        # HTF-pullback on bybit_1 (demo, paper money), reusing the
        # htf_pullback_trend_2h unit at the live BTC params. Sole strategy
        # on its symbol, so arbitration never fires.
        "eth_pullback_2h": eth_pullback_2h_signal_builder,
        # eth_pullback_prop_2h — swap-robust prop variant (2026-06-25, DRAFT
        # Tier-3): same ETHUSDT 2h pullback unit with tighter exits (tp_r 6 /
        # trail 3.5) routed to breakout_1 as execution: shadow (observe-only).
        # Sole strategy on (ETHUSDT, breakout_1), so arbitration never fires.
        "eth_pullback_prop_2h": eth_pullback_prop_2h_signal_builder,
        # pullback_2h alt cells (2026-06-18): four symbol-pinned
        # htf_pullback_trend_2h instances on the 2h candle (SOL/XRP/ADA/AVAX),
        # routed to bybit_1 (Bybit demo — paper money) for decision/ML soak.
        # WS-C k-fold classed them `paper_ready` (net-of-fee positive + 2x-fee
        # headroom; fail only the strict every-fold gate — SRQ-20260618-002).
        # Mirror eth_pullback_2h's params exactly (only the symbol differs).
        # Demo-only; NOT live-money-ready. Each is sole on its symbol, so
        # arbitration never fires. Builders honour the YAML `enabled` flag.
        "sol_pullback_2h": sol_pullback_2h_signal_builder,
        "xrp_pullback_2h": xrp_pullback_2h_signal_builder,
        "ada_pullback_2h": ada_pullback_2h_signal_builder,
        "avax_pullback_2h": avax_pullback_2h_signal_builder,
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

# Per-bar EMISSION debounce state (re-entry-storm guard, PERF-20260601-001).
# Maps (strategy, symbol) -> the closed-bar bucket [floor(epoch / bar_seconds)]
# of the strategy's most recent emitted intent. Persists ACROSS ticks (module
# level) so that across the ~N ticks inside one strategy bar, only the first
# actionable tick emits. Reset implicitly when a new bar opens (bucket changes).
# Bounded by strategy×symbol count; values are ints, no pruning needed.
_LAST_EMITTED_BUCKET: Dict[tuple, int] = {}


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


def _debounce_emissions(
    intents: List[StrategyIntent], *, now: Optional[float] = None
) -> List[StrategyIntent]:
    """Drop intents a strategy already emitted earlier in the SAME closed bar.

    Re-entry-storm guard (PERF-20260601-001). A strategy emits at most ONE
    intent per ``(strategy, symbol, floor(now / bar_seconds))`` — regardless of
    whether that intent later opens, is regime-gated, or is risk-rejected. The
    dispatch-side ``strategy_monocle._same_bar_entry_for_strategy`` guard is
    DB-backed (it scans ``order_packages``), so it cannot see a GATED intent
    that never creates a package — e.g. the ``htf_pullback_trend_2h`` short the
    regime router drops every tick, which floods the signals audit and distorts
    per-strategy stats. This guard runs at the once-per-tick aggregation
    boundary, BEFORE gating, so it covers the gated case too.

    State lives in the module-level ``_LAST_EMITTED_BUCKET`` so it persists
    across ticks (the ~N ticks inside one strategy bar collapse to one
    emission) and resets implicitly when a new bar opens (the bucket changes).

    Fail-open: the ``STRATEGY_BAR_DEBOUNCE_DISABLED`` kill-switch, an
    unresolvable timeframe, or a missing symbol all let the intent through —
    a live signal is never stranded by this guard. Shares the kill-switch and
    the ``_strategy_timeframe_seconds`` lookup with the dispatch-side guard.
    """
    if not intents or _bar_debounce_disabled():
        return list(intents)
    if now is None:
        now = time.time()
    kept: List[StrategyIntent] = []
    for intent in intents:
        bar_seconds = _strategy_timeframe_seconds(intent.strategy)
        if not bar_seconds or bar_seconds <= 0 or not intent.symbol:
            kept.append(intent)
            continue
        bucket = int(now // bar_seconds)
        key = (intent.strategy, intent.symbol)
        if _LAST_EMITTED_BUCKET.get(key) == bucket:
            logger.debug(
                "intent_multiplexer: '%s' on %s already emitted this bar "
                "(bucket=%d, bar_seconds=%d) — debounced (re-entry-storm guard)",
                intent.strategy, intent.symbol, bucket, int(bar_seconds),
            )
            continue
        _LAST_EMITTED_BUCKET[key] = bucket
        kept.append(intent)
    return kept


# Top-level key under which the full per-tick candidate set is attached to the
# returned pipeline signal (M18 P0b). Top-level (NOT under ``meta``) so it never
# rides into ``order_bridge`` → ``pkg.meta`` (which gets JSON-serialised — a
# typed SignalPackage there would break it). Mirrors the existing top-level
# ``signal_package`` convention the centralized-allocator path already uses.
CANDIDATE_BATCH_KEY = "candidate_signal_packages"


def intents_to_signal_packages(
    intents: List[StrategyIntent],
) -> List[SignalPackage]:
    """Project the per-tick candidate intents into typed ``SignalPackage``s.

    This is the **full opportunity set** for the tick — every strategy's
    actionable intent BEFORE ``aggregate_intents`` collapses them to one
    ``DesiredPosition`` per symbol. It is the input a portfolio capital
    allocator (M18) ranks/selects across; today it is exposed **observe-only**
    (attached to the returned signal under ``CANDIDATE_BATCH_KEY``) and consumed
    by nothing on the live order path — the allocator soak (M18 P0c) will read it.

    Pure + fail-permissive: a malformed intent is skipped, never raised, so this
    can never strand a live tick. ``account_id`` is intentionally left empty —
    intents are not account-bound at the multiplexer (account fan-out happens
    downstream in ``Coordinator.multi_account_execute``).
    """
    from datetime import datetime, timezone

    packages: List[SignalPackage] = []
    for intent in intents or []:
        try:
            if intent.side == "long":
                side = "long"
            elif intent.side == "short":
                side = "short"
            else:
                side = "none"
            ts = getattr(intent, "timestamp", None)
            try:
                ts_iso = (
                    datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                    if ts is not None
                    else ""
                )
            except (TypeError, ValueError, OSError, OverflowError):
                ts_iso = ""
            packages.append(
                SignalPackage(
                    strategy_id=intent.strategy,
                    symbol=intent.symbol,
                    account_id="",
                    side=side,
                    entry_price=intent.entry,
                    stop_loss=intent.sl,
                    take_profit=intent.tp,
                    timestamp_utc=ts_iso,
                    raw=dict(intent.meta or {}),
                    source_context={
                        "confidence": float(getattr(intent, "confidence", 0.0) or 0.0),
                        "priority": intent.effective_priority(),
                        "regime": getattr(intent, "regime", None),
                        "target_qty": getattr(intent, "target_qty", None),
                        "source": "intent_multiplexer_candidate_batch",
                    },
                )
            )
        except Exception:  # noqa: BLE001 — observe-only projection must never break a tick
            logger.debug("candidate-batch: skipped a malformed intent", exc_info=False)
            continue
    return packages


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
    # No per-strategy risk injection (removed 2026-06-29): position sizing is
    # the RiskManager's sole responsibility (account basis × confidence). A
    # strategy carries no risk level.
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
    # Re-entry-storm guard: collapse a strategy's repeated same-bar emissions to
    # one per closed bar before aggregation (PERF-20260601-001).
    intents = _debounce_emissions(intents)

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
    signal = _desired_to_pipeline_signal(desired, symbol=symbol, settings=settings)
    # M18 P0b (observe-only): attach the FULL candidate set for this tick — the
    # opportunity set a portfolio capital allocator ranks across. Nothing on the
    # live order path consumes it yet (the allocator soak, M18 P0c, will).
    # Fail-permissive: an attach failure never strands the signal.
    try:
        signal[CANDIDATE_BATCH_KEY] = intents_to_signal_packages(intents)
    except Exception:  # noqa: BLE001 — observe-only attach must never break a tick
        logger.debug("candidate-batch: attach failed", exc_info=False)
    # M18 P0c (observe-only): soak what a capital allocator WOULD pick (top-ranked
    # candidate) vs what the aggregator actually routed, + the regret between them.
    # Only fires when ≥ 2 actionable candidates exist (a genuine choice). Routing
    # is unchanged; nothing reads this back. Fail-permissive.
    try:
        from src.runtime.allocator_ev import candidate_ev_score
        from src.runtime.allocator_soak import record_allocator_soak
        _executed = desired.winning_intent.strategy if desired.winning_intent else None
        # M18 P1: rank candidates by cost-aware EV_R (not raw confidence), so the
        # soak's regret is in net-of-fee R-units. Pluggable score_fn — the harness
        # is unchanged from P0c.
        record_allocator_soak(
            signal.get(CANDIDATE_BATCH_KEY) or [],
            symbol=symbol,
            executed_strategy_id=_executed,
            executed_side=signal.get("side"),
            score_fn=candidate_ev_score,
            score_kind="ev_net_r",
        )
    except Exception:  # noqa: BLE001 — observe-only soak must never break a tick
        logger.debug("allocator_soak: record failed", exc_info=False)
    return signal
