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
- ``SUPPORTED_SYMBOLS``    — whitelist; currently ``{"BTCUSDT"}``

Future-strategy plug-in pattern
-------------------------------
Adding ``ict_scalp`` later means: (a) implement its signal builder, (b)
add ``"ict_scalp": <priority>`` to ``DEFAULT_PRIORITIES`` or pass
``priority=`` explicitly, (c) include the strategy name in the
multiplexer's collect-from list. The aggregator and delta computer do
not change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# This PR is single-symbol. Adding a symbol later means appending it here
# AND wiring per-symbol position-state lookups — the latter is out of
# scope until at least one more symbol is funded on a live account.
SUPPORTED_SYMBOLS: frozenset[str] = frozenset({"BTCUSDT"})


# Higher priority wins conflicts. Tiebreaker order is documented on
# ``aggregate_intents``. The Turtle Soup > VWAP order matches the
# operator's stated risk preference: Turtle Soup is the higher-conviction
# MTF setup; VWAP is the higher-frequency mean-reversion strategy. Tune
# this map (or override via StrategyIntent.priority) without changing
# the aggregator.
DEFAULT_PRIORITIES: Dict[str, int] = {
    "turtle_soup": 50,
    "vwap": 40,
}

# Priority used when a strategy is not listed in DEFAULT_PRIORITIES and
# the intent does not carry an explicit priority. Picked deliberately
# below the in-scope strategies so a misconfigured new strategy never
# silently overrides Turtle Soup / VWAP.
_UNKNOWN_STRATEGY_PRIORITY: int = 10


_VALID_SIDES: frozenset[str] = frozenset({"long", "short", "flat"})


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
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate at construction so a bad intent never leaks into the
        # aggregator; misuse should fail fast in tests, not produce a
        # silently-wrong target in production.
        if not self.strategy:
            raise ValueError("StrategyIntent.strategy must be non-empty")
        norm_symbol = self.symbol.upper().replace("/", "")
        if norm_symbol not in SUPPORTED_SYMBOLS:
            raise ValueError(
                f"StrategyIntent.symbol must be one of "
                f"{sorted(SUPPORTED_SYMBOLS)} for this PR; got {self.symbol!r}. "
                "Multi-symbol routing is intentionally out of scope; add a "
                "symbol to SUPPORTED_SYMBOLS only alongside the per-symbol "
                "position-state wiring."
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

    # Opposite-direction → flip. Close leg qty=current_abs (implicit),
    # new leg qty=target. The caller is responsible for sequencing the
    # two legs (close first, then open) and for applying the per-account
    # risk gate to each.
    return ExecutionDelta(
        action="flip",
        side=desired.side,
        qty_delta=target,
        target_qty=target,
        current_qty=current,
        reason=(
            f"flip_from_{current_side}_to_{desired.side}: close {current_abs} "
            f"then open {target}"
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
        },
    )
    return compute_execution_delta(
        current_signed_qty=current_signed_qty,
        desired=desired,
        qty_precision=qty_precision,
        min_delta=min_delta,
    )
