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
    """Queue a backtest run for *strategy* via the Colab/VM polling mechanism.

    Appends a JSON line to ``BACKTEST_QUEUE_PATH`` (default
    ``/tmp/backtest-queue.json``).  A VM cron job or Colab cell polls this file
    and executes the backtest.

    Parameters
    ----------
    strategy : str
        Strategy name to backtest.
    config : dict, optional
        Override payload fields.  Recognised keys: ``symbol``, ``timeframe``,
        ``start_date``, ``end_date``.

    Returns
    -------
    dict
        ``{queued: True, strategy: str, queue_path: str, payload: dict}``

    Raises
    ------
    OSError
        If the queue file cannot be written.
    """
    import json as _json
    import os as _os
    from datetime import datetime as _dt, timezone as _tz

    queue_path = _os.environ.get("BACKTEST_QUEUE_PATH", "/tmp/backtest-queue.json")

    payload: Dict[str, Any] = {
        "strategy": strategy,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "start_date": "2026-01-01",
        "end_date": None,
        "queued_at": _dt.now(_tz.utc).isoformat(),
        "vm_user": _os.environ.get("VM_USER", "ubuntu"),
        "vm_host": _os.environ.get("VM_HOST", ""),
        "repo_dir": _os.environ.get("REPO_DIR", "/home/ubuntu/ict-trading-bot"),
        "ssh_key": _os.environ.get("SSH_KEY_FILE", "ict-bot-ovm-private.key"),
    }
    if config:
        payload.update(config)

    with open(queue_path, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(payload) + "\n")

    return {"queued": True, "strategy": strategy, "queue_path": queue_path, "payload": payload}
