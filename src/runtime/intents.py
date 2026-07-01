"""Strategy-intent layer — multi-strategy execution scaffolding.

Background
----------
Pre-this-module the multiplexer (``src/runtime/pipeline.py::multiplexed_signal_builder``)
was strictly first-wins: it iterated ``STRATEGIES`` in order, returned the
first actionable signal, and the dispatcher placed exactly that order. The
downstream ``_has_open_position`` guard then blocked any subsequent strategy
from acting on the same ``(account, symbol)`` for the lifetime of the open
trade.

That model is fine when only one strategy can fire on the live account, but
once Turtle Soup and VWAP both run on Bybit2 / BTC/USDT it produces two
brittle outcomes:

1. **Reinforcement gets dropped.** Turtle Soup and VWAP both going long on
   BTC is a stronger conviction than either alone — instead the dispatcher
   takes whichever fires first and silently ignores the second.
2. **Conflicts are non-deterministic.** Whichever builder happens to be
   earlier in ``STRATEGIES`` wins, and the other strategy's signal is just
   dropped without any audit trail of the conflict.

This module is the structural fix. Strategies emit a typed ``StrategyIntent``
(target position, side, priority); a pure aggregator collapses the per-tick
intents into one ``DesiredPosition`` for BTC/USDT; a pure delta computer
turns that desired target plus the current net position into the minimal
``ExecutionDelta`` needed to reach it. Risk caps still fire in the
per-account ``RiskManager`` — this layer never decides qty; it only
decides target.

Scope
-----
**BTC/USDT only.** ``aggregate_intents`` filters to BTCUSDT by default and
refuses to mix symbols. The constants + symbol-normaliser are written so
the same primitives can later be applied per-symbol without redesign, but
no multi-symbol routing is wired in this PR.

**Two strategies in scope (Turtle Soup, VWAP).** The priority map is keyed
by strategy name with a sane default for unknown strategies; a future
ICT scalping strategy plugs in by passing its name + a priority through
``intent_from_signal`` or by registering a default priority via
``DEFAULT_PRIORITIES``. There is no special-case code path for either
strategy in scope today — both flow through the same aggregator.

Risk-layer invariant
--------------------
This module produces **targets**, not orders. The per-account
``RiskManager.position_size`` / ``RiskManager.evaluate`` gates in
``src/units/accounts/risk.py`` remain the only sites that decide actual
qty and the only sites that enforce daily-loss / margin / position-size
caps. ``compute_execution_delta`` reports the *intended* delta; the
dispatcher still routes that intent through the per-account risk gates
before any order leaves the process.

Public surface
--------------
- ``StrategyIntent``       — typed per-strategy intent (one per tick per strategy)
- ``DesiredPosition``      — aggregator output (one per tick per symbol)
- ``ExecutionDelta``       — what the executor should send (or noop)
- ``aggregate_intents``    — combine intents → DesiredPosition (deterministic)
- ``compute_execution_delta`` — DesiredPosition + current qty → delta
- ``intent_from_signal``   — bridge a pipeline signal dict → StrategyIntent
- ``DEFAULT_PRIORITIES``   — strategy-name → priority map (mutable; future
                             strategies append a row here)
- ``SUPPORTED_SYMBOLS``    — static base whitelist; validation actually goes
                             through ``supported_symbols()``, which unions in
                             every symbol declared in config/accounts.yaml

Future-strategy plug-in pattern
-------------------------------
Adding ``ict_scalp`` later means: (a) implement its signal builder, (b)
add ``"ict_scalp": <priority>`` to ``DEFAULT_PRIORITIES`` or pass
``priority=`` explicitly, (c) include the strategy name in the
multiplexer's collect-from list. The aggregator and delta computer do
not change.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

from src.runtime.conviction_arbitration import annotate_conviction_arbitration


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Symbols the intent layer accepts. Multi-symbol (2026-05-22): MES joins
# BTCUSDT now that the per-symbol position-state path is wired — the
# aggregator/delta are already symbol-parametric (every StrategyIntent /
# DesiredPosition / ExecutionDelta carries ``symbol``), and the
# strategy-monocle open-package gates are symbol-scoped (so an open
# BTCUSDT package can't suppress an MES entry). MGC (Micro Gold) + MHG (Micro
# Copper) join 2026-06-02 for the WS-A metals sleeve (mgc_pullback_1d /
# mhg_pullback_1d on ib_paper) — same per-symbol parametricity. This is a
# validation whitelist, not a runtime on/off gate — accounts.yaml ``symbols``
# drives what actually trades; ``mode:`` is the only execution gate.
#
# Config-driven since 2026-06-11: this frozenset is only the STATIC BASE.
# Validation goes through ``supported_symbols()``, which unions in every
# symbol declared on an account in ``config/accounts.yaml`` — so wiring a
# new instrument (M15: XAUUSD on oanda_practice, SPY/QQQ/GLD on
# alpaca_paper) never needs a code edit here again. The hand-maintained
# base had already drifted behind accounts.yaml once (the M15 symbols),
# which would have raised ``ValueError`` out of ``_collect_intents`` on
# the first actionable signal.
SUPPORTED_SYMBOLS: frozenset[str] = frozenset({"BTCUSDT", "MES", "MGC", "MHG"})

# accounts.yaml-declared symbols, cached briefly so per-intent validation
# doesn't re-read YAML on every construction. Fail-safe: a config-load
# failure leaves the cached set empty and validation falls back to the
# static base — never *narrower* than the pre-config-driven behaviour.
_CONFIG_SYMBOLS_TTL_S = 60.0
_config_symbols_state: Dict[str, Any] = {"at": 0.0, "symbols": frozenset()}


def _reset_config_symbols_cache() -> None:
    """Test hook — force the next ``supported_symbols()`` to re-read config."""
    _config_symbols_state["at"] = 0.0
    _config_symbols_state["symbols"] = frozenset()


def supported_symbols() -> frozenset[str]:
    """``SUPPORTED_SYMBOLS`` ∪ every symbol declared in ``config/accounts.yaml``.

    accounts.yaml is the single source of truth for what trades (it drives
    ``_resolve_tick_symbols``), so it is also the source of truth for what
    the intent layer should accept. A typo'd symbol is still rejected —
    no account declares it. Symbols are normalised (upper, ``/`` stripped)
    to match ``StrategyIntent`` normalisation.
    """
    now = time.monotonic()
    if now - _config_symbols_state["at"] > _CONFIG_SYMBOLS_TTL_S:
        symbols: set = set()
        try:
            from src.config.accounts_loader import load_accounts_dict

            for cfg in (load_accounts_dict() or {}).values():
                for sym in (cfg or {}).get("symbols") or []:
                    symbols.add(str(sym).upper().replace("/", ""))
        except Exception:  # noqa: BLE001 — fail-safe to the static base
            symbols = set()
        _config_symbols_state["symbols"] = frozenset(symbols)
        _config_symbols_state["at"] = now
    return SUPPORTED_SYMBOLS | _config_symbols_state["symbols"]


# Higher priority wins conflicts. Tiebreaker order is documented on
# ``aggregate_intents``. The Turtle Soup > VWAP order matches the
# operator's stated risk preference: Turtle Soup is the higher-conviction
# MTF setup; VWAP is the higher-frequency mean-reversion strategy. Tune
# this map (or override via StrategyIntent.priority) without changing
# the aggregator.
DEFAULT_PRIORITIES: Dict[str, int] = {
    "turtle_soup": 50,
    "vwap": 40,
    # ict_scalp_5m is set below vwap so this newer strategy cannot
    # override the established ones on a tie. Live since 2026-05-14
    # (PR #1156, post pre-live gate). Tune upward only with operator
    # approval after a backtest validates the priority change.
    "ict_scalp_5m": 30,
    # trend_donchian — deliberately the LOWEST priority on the roster
    # (S-STRAT-IMPROVE-S8 go-live). It runs alone on bybit_2 so its
    # priority never actually arbitrates there; the low value is a
    # safety floor so a wiring slip can't let it override turtle_soup /
    # vwap / ict_scalp on any account that runs more than one strategy.
    "trend_donchian": 20,
    # fade_breakout_4h — the floor of the roster (S9, 2026-05-24). Wired
    # execution:shadow (data-only, never sends a live order), so its
    # priority never arbitrates a real order; the lowest value is the
    # safety floor for the unproven strategy.
    "fade_breakout_4h": 10,
    # squeeze_breakout_4h — the floor (S9, 2026-05-24). execution:shadow
    # (data-only), so its priority never arbitrates a real order.
    "squeeze_breakout_4h": 5,
    # fvg_range_15m — the new floor (2026-05-30). The range member (FVG
    # mean-reversion in a confirmed static range). Wired execution:shadow
    # (data-only, never sends a live order), so its priority never arbitrates
    # a real order; the lowest value is the safety floor for the unproven
    # strategy.
    "fvg_range_15m": 3,
    # htf_pullback_trend_2h — the new floor (2026-06-01). Overnight-research
    # HTF-pullback trend-follower. Wired execution:shadow (data-only, never
    # sends a live order), so its priority never arbitrates a real order; the
    # lowest value is the safety floor for the unproven strategy.
    "htf_pullback_trend_2h": 2,
    # trend_donchian_1h — the new floor (2026-06-01). Faster-TF/wider-trail
    # shadow A/B of the live 2h trend_donchian. Wired execution:shadow
    # (data-only, never sends a live order), so its priority never arbitrates a
    # real order; the lowest value is the safety floor for the unproven instance.
    "trend_donchian_1h": 1,
    # mes_trend_long_1d — the new floor (2026-06-01). MES daily long-only
    # equity-index diversifier. Wired execution:shadow (data-only, never sends a
    # live order), so its priority never arbitrates a real order; the lowest
    # value is the safety floor for the unproven sleeve.
    "mes_trend_long_1d": 0,
    # xauusd_trend_1h — gold 1h trend (M15 Phase 3); sole strategy on its
    # symbol/account, value 0 mirrors mes_trend_long_1d.
    "xauusd_trend_1h": 0,
    # mgc_trend_1h — gold 1h trend on MGC futures (ib_paper); IBKR sibling of
    # xauusd_trend_1h. Shares (MGC, ib_paper) with mgc_pullback_1d but a
    # different timeframe/style; value 0 mirrors the other trend sleeves.
    "mgc_trend_1h": 0,
    # M15 Phase 4 ETF legs — sole strategy per symbol, value 0.
    "spy_trend_long_1d": 0,
    "qqq_trend_long_1d": 0,
    # Leveraged Nasdaq-100 ETF trend cells (2026-06-30) — TQQQ (3x) + QLD (2x),
    # sole strategy per symbol, value 0 mirrors the other ETF legs.
    "tqqq_trend_long_1d": 0,
    "qld_trend_long_1d": 0,
    "gld_pullback_1d": 0,
    # ETF-breadth daily sweep (2026-06-20) — IWM small-cap trend + TLT/IEF bond
    # pullback on alpaca_paper. Each is sole on its symbol, value 0 mirrors the
    # other single-symbol ETF legs.
    "iwm_trend_long_1d": 0,
    "tlt_pullback_1d": 0,
    "ief_pullback_1d": 0,
    # Intraday ETF pilot (2026-06-20 § 0e) — GLD 1h pullback + SLV 1h
    # bidirectional trend on alpaca_paper. Each sole on its (symbol, timeframe),
    # value 0 mirrors the other single-symbol ETF legs.
    "gld_pullback_1h": 0,
    "slv_trend_1h": 0,
    # Intraday ETF rollout 2b (2026-06-20 § 0e) — SPY/QQQ/TLT 1h pullback + USO
    # 1h long-only trend on alpaca_paper. Each sole on its (symbol, timeframe),
    # value 0 mirrors the other single-symbol ETF legs.
    "spy_pullback_1h": 0,
    "qqq_pullback_1h": 0,
    "tlt_pullback_1h": 0,
    "uso_trend_1h": 0,
    # eth_pullback_2h — M15 WS-C alt sleeve (2026-06-11); sole strategy on
    # ETHUSDT (bybit_1 demo), value 0 mirrors the other single-symbol legs.
    "eth_pullback_2h": 0,
    # eth_pullback_prop_2h — swap-robust prop variant (2026-06-25, DRAFT Tier-3);
    # sole strategy on (ETHUSDT, breakout_1), execution: shadow, value 0 mirrors
    # the other single-symbol legs (priority is moot — never arbitrates).
    "eth_pullback_prop_2h": 0,
    # mgc_pullback_1d / mhg_pullback_1d — the WS-A metals sleeve (2026-06-02).
    # Micro Gold + Micro Copper daily HTF-pullback diversifiers. Each runs ALONE
    # on its own symbol (MGC / MHG) on ib_paper, so priority is moot — they never
    # arbitrate against another strategy. Value 0 follows mes_trend_long_1d.
    "mgc_pullback_1d": 0,
    "mhg_pullback_1d": 0,
    # trend_donchian_sol / _eth — PROP-account alt variants (PB-20260616-004) on
    # the Breakout manual-bridge account. Each runs ALONE on its (symbol,
    # prop-account), so priority is moot — they never arbitrate against another
    # strategy. Value 0 follows the other single-symbol legs.
    "trend_donchian_sol": 0,
    "trend_donchian_eth": 0,
    # SWAP-ROBUST prop exit variants (Unit C, Phase 0, 2026-06-29; DRAFT Tier-3) —
    # tightened-exit prop-only siblings of trend_donchian_sol/_eth on breakout_1.
    # Each runs ALONE on its (symbol, prop-account) so priority is moot. Value 0.
    "trend_donchian_sol_prop": 0,
    "trend_donchian_eth_prop": 0,
    # trend_4h + pullback_2h alt cells (2026-06-18) — bybit_1 DEMO soak,
    # paper_ready (WS-C k-fold: net-of-fee positive + 2x-fee headroom, fail only
    # the strict every-fold gate; SRQ-20260618-001 / -002). Each runs ALONE on
    # its (symbol, bybit_1) so priority is moot — never arbitrates against
    # another strategy. Value 0 follows the other single-symbol legs.
    "trend_donchian_eth_4h": 0,
    "trend_donchian_sol_4h": 0,
    "trend_donchian_xrp_4h": 0,
    "trend_donchian_ada_4h": 0,
    "trend_donchian_avax_4h": 0,
    "sol_pullback_2h": 0,
    "xrp_pullback_2h": 0,
    "ada_pullback_2h": 0,
    "avax_pullback_2h": 0,
}

# Priority used when a strategy is not listed in DEFAULT_PRIORITIES and
# the intent does not carry an explicit priority. Picked deliberately
# below the in-scope strategies so a misconfigured new strategy never
# silently overrides Turtle Soup / VWAP.
_UNKNOWN_STRATEGY_PRIORITY: int = 10


_VALID_SIDES: frozenset[str] = frozenset({"long", "short", "flat"})


# ---------------------------------------------------------------------------
# Flip-policy — conflict-resolution behaviour on an opposite net vote
# ---------------------------------------------------------------------------
#
# When the desired net side is the OPPOSITE of the currently-held position,
# ``compute_execution_delta`` must decide what to do. Three behaviours,
# mirroring the ``--flip-policy`` knob in ``scripts/backtest_system.py`` so the
# live path and the system backtester stay faithful twins:
#
#   "hold"  (DEFAULT since 2026-05-31; walk-forward verified PASS) — keep the
#             current position; ignore the opposite vote and let the owning
#             strategy's own ``monitor()`` / SL / TP exit it naturally
#             (``action="noop"``). Walk-forward (24 cells = 2 anchored folds ×
#             2 halves × 2 rosters × 3 policies, see
#             ``docs/audits/walkforward-flip-policy-2026-05-30.md``) showed
#             hold beats reverse on net AND maxDD% across all four 4-member
#             cells (OOS lift > train lift on both folds) and is materially
#             less-bad on every single 6-member cell. Zeroes flip-churn,
#             ~halves max-DD, flips the 4-member book net-positive.
#   "reverse" (legacy) — close the current position AND open the new side
#             immediately (``action="flip"``). The historical pre-2026-05-31
#             behaviour and the rollback path: set ``FLIP_POLICY=reverse`` on
#             the live VM (no redeploy needed). The system backtester also
#             replicates this behaviour under ``--flip-policy reverse``.
#   "flat"  — close the current position but do NOT re-open (``action="close"``);
#             stand aside on conflict. Tested by the walk-forward, never the
#             best policy in any cell.
#
# The DEFAULT IS "hold" (2026-05-31, operator-approved). Switching back to
# ``"reverse"`` is the operator-gated rollback path via the ``FLIP_POLICY``
# env on the live VM (or a settings key). This is NOT an auto-disable /
# auto-flip path: it is a per-tick target decision, journalled on every
# suppression (the coordinator logs the resulting ``noop`` to the trade
# journal as ``intent_noop:flip_suppressed_hold_policy:…``), consistent with
# the Prime Directive's "no silent state" rule.
FLIP_POLICIES: frozenset[str] = frozenset({"reverse", "hold", "flat"})
_DEFAULT_FLIP_POLICY: str = "hold"


def resolve_flip_policy(settings: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the active flip policy. Mirrors ``intent_multiplexer_enabled``.

    Resolution order: explicit ``settings["FLIP_POLICY"]`` → the ``FLIP_POLICY``
    env var → ``"hold"`` (the post-walk-forward live default since 2026-05-31;
    see ``docs/audits/walkforward-flip-policy-2026-05-30.md``). An unrecognised
    value falls back to the default rather than raising, so a typo on the VM
    can never strand the order path. Operator rollback to the legacy
    close-and-reverse behaviour: ``FLIP_POLICY=reverse`` on the systemd unit;
    no redeploy needed.
    """
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("FLIP_POLICY")
    if raw is None:
        raw = os.environ.get("FLIP_POLICY", _DEFAULT_FLIP_POLICY)
    policy = str(raw).strip().lower()
    return policy if policy in FLIP_POLICIES else _DEFAULT_FLIP_POLICY


def resolve_flip_confidence_threshold(settings: Optional[Dict[str, Any]] = None) -> float:
    """Minimum confidence gap (new − existing) to override the hold policy.

    0.0 (default) disables the feature — hold always wins. Set via
    ``FLIP_CONFIDENCE_THRESHOLD`` env var (e.g. ``0.15`` for a 15 pp gap).
    Resolution order: ``settings["FLIP_CONFIDENCE_THRESHOLD"]`` → env var →
    ``0.0``. Invalid / non-positive values fall back to 0.0 (disabled).
    Tier-3: changing this on the live VM changes live order routing.
    """
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("FLIP_CONFIDENCE_THRESHOLD")
    if raw is None:
        raw = os.environ.get("FLIP_CONFIDENCE_THRESHOLD", "")
    try:
        val = float(raw)
        return val if val > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def resolve_flip_min_position_age_hours(settings: Optional[Dict[str, Any]] = None) -> float:
    """Minimum hours an existing position must be open before a confidence
    override may flip it.

    0.0 (default) means no minimum — override applies regardless of age.
    Set via ``FLIP_MIN_POSITION_AGE_HOURS`` env var. Companion to
    ``FLIP_CONFIDENCE_THRESHOLD``; has no effect when that threshold is 0.
    Resolution: ``settings["FLIP_MIN_POSITION_AGE_HOURS"]`` → env var → ``0.0``.
    """
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("FLIP_MIN_POSITION_AGE_HOURS")
    if raw is None:
        raw = os.environ.get("FLIP_MIN_POSITION_AGE_HOURS", "")
    try:
        val = float(raw)
        return max(0.0, val)
    except (TypeError, ValueError):
        return 0.0


def _evaluate_confidence_override(
    desired,
    existing_confidence: Optional[float],
    existing_age_hours: Optional[float],
) -> Optional[str]:
    """Return an audit reason string if hold policy should be overridden, else None.

    Preconditions (any failure → return None, hold wins):
      - ``FLIP_CONFIDENCE_THRESHOLD`` > 0 (feature enabled)
      - Both new and existing confidence are known
      - confidence gap >= threshold
      - existing_age_hours >= FLIP_MIN_POSITION_AGE_HOURS (if set)
    """
    threshold = resolve_flip_confidence_threshold()
    if threshold <= 0:
        return None

    # New signal confidence: from DesiredPosition.winning_intent when
    # produced by the aggregator; or from meta["incoming_confidence"] when
    # bridged from an OrderPackage by compute_execution_delta_for_package.
    winning = getattr(desired, "winning_intent", None)
    new_conf: Optional[float] = getattr(winning, "confidence", None) if winning else None
    if new_conf is None:
        new_conf = (getattr(desired, "meta", None) or {}).get("incoming_confidence")
    if new_conf is None or existing_confidence is None:
        return None

    gap = float(new_conf) - float(existing_confidence)
    if gap < threshold:
        return None

    min_age = resolve_flip_min_position_age_hours()
    if min_age > 0 and (existing_age_hours is None or float(existing_age_hours) < min_age):
        return None

    age_str = (
        f" age={float(existing_age_hours):.2f}h≥{min_age:.2f}h"
        if existing_age_hours is not None else ""
    )
    return (
        f"new_conf={float(new_conf):.3f} old_conf={float(existing_confidence):.3f} "
        f"gap={gap:.3f}≥{threshold:.3f}{age_str}"
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyIntent:
    """One strategy's per-tick statement of intent.

    Strategies emit one of these per tick instead of placing orders
    directly. The aggregator combines all intents for the current
    symbol into a single ``DesiredPosition``.

    Fields
    ------
    strategy : str
        Strategy name (e.g. ``"turtle_soup"``, ``"vwap"``). Used as the
        key into ``DEFAULT_PRIORITIES`` and as a tiebreaker for
        deterministic ordering.
    symbol : str
        Trading symbol; must be in ``SUPPORTED_SYMBOLS``.
    side : str
        One of ``"long"`` / ``"short"`` / ``"flat"``. ``"flat"`` means
        "I want no position right now"; the aggregator treats flat as a
        non-vote unless every intent is flat.
    target_qty : float
        Absolute desired position size in base-coin units (BTC for
        BTCUSDT). ``0.0`` is valid and equivalent to ``side="flat"``.
        Must be non-negative.
    priority : int, optional
        Higher wins conflicts. When ``None`` the aggregator falls back
        to ``DEFAULT_PRIORITIES.get(strategy, _UNKNOWN_STRATEGY_PRIORITY)``.
    timestamp : float
        Epoch seconds (float). Earlier wins as the second tiebreaker;
        third tiebreaker is the strategy name alphabetically.
    entry, sl, tp : float, optional
        Order-package fields. The aggregator carries the winning
        intent's entry/sl/tp into the ``DesiredPosition`` so the
        downstream executor can build a complete ``OrderPackage`` from
        the aggregated decision. Strategies that only emit a
        target-without-entry (e.g. a portfolio rebalance hook) leave
        these as ``None``.
    confidence : float
        Strategy-reported probability / score in ``[0, 1]``. Not used by
        the aggregator yet but carried into ``DesiredPosition.meta``
        for downstream logging.
    meta : dict
        Free-form attribution carried through to ``DesiredPosition.meta``.

    Notes
    -----
    Frozen so a strategy cannot mutate its own intent after emission.
    The aggregator is a pure function over an iterable of these.
    """

    strategy: str
    symbol: str
    side: str
    target_qty: float
    priority: Optional[int] = None
    timestamp: float = field(default_factory=lambda: time.time())
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    confidence: float = 0.0
    # Regime tag computed by ``src.runtime.regime.detect_regime`` on the
    # strategy's OWN candles (per-strategy TF — matches how the
    # regime-roster matrix was measured). Optional / default-None so the
    # field stays backwards-compatible: any caller that doesn't populate
    # them gets the same behaviour as before. Phase 2 of the regime
    # router (PERF-20260601-002) reads these to evaluate the policy
    # table in shadow mode (log only; no enforcement until phase 3).
    regime: Optional[str] = None
    adx_14: Optional[float] = None
    # Volatility-axis regime tag (S-MLOPT-S15b) — ``calm`` / ``volatile`` from
    # ``src.runtime.regime.vol_detector`` on the strategy's OWN candles, the
    # second (orthogonal) axis to ``regime`` (trend). Stamped onto signal.meta
    # by the builder (``_stamp_regime_on_meta``) and read by
    # ``intent_from_signal``. Optional / default-None so the field stays
    # backwards-compatible; ``_shadow_regime_gate`` reads it to evaluate the
    # observe-only 2-D ``trend × vol`` policy cell (log only; no enforcement).
    vol_regime: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate at construction so a bad intent never leaks into the
        # aggregator; misuse should fail fast in tests, not produce a
        # silently-wrong target in production.
        if not self.strategy:
            raise ValueError("StrategyIntent.strategy must be non-empty")
        norm_symbol = self.symbol.upper().replace("/", "")
        accepted = supported_symbols()
        if norm_symbol not in accepted:
            raise ValueError(
                f"StrategyIntent.symbol must be one of "
                f"{sorted(accepted)}; got {self.symbol!r}. A symbol becomes "
                "supported by declaring it in the `symbols:` list of an "
                "account in config/accounts.yaml (alongside its per-symbol "
                "position-state wiring) — no code edit needed."
            )
        # Normalise via object.__setattr__ since the dataclass is frozen.
        object.__setattr__(self, "symbol", norm_symbol)
        if self.side not in _VALID_SIDES:
            raise ValueError(
                f"StrategyIntent.side must be one of {sorted(_VALID_SIDES)}; "
                f"got {self.side!r}"
            )
        if self.target_qty < 0:
            raise ValueError(
                f"StrategyIntent.target_qty must be >= 0; got {self.target_qty!r}"
            )
        # Flat intents always have qty=0 — drop any stray non-zero so the
        # aggregator's flat-detection logic stays simple. The reverse
        # (target_qty=0 on a directional side) is intentionally allowed:
        # strategies emit ``target_qty=0`` as the sentinel for "I want
        # a long/short position, the per-account RiskManager decides
        # the qty". This is the production path — the multiplexer never
        # pre-computes qty.
        if self.side == "flat" and self.target_qty != 0:
            object.__setattr__(self, "target_qty", 0.0)

    def effective_priority(self) -> int:
        """Resolved priority used by the aggregator's conflict-resolver."""
        if self.priority is not None:
            return int(self.priority)
        return DEFAULT_PRIORITIES.get(self.strategy, _UNKNOWN_STRATEGY_PRIORITY)


@dataclass(frozen=True)
class DesiredPosition:
    """Aggregator output: the desired net target for a symbol after combining intents.

    Fields
    ------
    symbol : str
        BTCUSDT for this PR.
    side : str
        ``"long"`` / ``"short"`` / ``"flat"``.
    target_qty : float
        Absolute desired position size (>= 0). 0.0 when side is "flat".
    contributing_intents : tuple[StrategyIntent, ...]
        Every intent that was considered (same direction as the winning
        side, after conflict resolution). For "flat" outcomes this lists
        every input intent so the audit trail explains the no-op.
    winning_intent : StrategyIntent, optional
        The intent whose entry/sl/tp/priority decided the target. None
        when the outcome is flat.
    reason : str
        Human-readable single-line explanation; landed in audit logs.
    meta : dict
        Aggregator-supplied attribution (contributing strategy names,
        conflict-resolution path, etc.). Carried into the downstream
        OrderPackage's ``meta`` so the operator can grep for it.
    """

    symbol: str
    side: str
    target_qty: float
    contributing_intents: tuple
    winning_intent: Optional[StrategyIntent]
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionDelta:
    """The minimal action the executor should take to reach ``DesiredPosition``.

    Fields
    ------
    action : str
        One of:
          * ``"noop"``     — current position already matches the target.
          * ``"open"``     — currently flat, need to open a new position.
          * ``"increase"`` — current position is on the right side but
                             smaller than the target; add qty.
          * ``"reduce"``   — current position is on the right side but
                             larger than the target; trim qty.
          * ``"close"``    — desired side is "flat"; flatten the entire
                             current position.
          * ``"flip"``     — current position is on the wrong side; close
                             it AND open the opposite-side target.
    side : str, optional
        Side of the order(s) to place. For ``"flip"`` this is the
        desired side AFTER the flip (the close leg's side is implied as
        the opposite of ``current_side``). ``None`` for ``"noop"``.
    qty_delta : float
        Absolute qty to send when ``action`` is ``"open"`` / ``"increase"``
        / ``"reduce"`` / ``"close"``. For ``"flip"`` this is the qty of
        the SECOND (new-direction) leg — the close leg is sized at
        ``abs(current_qty)``. Always >= 0.
    target_qty : float
        The aggregator's absolute target. Echoed for convenience.
    current_qty : float
        Signed current net position: positive for long, negative for
        short, zero for flat. Echoed for convenience + audit.
    reason : str
        Single-line explanation.
    """

    action: str
    side: Optional[str]
    qty_delta: float
    target_qty: float
    current_qty: float
    reason: str


# ---------------------------------------------------------------------------
# Helpers — bridges from the existing pipeline signal shape
# ---------------------------------------------------------------------------


def intent_from_signal(
    signal: Dict[str, Any],
    *,
    strategy: Optional[str] = None,
    target_qty: float = 0.0,
    priority: Optional[int] = None,
    timestamp: Optional[float] = None,
) -> Optional[StrategyIntent]:
    """Build a ``StrategyIntent`` from a pipeline-shape signal dict.

    The pipeline signal shape is what every builder in
    ``src/runtime/strategy_signal_builders.py`` produces today:
    ``{symbol, side, price/entry_price, stop_loss, take_profit, meta}``.
    This helper extracts the order-package-relevant fields and returns a
    typed intent so the strategy code stays unchanged while the
    multiplexer rewires to the intent layer.

    Returns
    -------
    StrategyIntent | None
        ``None`` when ``signal["side"]`` is non-actionable (``"none"``,
        ``""``, missing) — caller can treat None as "no vote this tick".

    Notes
    -----
    Sizing is **not** decided here. ``target_qty`` defaults to 0 and is
    expected to be passed by the caller (the multiplexer) from a sizing
    hint, or left at 0 so the aggregator treats it as an entry-only
    signal whose actual qty is settled later by the per-account
    ``RiskManager.position_size``. The intent layer does not assume
    knowledge of account balances.
    """
    side_raw = str(signal.get("side") or "").strip().lower()
    if side_raw not in ("buy", "sell"):
        return None

    side = "long" if side_raw == "buy" else "short"
    meta = dict(signal.get("meta") or {})

    strategy_name = (
        strategy
        or meta.get("strategy_name")
        or signal.get("strategy")
        or "unknown"
    )

    entry = (
        signal.get("entry_price")
        or signal.get("price")
        or meta.get("price")
        or meta.get("entry")
    )
    sl = (
        signal.get("stop_loss")
        or meta.get("stop_loss")
        or meta.get("sl")
    )
    tp = (
        signal.get("take_profit")
        or meta.get("take_profit")
        or meta.get("tp")
    )

    # Regime tag — phase 2 of the regime router. The strategy builder
    # stamps these onto signal.meta via _stamp_regime_on_meta (mirrors
    # the audit-row stamping that shipped in phase 1). When the builder
    # didn't stamp (older code paths / tests), the fields stay None and
    # the policy evaluator falls through to the permissive default.
    regime_value = meta.get("regime")
    adx_14_value = meta.get("adx_14")
    # Vol-axis tag (S-MLOPT-S15b) — stamped alongside regime/adx_14 by
    # _stamp_regime_on_meta. Absent on builders not yet wired / test paths →
    # None, and the 2-D policy evaluator falls through to permissive.
    vol_regime_value = meta.get("vol_regime")
    return StrategyIntent(
        strategy=str(strategy_name),
        symbol=str(signal.get("symbol") or "BTCUSDT"),
        side=side,
        target_qty=float(target_qty or 0.0),
        priority=priority,
        timestamp=timestamp if timestamp is not None else time.time(),
        entry=float(entry) if entry is not None else None,
        sl=float(sl) if sl is not None else None,
        tp=float(tp) if tp is not None else None,
        confidence=float(meta.get("confidence") or 0.0),
        regime=str(regime_value) if regime_value is not None else None,
        adx_14=float(adx_14_value) if isinstance(adx_14_value, (int, float)) else None,
        vol_regime=str(vol_regime_value) if vol_regime_value is not None else None,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _flat_position(
    symbol: str,
    contributing: tuple,
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> DesiredPosition:
    return DesiredPosition(
        symbol=symbol,
        side="flat",
        target_qty=0.0,
        contributing_intents=contributing,
        winning_intent=None,
        reason=reason,
        meta=meta or {},
    )


# Lazy-loaded regime-policy cache. Re-read on REGIME_POLICY_RELOAD truthy env
# (no live-reload required for phase 2 — operator restarts the trader after
# editing the table). Importing here (not at module top) keeps the regime
# package out of intents.py's import-time graph for the legacy test paths
# that import this module without the regime config in place.
_REGIME_POLICY_CACHE: Optional[Dict[str, Any]] = None


def _load_regime_policy() -> Dict[str, Any]:
    """Return the cached regime-policy table; load on first use.

    The loader inside ``src.runtime.regime.policy`` returns ``{}`` on any
    failure (missing file, bad YAML, PyYAML not installed) so this never
    raises. Phase 2 is observability-only — an empty / unloaded policy is
    treated as fully permissive, and the aggregator's behaviour is
    identical to pre-phase-2.
    """
    global _REGIME_POLICY_CACHE
    if _REGIME_POLICY_CACHE is None:
        try:
            from src.runtime.regime import load_policy
            _REGIME_POLICY_CACHE = load_policy()
        except Exception:  # noqa: BLE001 — observability-only
            _REGIME_POLICY_CACHE = {}
    return _REGIME_POLICY_CACHE


def _regime_ml_verdict_mode() -> str:
    """Design-A mode: ``off`` (default) | ``shadow`` | ``use``.

    Thin wrapper over ``runtime_flags._regime_ml_verdict_mode`` (env-only here;
    the intent layer has no settings dict). Default ``off`` → deploying this
    code is a behaviour no-op (no ``regime_ml_vol_shadow`` row, zero ML work).
    ``shadow`` emits the ``regime_ml_vol_shadow`` agreement row but the gate
    keeps using the frozen label. ``use`` (Phase 2) substitutes the advisory
    head's ML vol label into the gate DECISION via ``_decision_vol_regime``
    (fail-permissive → frozen when the ML verdict is ``unknown``). NOTE: ``use``
    only changes a real-money outcome when (a) an OFF cell exists for the
    ``(trend, vol)`` pair AND (b) the gated strategy's SYMBOL has an advisory
    regime head — resolution is **per-SYMBOL** (``ml_vol_regime_for_symbol``),
    NOT per-(symbol, timeframe): BTC has the 15m advisory head, so every BTC
    cell resolves the ML label regardless of the strategy's timeframe (the live
    ``trend_vol`` cells gate BTC ``trend_donchian`` (1h) + ``squeeze_breakout_4h``
    (4h) and they DO resolve ML, not frozen); a symbol with no advisory head
    resolves ``unknown`` → frozen (permissive) AND (c) the hard gate is active
    (baseline-on; kill-switch ``REGIME_ROUTER_DISABLED``).
    """
    try:
        from src.runtime.runtime_flags import _regime_ml_verdict_mode as _mode
        return _mode(None)
    except Exception:  # noqa: BLE001 — fail-safe to off
        return "off"


def _emit_ml_vol_shadow_rows(candidates: tuple) -> None:
    """Phase-1 (Design A): emit a ``regime_ml_vol_shadow`` audit row per candidate.

    For each candidate, resolve the advisory head's ``vol_regime`` verdict
    (``ml_vol_regime``) and log a row comparing it against the frozen
    ``intent.vol_regime`` — ``{vol_regime_frozen, vol_regime_ml, p_volatile,
    agree, ml_source, model_id, enforced: false}``. **Observe-only:** this
    function never touches the candidate set; it is the agreement-audit row.
    (Under mode=``use`` the gate DECISION itself does substitute the ML label
    via ``_decision_vol_regime`` — that substitution lives in the gate loops,
    not here.) Called whenever the mode is not ``off`` (``shadow``/``use``); the
    ``off`` path returns before this runs so the default deploy adds ZERO
    overhead.

    Fail-permissive: any exception (per candidate or overall) is swallowed so
    the ML/audit path can never strand a signal.
    """
    try:
        from src.runtime.regime import ml_vol_regime_for_symbol
        from src.utils.signal_audit_logger import log_signal
    except Exception:  # noqa: BLE001 — observability-only
        return
    for intent in candidates:
        try:
            # Per-symbol resolution — identical to the decision path
            # (_decision_vol_regime) so the agreement soak reflects what enforce
            # would actually use.
            verdict = ml_vol_regime_for_symbol(intent.symbol)
            vol_regime_ml = verdict.get("vol_regime")
            frozen = intent.vol_regime
            # ``agree`` is only meaningful when BOTH labels are concrete
            # (neither None/unknown); otherwise it's None (not a false mismatch).
            if (
                frozen in ("calm", "volatile")
                and vol_regime_ml in ("calm", "volatile")
            ):
                agree = bool(frozen == vol_regime_ml)
            else:
                agree = None
            log_signal({
                "event": "regime_ml_vol_shadow",
                "strategy": intent.strategy,
                "symbol": intent.symbol,
                "side": intent.side,
                "vol_regime_frozen": frozen,
                "vol_regime_ml": vol_regime_ml,
                "p_volatile": verdict.get("p_volatile"),
                "agree": agree,
                "ml_source": verdict.get("source"),
                "model_id": verdict.get("model_id"),
                # Phase 1 is observe-only — the gate decision is unchanged.
                "enforced": False,
            })
        except Exception:  # noqa: BLE001 — never strand a signal on the ML path
            continue


def _decision_vol_regime(intent: Any, mode: str) -> tuple:
    """Resolve the ``vol_regime`` the GATE DECISION should use for ``intent``.

    Design-A Phase 2 (``use``): substitute the **advisory** regime head's live
    ML vol label for the frozen ``intent.vol_regime`` when it resolves to a
    concrete ``calm``/``volatile``. The evidence cells in
    ``config/regime_policy.yaml::trend_vol`` were authored under the ML label
    and LOSE money under the frozen label
    (``docs/research/A-vol-gating-OFFcell-design-2026-06-27.md``), so a correct
    enforce MUST gate on the ML label — this is the wiring that was a documented
    placeholder before (the gate previously always used the frozen label).

    Fail-permissive: any mode other than ``use``, an ML verdict of ``unknown``
    (no advisory head for the strategy's ``(symbol, timeframe)`` — e.g. there is
    no BTC 1h/4h advisory head today, only 15m — a cold per-bar cache, or any
    exception) → the frozen ``intent.vol_regime`` is kept unchanged. Never
    raises, so the decision path can't be stranded by the ML lookup.

    Returns ``(effective_vol_regime, frozen_vol_regime, ml_vol_regime_or_None,
    ml_source_or_None)`` so the caller can audit which label drove the gate.
    """
    frozen = getattr(intent, "vol_regime", None)
    if mode != "use":
        return frozen, frozen, None, None
    try:
        # Resolve by SYMBOL (not the strategy's timeframe): the validated A/B
        # applied the single 15m advisory head's vol label to every BTC cell —
        # the vol regime is a per-symbol market label. A per-strategy-timeframe
        # lookup would return ``unknown`` for the live 1h/4h cells (no 1h/4h
        # advisory head) and silently fall back to the money-losing frozen label.
        from src.runtime.regime import ml_vol_regime_for_symbol
        verdict = ml_vol_regime_for_symbol(intent.symbol)
        ml = verdict.get("vol_regime")
        source = verdict.get("source")
        if ml in ("calm", "volatile"):
            return ml, frozen, ml, source  # ML label drives the decision
        return frozen, frozen, ml, source  # ML unknown → keep frozen (permissive)
    except Exception:  # noqa: BLE001 — fail-permissive: keep the frozen label
        return frozen, frozen, None, None


def _shadow_regime_gate(candidates: tuple) -> None:
    """Evaluate every candidate intent against the regime policy table and
    emit a ``regime_shadow_gate`` audit row when an OFF cell WOULD have
    suppressed it. Phase 2 — log only; never mutates the candidate set
    and never raises. The aggregator's downstream behaviour is unchanged.

    S-MLOPT-S15b adds the **vol axis**: each intent carries an optional
    ``vol_regime`` (``calm`` / ``volatile``) stamped by the signal builder, so
    ``would_gate`` also returns the observe-only 2-D ``trend × vol`` verdict.
    A row is emitted when the 1-D trend cell OR the 2-D vol cell would gate;
    every row carries both axes (``regime``/``vol_regime`` + the per-axis cell)
    so a later analysis can split would-gate evidence by volatility. Both axes
    are ``enforced: false`` — phase-3 (Tier-3) is the enforcement gate.

    Design A (Phase 1, default-off): when ``REGIME_ML_VERDICT_MODE`` is
    ``shadow``/``use`` this ALSO emits a ``regime_ml_vol_shadow`` row per
    candidate comparing the advisory head's ML ``vol_regime`` against the
    frozen one. The gate decision is UNCHANGED (still the frozen label); the
    ``off`` default short-circuits before any ML work runs.
    """
    # Design-A Phase-1 ML-vol shadow audit — gated, observe-only, fires
    # independently of the trend policy below (a different axis). The ``off``
    # default returns here with ZERO added overhead.
    if _regime_ml_verdict_mode() != "off":
        _emit_ml_vol_shadow_rows(candidates)
    try:
        from src.runtime.regime import would_gate
        from src.utils.signal_audit_logger import log_signal
        policy = _load_regime_policy()
    except Exception:  # noqa: BLE001
        return
    if not policy:
        # Empty / missing table → no shadow rows; permissive everywhere.
        return
    mode = _regime_ml_verdict_mode()
    for intent in candidates:
        eff_vol, frozen_vol, ml_vol, ml_src = _decision_vol_regime(intent, mode)
        try:
            verdict = would_gate(
                strategy=intent.strategy,
                side=intent.side,
                regime=intent.regime,
                policy=policy,
                vol_regime=eff_vol,
            )
        except Exception:  # noqa: BLE001
            continue
        trend_gated = bool(verdict.get("gated"))
        vol_gated = bool(verdict.get("vol_gated"))
        if not (trend_gated or vol_gated):
            continue
        try:
            log_signal({
                "event": "regime_shadow_gate",
                "strategy": intent.strategy,
                "symbol": intent.symbol,
                "side": intent.side,
                # Trend axis (1-D) — unchanged fields.
                "regime": intent.regime,
                "adx_14": intent.adx_14,
                "gated": trend_gated,
                "cell": verdict.get("cell"),
                "reason": verdict.get("reason"),
                # Vol axis (2-D, S-MLOPT-S15b). ``vol_regime`` is the label the
                # would-gate DECISION used — the ML label under mode=use (Design-A
                # Phase 2), else the frozen stamp. The frozen + ML labels + which
                # drove it are kept alongside for audit.
                "vol_regime": eff_vol,
                "vol_regime_frozen": frozen_vol,
                "vol_regime_ml": ml_vol,
                "vol_label_source": "ml" if (mode == "use" and ml_vol in ("calm", "volatile")) else "frozen",
                "vol_gated": vol_gated,
                "vol_cell": verdict.get("vol_cell"),
                "vol_reason": verdict.get("vol_reason"),
                # Neither axis is acted on. The audit row exists to
                # accumulate would-gate evidence for the phase-3 decision.
                "enforced": False,
            })
        except Exception:  # noqa: BLE001
            pass


def _regime_router_active() -> bool:
    """Regime hard-gate — **BASELINE ON**, kill-switch via ``REGIME_ROUTER_DISABLED``.

    The regime router was validated and promoted to a live order-routing
    influence (the Design-A vol-gate go-live, 2026-06-28). It is now a
    *required* live capability, so per the Prime Directive it must NOT sit
    behind a default-off ``*_ENABLED`` flag: if such a var were dropped on a
    redeploy/VM-migration the gate would **silently stop enforcing** and the
    money-losing ``trend_vol`` OFF-cells would trade again — exactly the
    netting-guard / Ampere-migration failure class (a default-off gate silently
    reverting). Baseline-on means the live behaviour survives an env drop.

    Resolution (highest precedence first):

    1. ``REGIME_ROUTER_DISABLED`` truthy → **off** (the sanctioned kill-switch:
       one env flip + restart, no redeploy — the rollback path).
    2. A leftover *explicit* falsy ``REGIME_ROUTER_ENABLED`` (``0``/``false``/
       ``no``/``off``) → **off** (legacy rollback honoured so a VM mid-migration
       with the old var set isn't surprised).
    3. Otherwise → **on** (baseline). Unset, ``REGIME_ROUTER_ENABLED=1``, or any
       non-falsy value all resolve active.

    NOTE: a non-live consumer that must NOT enforce (the backtest A/B baseline
    arm, a unit test asserting shadow-only behaviour) sets
    ``REGIME_ROUTER_DISABLED=1`` explicitly — it can no longer rely on an
    unset-env default. ``scripts/backtest_system.py`` does this for every run
    that isn't ``--regime-router on``.
    """
    import os as _os

    def _truthy(v):
        return str(v or "").strip().lower() in {"1", "true", "yes", "on"}

    # (1) Sanctioned kill-switch — wins over everything. allow-silent: this is
    # the documented rollback escape hatch, not a hidden trading-mode gate; the
    # live/dry contract remains RiskManager.dry_run only.
    if _truthy(_os.environ.get("REGIME_ROUTER_DISABLED")):  # allow-silent: regime-router kill-switch
        return False
    # (2) Legacy explicit-off rollback (REGIME_ROUTER_ENABLED=0/false/...).
    legacy = _os.environ.get("REGIME_ROUTER_ENABLED")  # allow-silent: legacy regime-router rollback switch
    if legacy is not None and legacy.strip() != "" and not _truthy(legacy):
        return False
    # (3) Baseline ON.
    return True


def _hard_regime_gate(candidates: tuple) -> tuple:
    """Phase 3 (PERF-20260601-006, Tier-3): drop OFF-cell candidates from
    the aggregation AND emit a ``regime_hard_gate`` audit row with
    ``enforced: true``. Returns the surviving tuple in input order.

    Symmetric with ``_shadow_regime_gate`` so the OFF-cell verdict (per
    ``would_gate``) is computed identically — phase 3 is "phase 2 + drop"
    rather than a re-derived gate. The only differences vs phase 2:

    - the gated intent is **removed** from the returned tuple (so the
      aggregator never sees it for reinforcement / conflict);
    - the audit event is ``regime_hard_gate`` (not ``regime_shadow_gate``)
      and carries ``enforced: true`` so a later ``/performance-review``
      can cleanly partition "would have gated" (phase 2 history) from
      "did gate" (phase 3 history).

    Best-effort: any exception in the policy load or per-intent verdict
    falls back to keeping the intent (fail-permissive). A live-path
    failure must never silently drop a tradeable signal — the bias is
    toward the existing pre-phase-3 behaviour.

    Design A (Phase 1, default-off): when ``REGIME_ML_VERDICT_MODE`` is
    ``shadow``/``use`` this ALSO emits a ``regime_ml_vol_shadow`` row per
    candidate (observe-only). It does NOT influence which intents are dropped —
    the enforcement still keys on the frozen ``intent.vol_regime`` via
    ``would_gate``. The ``off`` default short-circuits before any ML work runs.
    """
    # Design-A Phase-1 ML-vol shadow audit — gated, observe-only, fires
    # independently of the trend policy / enforcement below (a different axis,
    # never alters the kept set). The ``off`` default returns here with ZERO
    # added overhead.
    if _regime_ml_verdict_mode() != "off":
        _emit_ml_vol_shadow_rows(candidates)
    try:
        from src.runtime.regime import would_gate
        from src.utils.signal_audit_logger import log_signal
        policy = _load_regime_policy()
    except Exception:  # noqa: BLE001
        return candidates
    if not policy:
        return candidates
    kept: list = []
    mode = _regime_ml_verdict_mode()
    for intent in candidates:
        eff_vol, frozen_vol, ml_vol, ml_src = _decision_vol_regime(intent, mode)
        try:
            verdict = would_gate(
                strategy=intent.strategy,
                side=intent.side,
                regime=intent.regime,
                policy=policy,
                vol_regime=eff_vol,
            )
        except Exception:  # noqa: BLE001
            # Fail-permissive: an unverifiable verdict keeps the intent
            # so a policy-loader bug never silently strands a live
            # signal. The shadow gate's matching exception path also
            # skips the row rather than guessing.
            kept.append(intent)
            continue
        trend_gated = bool(verdict.get("gated"))
        vol_gated = bool(verdict.get("vol_gated"))
        # ML-only-enforce guard: a VOL cell may only DROP an intent when the vol
        # label came from the advisory ML head (vol_label_source == "ml"). The
        # trend_vol cells LOSE money under the frozen label, so a vol gate on a
        # frozen-fallback (no advisory head for the symbol) must NOT enforce —
        # it stays permissive. The 1-D TREND (ADX) axis is ML-independent and
        # always enforces. So a symbol without an advisory vol head is safely
        # never vol-gated, and enforce activates per-symbol as heads promote.
        vol_is_ml = mode == "use" and ml_vol in ("calm", "volatile")
        vol_enforced = vol_gated and vol_is_ml
        if not (trend_gated or vol_enforced):
            kept.append(intent)
            continue
        # Phase 3 enforcement: drop the intent + audit the action.
        try:
            log_signal({
                "event": "regime_hard_gate",
                "strategy": intent.strategy,
                "symbol": intent.symbol,
                "side": intent.side,
                "regime": intent.regime,
                "adx_14": intent.adx_14,
                "gated": trend_gated,
                "cell": verdict.get("cell"),
                "reason": verdict.get("reason"),
                # ``vol_regime`` = the label the enforce DECISION used (ML under
                # mode=use, else frozen); frozen + ML kept alongside for audit.
                "vol_regime": eff_vol,
                "vol_regime_frozen": frozen_vol,
                "vol_regime_ml": ml_vol,
                "vol_label_source": "ml" if vol_is_ml else "frozen",
                "vol_gated": vol_gated,
                # ``vol_enforced``: the vol axis actually drove this drop (only
                # true when the label was ML-sourced). A vol_gated-but-frozen
                # cell shows vol_enforced:false and the drop (if any) is the
                # trend axis.
                "vol_enforced": vol_enforced,
                "vol_cell": verdict.get("vol_cell"),
                "vol_reason": verdict.get("vol_reason"),
                # PHASE 3: enforced -> the intent is dropped from the
                # aggregator's candidate set right here, before the
                # reinforcement / conflict-resolution logic runs.
                "enforced": True,
            })
        except Exception:  # noqa: BLE001
            pass
        # NOTE: intent intentionally NOT appended to `kept` — drop it.
    return tuple(kept)


def aggregate_intents(
    intents: Iterable[StrategyIntent],
    *,
    symbol: str = "BTCUSDT",
) -> DesiredPosition:
    """Combine per-strategy intents for ``symbol`` into a single net target.

    Rules (in order)
    ----------------
    1. **Filter to symbol.** Intents for any other symbol are silently
       dropped from this aggregation — symbol routing is the caller's
       job. The aggregator never mixes symbols.

    2. **Drop flat intents from the vote unless every intent is flat.**
       A flat intent is "I don't want a position"; it does not pull the
       net position to zero. The aggregator only goes flat when there
       are no non-flat intents at all (or when conflict resolution
       picks a flat winner, which only happens if every intent is flat).

    3. **Same-direction reinforcement: max target_qty wins.** If every
       non-flat intent is on the same side, the aggregator returns
       that side with ``target_qty = max(intent.target_qty for intent in
       intents)``. The entry/sl/tp / winning_intent come from the
       intent whose ``target_qty`` is the largest (tiebreaker:
       highest effective priority, then earliest timestamp, then
       strategy name alphabetical).

       Rationale: two strategies both wanting LONG BTC is conviction
       reinforcement — keep at least the larger valid target. We do
       NOT sum, because summing would double-count exposure when both
       strategies are sizing against the same risk budget. The
       per-account RiskManager still applies its own cap on top.

    4. **Conflict resolution: deterministic priority.** When at least
       one intent says long and at least one says short, the winner is
       the intent with the highest ``effective_priority()``. Tiebreakers
       (in order): earliest ``timestamp``, then strategy name
       alphabetical. The aggregator NEVER places both orders; it picks
       a side and the loser's intent is recorded under ``meta`` so the
       audit trail explains the dropped signal.

    5. **No-vote result.** Empty input, or every intent for the wrong
       symbol, or every intent flat → ``side="flat"``, ``target_qty=0``.

    Parameters
    ----------
    intents : Iterable[StrategyIntent]
    symbol : str
        Symbol to aggregate; BTCUSDT in this PR. Other symbols are
        dropped from the candidate set so a multi-symbol multiplexer
        can call this once per symbol safely.

    Returns
    -------
    DesiredPosition
    """
    norm_symbol = symbol.upper().replace("/", "")
    candidates = tuple(
        i for i in intents
        if i.symbol == norm_symbol
    )

    # Regime router (PERF-20260601-002 §5):
    #
    # * **Hard gate (BASELINE, Tier-3 live capability)** — ``_hard_regime_gate``
    #   **drops** OFF-cell intents from ``candidates`` BEFORE the reinforcement /
    #   conflict-resolution logic runs AND emits a ``regime_hard_gate`` row
    #   (``enforced: true``). This is the default since the Design-A vol-gate
    #   go-live (2026-06-28): a *required* live capability must not sit behind a
    #   default-off flag (Prime Directive), so the router is baseline-on and
    #   survives an env drop. Kill-switch / rollback is ``REGIME_ROUTER_DISABLED``
    #   (one env flip + restart, no redeploy).
    # * **Shadow (observability-only, opt-OUT)** — when the router is disabled,
    #   ``_shadow_regime_gate`` instead evaluates each candidate and emits a
    #   ``regime_shadow_gate`` audit row (``enforced: false``); the aggregator's
    #   decision is UNCHANGED. The backtest A/B baseline arm + shadow-only tests
    #   reach this path by setting ``REGIME_ROUTER_DISABLED=1``.
    #
    # Exactly one of the two runs per tick so the audit log cleanly
    # partitions "would have gated" from "did gate". Both swallow all
    # failures internally so a missing policy file / bad cell cannot
    # break the tick.
    if _regime_router_active():
        candidates = _hard_regime_gate(candidates)
    else:
        _shadow_regime_gate(candidates)

    if not candidates:
        return _flat_position(
            symbol=norm_symbol,
            contributing=tuple(),
            reason="no_intents_for_symbol",
        )

    non_flat = tuple(i for i in candidates if i.side != "flat")
    if not non_flat:
        return _flat_position(
            symbol=norm_symbol,
            contributing=candidates,
            reason="all_intents_flat",
            meta={
                "contributing_strategies": sorted({i.strategy for i in candidates}),
            },
        )

    longs = tuple(i for i in non_flat if i.side == "long")
    shorts = tuple(i for i in non_flat if i.side == "short")

    # No conflict — every non-flat intent agrees on direction.
    if not (longs and shorts):
        agreed_side = "long" if longs else "short"
        same_side = longs or shorts
        # Pick the intent with the largest target_qty; this is the
        # spec's "keep at least the larger valid target size" rule.
        # Deterministic tiebreakers prevent flapping when two strategies
        # happen to publish the same target on the same tick.
        winner = max(
            same_side,
            key=lambda i: (
                i.target_qty,
                i.effective_priority(),
                -i.timestamp,
                # Alphabetical strategy name negation: max() wants
                # "earlier alphabet" to win, so we negate by sorting
                # descending; flip with a tuple trick.
                tuple(-ord(c) for c in i.strategy.lower()),
            ),
        )
        contributing = same_side
        # P3 conviction arbitration — OBSERVE-ONLY (design § 3.4, no gate). Log
        # what conviction-weighted reinforcement WOULD pick vs today's max-qty
        # winner; the decision below is unchanged. Fail-permissive.
        annotate_conviction_arbitration(
            same_side,
            symbol=norm_symbol,
            resolution="same_direction",
            actual_winner_strategy=winner.strategy,
            actual_target_qty=float(winner.target_qty),
        )
        return DesiredPosition(
            symbol=norm_symbol,
            side=agreed_side,
            target_qty=float(winner.target_qty),
            contributing_intents=contributing,
            winning_intent=winner,
            reason=(
                f"same_direction_reinforcement: {len(contributing)} "
                f"intents agree on {agreed_side}; max target_qty="
                f"{winner.target_qty} from strategy={winner.strategy}"
            ),
            meta={
                "resolution": "same_direction",
                "contributing_strategies": sorted({i.strategy for i in contributing}),
                "winning_strategy": winner.strategy,
            },
        )

    # Conflict — priority-based deterministic resolution. Highest
    # effective_priority wins; tiebreakers earliest timestamp then
    # strategy name alphabetical.
    def _conflict_sort_key(intent: StrategyIntent) -> tuple:
        return (
            -intent.effective_priority(),
            intent.timestamp,
            intent.strategy.lower(),
        )

    ordered = sorted(non_flat, key=_conflict_sort_key)
    winner = ordered[0]
    losers = tuple(ordered[1:])
    # The contributing set for the winning side is every same-direction
    # intent (so the audit trail reflects reinforcement on the winning
    # leg as well, if any). target_qty is the max across that subset —
    # same rule as the no-conflict branch, applied to the winning side.
    winning_side_intents = tuple(i for i in non_flat if i.side == winner.side)
    target_qty = max(i.target_qty for i in winning_side_intents)
    # P3 conviction arbitration — OBSERVE-ONLY (design § 3.4, no gate). Log what
    # the higher-conviction intent WOULD have been vs today's priority winner;
    # the priority decision below is unchanged. Fail-permissive.
    annotate_conviction_arbitration(
        non_flat,
        symbol=norm_symbol,
        resolution="priority_conflict",
        actual_winner_strategy=winner.strategy,
        actual_target_qty=float(target_qty),
    )
    return DesiredPosition(
        symbol=norm_symbol,
        side=winner.side,
        target_qty=float(target_qty),
        contributing_intents=winning_side_intents,
        winning_intent=winner,
        reason=(
            f"conflict_resolved_by_priority: winner={winner.strategy}"
            f"(priority={winner.effective_priority()}, side={winner.side}); "
            f"dropped={[(lost.strategy, lost.side, lost.effective_priority()) for lost in losers]}"
        ),
        meta={
            "resolution": "priority_conflict",
            "winning_strategy": winner.strategy,
            "winning_priority": winner.effective_priority(),
            "dropped_intents": [
                {
                    "strategy": lost.strategy,
                    "side": lost.side,
                    "priority": lost.effective_priority(),
                    "target_qty": lost.target_qty,
                }
                for lost in losers
            ],
            "contributing_strategies": sorted({i.strategy for i in winning_side_intents}),
        },
    )


# ---------------------------------------------------------------------------
# Delta execution
# ---------------------------------------------------------------------------


def compute_execution_delta(
    current_signed_qty: float,
    desired: DesiredPosition,
    *,
    qty_precision: int = 6,
    min_delta: float = 0.0,
    flip_policy: Optional[str] = None,
    existing_confidence: Optional[float] = None,
    existing_age_hours: Optional[float] = None,
) -> ExecutionDelta:
    """Translate a ``DesiredPosition`` + the current net position into a delta.

    Parameters
    ----------
    current_signed_qty : float
        The account's current net qty for ``desired.symbol``. **Signed**:
        positive = long, negative = short, zero = flat.
    desired : DesiredPosition
        Output of ``aggregate_intents``.
    qty_precision : int
        Decimal places to round all qty arithmetic to so tiny
        floating-point residuals don't trigger spurious orders. Default
        is 6 dp which sits comfortably below Bybit's 0.001 BTC min-lot.
    min_delta : float
        Treat any abs delta below this as zero (returns ``"noop"``).
        Useful when the dispatch path wants to suppress sub-tick noise.

    Returns
    -------
    ExecutionDelta

    Examples
    --------
    Same-direction reinforcement, larger target than current:
        current=+0.01 (long), desired=long 0.03 → ``action="increase"``,
        ``side="long"``, ``qty_delta=0.02``.

    Conflict resolved to opposite side:
        current=+0.01 (long), desired=short 0.02 → ``action="flip"``,
        ``side="short"``, ``qty_delta=0.02`` (close leg's qty=0.01 is
        implied by ``abs(current_qty)``).

    Same-direction but already at-or-above target:
        current=+0.05 (long), desired=long 0.03 → ``action="reduce"``,
        ``side="short"`` (reduce-only sell), ``qty_delta=0.02``.

    Desired is flat:
        current=+0.01 (long), desired=flat → ``action="close"``,
        ``side="short"``, ``qty_delta=0.01``.

    Already at target:
        current=+0.03, desired=long 0.03 → ``action="noop"``.
    """
    current = round(float(current_signed_qty), qty_precision)
    target = round(float(desired.target_qty), qty_precision)

    # Currently flat.
    if current == 0:
        if desired.side == "flat" or target == 0:
            return ExecutionDelta(
                action="noop",
                side=None,
                qty_delta=0.0,
                target_qty=target,
                current_qty=current,
                reason="already_flat_and_target_flat",
            )
        return ExecutionDelta(
            action="open",
            side=desired.side,
            qty_delta=target,
            target_qty=target,
            current_qty=current,
            reason=f"open_new_{desired.side}_target={target}",
        )

    current_side = "long" if current > 0 else "short"
    current_abs = abs(current)

    # Desired is flat → close out.
    if desired.side == "flat" or target == 0:
        return ExecutionDelta(
            action="close",
            side="short" if current_side == "long" else "long",
            qty_delta=current_abs,
            target_qty=0.0,
            current_qty=current,
            reason=f"close_existing_{current_side}_qty={current_abs}",
        )

    # Current and desired agree on direction → increase / reduce / noop.
    if desired.side == current_side:
        delta = round(target - current_abs, qty_precision)
        if abs(delta) <= min_delta:
            return ExecutionDelta(
                action="noop",
                side=None,
                qty_delta=0.0,
                target_qty=target,
                current_qty=current,
                reason=(
                    f"at_target: current={current_abs} matches target={target} "
                    f"within min_delta={min_delta}"
                ),
            )
        if delta > 0:
            return ExecutionDelta(
                action="increase",
                side=current_side,
                qty_delta=delta,
                target_qty=target,
                current_qty=current,
                reason=(
                    f"increase_{current_side}: current={current_abs} → target={target} "
                    f"(delta=+{delta})"
                ),
            )
        # delta < 0: position is bigger than desired target.
        # Reduce-only order on the opposite side.
        return ExecutionDelta(
            action="reduce",
            side="short" if current_side == "long" else "long",
            qty_delta=abs(delta),
            target_qty=target,
            current_qty=current,
            reason=(
                f"reduce_{current_side}: current={current_abs} → target={target} "
                f"(delta=-{abs(delta)})"
            ),
        )

    # Opposite-direction → behaviour governed by the flip policy (see the
    # FLIP_POLICIES block above). Default "hold" (since 2026-05-31, PR #2451,
    # walk-forward verified) keeps the position for the owner's monitor()/SL/TP
    # to exit; "reverse" (legacy close-and-reopen) and "flat" are the
    # operator-gated alternatives via FLIP_POLICY on the live VM.
    policy = (
        str(flip_policy).strip().lower()
        if flip_policy is not None
        else resolve_flip_policy()
    )
    if policy not in FLIP_POLICIES:
        policy = _DEFAULT_FLIP_POLICY

    # Confidence-weighted hold override: when FLIP_CONFIDENCE_THRESHOLD > 0
    # and the new signal's confidence sufficiently exceeds the existing
    # position's entry confidence (and the position is old enough), allow
    # the flip despite the hold policy. _hold_override_reason is non-None
    # only when the override fires; it is threaded into the flip reason below
    # for a complete audit trail.
    _hold_override_reason: Optional[str] = None

    if policy == "hold":
        # Keep the current position; let the owning strategy's monitor()/SL/TP
        # exit it. Suppresses the fee-paying close-and-reverse churn the system
        # backtest flagged as the #1 portfolio-level loss driver. Surfaced as a
        # noop so the coordinator journals it (loud, auditable — not a silent
        # state change).
        _hold_override_reason = _evaluate_confidence_override(
            desired, existing_confidence, existing_age_hours
        )
        if _hold_override_reason is None:
            return ExecutionDelta(
                action="noop",
                side=None,
                qty_delta=0.0,
                target_qty=target,
                current_qty=current,
                reason=(
                    f"flip_suppressed_hold_policy: desired {desired.side} opposes "
                    f"current {current_side} (qty={current_abs}); holding for owner exit"
                ),
            )
        # Override triggered — fall through to the flip return below.
        import logging as _logging
        _logging.getLogger(__name__).info(
            "[intents] hold-policy confidence override for %s: %s → "
            "allowing flip %s→%s",
            desired.symbol, _hold_override_reason, current_side, desired.side,
        )

    if policy == "flat" and _hold_override_reason is None:
        # Close the conflicting position but stand aside — do not re-open.
        return ExecutionDelta(
            action="close",
            side="short" if current_side == "long" else "long",
            qty_delta=current_abs,
            target_qty=0.0,
            current_qty=current,
            reason=(
                f"flip_flat_policy: close {current_side} qty={current_abs} on "
                f"opposite {desired.side} vote; no re-open"
            ),
        )

    # policy == "reverse" (default), OR hold-policy confidence override:
    # close leg qty=current_abs (implicit), new leg qty=target. The caller
    # is responsible for sequencing the two legs (close first, then open)
    # and for applying the per-account risk gate to each.
    _flip_prefix = (
        f"hold_confidence_override ({_hold_override_reason}): "
        if _hold_override_reason else ""
    )
    return ExecutionDelta(
        action="flip",
        side=desired.side,
        qty_delta=target,
        target_qty=target,
        current_qty=current,
        reason=(
            f"{_flip_prefix}flip_from_{current_side}_to_{desired.side}: "
            f"close {current_abs} then open {target}"
        ),
    )


# ---------------------------------------------------------------------------
# OrderPackage bridge — used by Coordinator.multi_account_execute
# ---------------------------------------------------------------------------


# Sentinel passed by ``intent_multiplexer._desired_to_pipeline_signal``
# on the package's meta so the dispatcher can recognise an intent-mode
# package without doing a duck-type check on the rest of meta. Kept here
# (not in intent_multiplexer.py) so both the writer and the reader
# import from the same module.
INTENT_MODE_META_KEY = "aggregated_via"
INTENT_MODE_META_VALUE = "multi_strategy_intent_layer"


def package_is_intent_mode(pkg) -> bool:
    """True when *pkg* was produced by the intent-aware multiplexer."""
    meta = getattr(pkg, "meta", None) or {}
    return meta.get(INTENT_MODE_META_KEY) == INTENT_MODE_META_VALUE


def compute_execution_delta_for_package(
    pkg,
    current_signed_qty: float,
    *,
    risk_sized_qty: float,
    qty_precision: int = 6,
    min_delta: float = 0.0,
    flip_policy: Optional[str] = None,
    existing_confidence: Optional[float] = None,
    existing_age_hours: Optional[float] = None,
) -> ExecutionDelta:
    """Bridge an ``OrderPackage`` into an ``ExecutionDelta``.

    ``Coordinator.multi_account_execute`` already computes
    ``risk_sized_qty`` via the per-account RiskManager. That qty is the
    AUTHORITATIVE cap on how much to send for an open/increase — the
    intent layer never gets to size above what the risk manager
    approved. The aggregator's ``aggregated_target_qty`` (when
    non-zero) acts as an upper bound on top.

    Resolution order for the effective target:
      1. ``min(risk_sized_qty, aggregated_target_qty)`` when the intent
         layer set a non-zero target (i.e. the strategy expressed an
         explicit size preference).
      2. ``risk_sized_qty`` otherwise — the production path. Strategies
         currently emit ``target_qty=0`` as the "RiskManager decides"
         sentinel, so this branch is the common one.

    Direction comes from ``pkg.direction`` (the aggregator's winning
    side, already resolved). The delta is computed against the signed
    current net position.

    Parameters
    ----------
    pkg : OrderPackage
        Package coming out of the intent multiplexer.
    current_signed_qty : float
        Signed net position for ``(account, pkg.symbol)``. Positive long,
        negative short, zero flat. Read from the trade journal by the
        caller — see ``src.runtime.positions.current_net_position_qty``.
    risk_sized_qty : float
        Output of the per-account RiskManager's ``position_size``. This
        is the cap; the delta will never exceed this.
    qty_precision, min_delta :
        Passed through to ``compute_execution_delta``.

    Returns
    -------
    ExecutionDelta
    """
    if pkg.direction not in ("long", "short"):
        raise ValueError(
            f"compute_execution_delta_for_package: pkg.direction must be "
            f"'long'/'short' for an intent-mode package; got {pkg.direction!r}"
        )

    aggregated_target = float((pkg.meta or {}).get("aggregated_target_qty") or 0.0)
    risk_qty = float(risk_sized_qty)
    if aggregated_target > 0:
        effective_target = min(aggregated_target, risk_qty)
    else:
        effective_target = risk_qty

    desired = DesiredPosition(
        symbol=pkg.symbol,
        side=pkg.direction,
        target_qty=max(0.0, effective_target),
        contributing_intents=tuple(),
        winning_intent=None,
        reason="bridged_from_order_package",
        meta={
            "aggregated_target_qty": aggregated_target,
            "risk_sized_qty": risk_qty,
            # Surface pkg.confidence for _evaluate_confidence_override, which
            # reads meta["incoming_confidence"] when winning_intent is None.
            "incoming_confidence": getattr(pkg, "confidence", None),
        },
    )
    return compute_execution_delta(
        current_signed_qty=current_signed_qty,
        desired=desired,
        qty_precision=qty_precision,
        min_delta=min_delta,
        flip_policy=flip_policy,
        existing_confidence=existing_confidence,
        existing_age_hours=existing_age_hours,
    )
