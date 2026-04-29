"""Trading School — strategy validator (S-008 PR #125).

Validates live performance metrics against acceptable thresholds before
allowing a strategy update to be applied.  Called by the Coordinator;
no unit calls this directly.

Backtest triggering is stubbed here and will be wired to the actual
Colab/HF backtest pipeline in a later PR.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# Default thresholds applied when units.yaml provides no overrides.
_DEFAULTS: Dict[str, Any] = {
    "min_win_rate": 0.40,          # 40 % minimum win rate
    "min_profit_factor": 1.0,      # break-even or better
    "max_drawdown_pct": 0.30,      # 30 % max drawdown
    "min_trades": 5,               # need at least 5 trades to evaluate
}


def validate_metrics(
    strategy: str,
    metrics: Dict[str, Any],
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Check *metrics* against *thresholds* for *strategy*.

    Parameters
    ----------
    strategy : str
        Strategy name (used in the returned verdict for traceability).
    metrics : dict
        Observed performance metrics.  Recognised keys:
          ``win_rate``        : float 0-1
          ``profit_factor``   : float ≥ 0
          ``drawdown_pct``    : float 0-1 (fraction, not percentage)
          ``trade_count``     : int
    thresholds : dict, optional
        Override default thresholds.  Missing keys fall back to _DEFAULTS.

    Returns
    -------
    dict
        ``{ok: bool, strategy: str, metrics: dict, issues: list[str]}``
    """
    th = {**_DEFAULTS, **(thresholds or {})}
    issues: List[str] = []

    trade_count = metrics.get("trade_count", 0)
    if trade_count < th["min_trades"]:
        issues.append(
            f"Insufficient trades: {trade_count} < {th['min_trades']} required"
        )

    win_rate = metrics.get("win_rate")
    if win_rate is not None and win_rate < th["min_win_rate"]:
        issues.append(
            f"Win rate {win_rate:.1%} below minimum {th['min_win_rate']:.1%}"
        )

    pf = metrics.get("profit_factor")
    if pf is not None and pf < th["min_profit_factor"]:
        issues.append(
            f"Profit factor {pf:.2f} below minimum {th['min_profit_factor']:.2f}"
        )

    dd = metrics.get("drawdown_pct")
    if dd is not None and dd > th["max_drawdown_pct"]:
        issues.append(
            f"Drawdown {dd:.1%} exceeds maximum {th['max_drawdown_pct']:.1%}"
        )

    return {
        "ok": len(issues) == 0,
        "strategy": strategy,
        "metrics": dict(metrics),
        "issues": issues,
    }


def trigger_backtest(
    strategy: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Trigger a backtest run for *strategy*.

    Stub — wired to Colab/HF pipeline in a later PR.

    Raises
    ------
    NotImplementedError
        Always; will be implemented in PR #126 (Workflows integration).
    """
    raise NotImplementedError(
        f"trigger_backtest('{strategy}') not yet wired to Colab pipeline; "
        "implement in PR #126."
    )
