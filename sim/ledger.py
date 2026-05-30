"""SIM ledger + funnel bookkeeping (Phase 1).

The ledger is the bookkeeper half of the harness. It records, per bar:

  * the **funnel counts** — how a strategy's signals attrit at each stage
    (emitted -> survived the multiplexer -> passed the risk gate -> filled).
    This is the headline Phase-1 deliverable: it answers "how many of a
    strategy's solo-backtest trades actually survive the INTEGRATED funnel
    when it competes for one account through ``aggregate_intents``?"
  * the realized **trades** (entry/exit/R/pnl) for the portfolio equity curve
    and per-strategy attribution.

No trading logic here — pure accounting. Deterministic: same inputs produce
byte-identical output so variants are comparable (design doc § 4 rule 4).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


class FunnelStage(enum.Enum):
    """The decision-funnel stages a strategy signal passes (or dies at).

    Mirrors the live order path: a strategy emits a directional signal, the
    intent multiplexer (``aggregate_intents``) may drop it (lost a conflict,
    or another strategy's same-side intent won the reinforcement tiebreak),
    the risk gate may refuse it, and only the survivors become fills.
    """

    EMITTED = "emitted"            # strategy produced a directional order_package
    SURVIVED_MUX = "survived_mux"  # this strategy's intent won aggregate_intents
    PASSED_RISK = "passed_risk"    # the aggregated order passed the risk gate
    FILLED = "filled"             # a position was opened in the sim


@dataclass
class SimTrade:
    """One realized simulated trade (a fill that opened then closed)."""

    strategy: str
    symbol: str
    direction: str            # "long" | "short"
    entry_ts: str             # ISO UTC of the bar that opened it
    entry: float
    sl: float
    tp: float
    exit_ts: Optional[str] = None
    exit: Optional[float] = None
    exit_reason: Optional[str] = None   # "tp" | "sl" | "timeout" | "eod"
    r_multiple: Optional[float] = None  # net-of-fee realized R (WITHOUT model)
    confidence: float = 0.0
    # Phase 2 (models-in-the-loop): the advisory size factor a model/quorum
    # applied to this decision (1.0 = no influence / no model), and the
    # resulting model-adjusted R (r_multiple * model_factor). None when SIM
    # ran without a model scorer.
    model_factor: Optional[float] = None
    model_scores: Optional[dict[str, float]] = None
    r_multiple_model: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def is_open(self) -> bool:
        return self.exit_ts is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SimLedger:
    """Accumulates funnel counts + realized trades across a replay run.

    All mutation goes through the ``record_*`` methods so the funnel
    accounting stays the single source of truth. ``summary()`` is the
    portfolio + per-strategy + funnel roll-up written to ``summary.json``.
    """

    def __init__(self) -> None:
        # funnel[strategy][stage] = count
        self._funnel: dict[str, dict[str, int]] = {}
        self._trades: list[SimTrade] = []

    # -- funnel accounting --------------------------------------------------
    def record_stage(self, strategy: str, stage: FunnelStage, n: int = 1) -> None:
        s = self._funnel.setdefault(
            strategy, {st.value: 0 for st in FunnelStage}
        )
        s[stage.value] += n

    # -- trades -------------------------------------------------------------
    def open_trade(self, trade: SimTrade) -> SimTrade:
        self._trades.append(trade)
        return trade

    @property
    def trades(self) -> list[SimTrade]:
        return self._trades

    def open_positions(self) -> list[SimTrade]:
        return [t for t in self._trades if t.is_open()]

    # -- roll-ups -----------------------------------------------------------
    def funnel(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in self._funnel.items()}

    def _per_strategy_stats(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in self._trades:
            if t.is_open() or t.r_multiple is None:
                continue
            s = out.setdefault(
                t.strategy,
                {"trades": 0, "wins": 0, "net_r": 0.0},
            )
            s["trades"] += 1
            s["net_r"] += t.r_multiple
            if t.r_multiple > 0:
                s["wins"] += 1
        for s in out.values():
            n = s["trades"]
            s["win_rate"] = round(s["wins"] / n, 4) if n else 0.0
            s["expectancy_r"] = round(s["net_r"] / n, 4) if n else 0.0
            s["net_r"] = round(s["net_r"], 4)
        return out

    def equity_curve_r(self) -> list[dict[str, Any]]:
        """Cumulative net-R equity curve over closed trades, in close order."""
        closed = sorted(
            (t for t in self._trades if not t.is_open() and t.r_multiple is not None),
            key=lambda t: (t.exit_ts or "", t.entry_ts),
        )
        cum = 0.0
        curve = []
        for t in closed:
            cum += t.r_multiple
            curve.append({"t": t.exit_ts, "cum_r": round(cum, 4)})
        return curve

    @staticmethod
    def _max_drawdown_r(curve: list[dict[str, Any]]) -> float:
        peak = 0.0
        maxdd = 0.0
        for pt in curve:
            peak = max(peak, pt["cum_r"])
            maxdd = max(maxdd, peak - pt["cum_r"])
        return round(maxdd, 4)

    def summary(self) -> dict[str, Any]:
        closed = [t for t in self._trades if not t.is_open() and t.r_multiple is not None]
        net_r = round(sum(t.r_multiple for t in closed), 4)
        wins = sum(1 for t in closed if t.r_multiple > 0)
        n = len(closed)
        curve = self.equity_curve_r()
        out: dict[str, Any] = {
            "portfolio": {
                "closed_trades": n,
                "open_trades": len(self.open_positions()),
                "wins": wins,
                "win_rate": round(wins / n, 4) if n else 0.0,
                "net_r": net_r,
                "expectancy_r": round(net_r / n, 4) if n else 0.0,
                "max_drawdown_r": self._max_drawdown_r(curve),
            },
            "per_strategy": self._per_strategy_stats(),
            "funnel": self.funnel(),
            "equity_curve_r": curve,
        }
        # Phase 2: with-model vs without-model portfolio diff, only when a
        # model scorer ran (model_factor populated on the trades).
        scored = [t for t in closed if t.model_factor is not None]
        if scored:
            out["models_in_loop"] = self._model_diff(scored, baseline_net_r=net_r)
        return out

    @staticmethod
    def _model_diff(scored: list[SimTrade], *, baseline_net_r: float) -> dict[str, Any]:
        """With-model vs without-model portfolio comparison (Phase 2).

        ``r_multiple`` is the WITHOUT-model R; ``r_multiple_model`` =
        r_multiple * model_factor is the WITH-model R. The diff is the realized
        effect of letting the model(s) resize orders over this history.
        """
        with_r = round(sum(t.r_multiple_model for t in scored), 4)
        without_r = round(sum(t.r_multiple for t in scored), 4)
        downsized = [t for t in scored if t.model_factor < 1.0]
        # On how many of the downsized trades did the model HELP (cut a loser)
        # vs HURT (cut a winner)?
        cut_losers = sum(1 for t in downsized if t.r_multiple < 0)
        cut_winners = sum(1 for t in downsized if t.r_multiple > 0)
        return {
            "scored_trades": len(scored),
            "downsized_trades": len(downsized),
            "net_r_without_model": without_r,
            "net_r_with_model": with_r,
            "delta_r": round(with_r - without_r, 4),
            "downsize_cut_losers": cut_losers,   # model shrank a losing trade (good)
            "downsize_cut_winners": cut_winners,  # model shrank a winning trade (bad)
        }
