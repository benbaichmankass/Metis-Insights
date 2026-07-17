"""Net-of-cost R label pipeline (M24 Phase 1).

Design of record: ``docs/research/M24-net-r-cost-aware-DESIGN.md``. Slice B
(``src/runtime/broker_cost_attribution.py``, PR #6780) just landed the clean
per-trade cost columns on the close path — broker-truth ``fee_taker_usd`` /
``fee_maker_usd`` / ``funding_paid_usd`` with a ``cost_source`` label
(``'broker'`` for cleanly-attributed trades, ``'estimate'`` for the fixed-model
Slice-A fallback). This module joins those columns to each resolved closed
trade to emit the first true **net-of-cost R** label:

```
net_pnl_usd = gross_pnl_usd − fee_taker_usd − fee_maker_usd − funding_paid_usd   (costs are POSITIVE, per Slice B)
net_R       = net_pnl_usd / risk_usd_at_entry
```

``risk_usd_at_entry`` is the same SL-distance denominator the ``/performance``
R-metrics use: ``abs(entry − stop) × qty × contract_value`` — **null (row
excluded from R) when the risk basis is unknown, never a raw-pnl fallback**
(the existing honest-coverage rule). ``cost_source`` rides along as a
label-quality flag so a downstream consumer can weight ``broker`` rows above
``estimate`` and never mistake a mostly-``estimate`` cell for broker-truth.

This module is **pure** (no DB / no I/O, stdlib only) and **observability-only**
— it computes labels + a coverage report; nothing here routes or sizes a live
order. The sweep driver that reads ``trade_journal.db`` and applies it lives
elsewhere.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

# Reuse the Slice-B symbol folder so cell keys fold ccxt (``BTC/USDT:USDT``) and
# plain (``BTCUSDT``) forms of the same instrument together.
from src.runtime.broker_cost_attribution import normalize_symbol

# cost_source values that mean a cost was actually attributed (broker-truth or
# the fixed-model estimate). Anything else (null / absent / unknown string) →
# the row is treated as UN-costed (costs default to 0 but the label is honest
# that no cost was applied).
_COSTED_SOURCES = ("broker", "estimate")


def _num(value: Any) -> Optional[float]:
    """Coerce to float, or None when missing / unparseable (never fabricate 0)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _cost(value: Any) -> float:
    """A cost column: a real number or 0.0 when the column is missing/unparseable."""
    f = _num(value)
    return f if f is not None else 0.0


def _contract_value(trade: Mapping) -> float:
    """Per-unit USD contract multiplier: the row's value if present, else 1.0."""
    for key in ("contract_value_usd", "contract_value"):
        f = _num(trade.get(key))
        if f is not None and f > 0:
            return f
    return 1.0


def risk_usd_for_trade(trade: Mapping) -> Optional[float]:
    """``abs(entry − stop) × qty × contract_value`` or None if the basis is missing.

    Returns None when entry / stop / qty are absent or unparseable, or when the
    resulting risk is <= 0 — never a fabricated risk basis.
    """
    entry = _num(trade.get("entry_price"))
    stop = _num(trade.get("stop_loss"))
    qty = _num(trade.get("position_size"))
    if entry is None or stop is None or qty is None:
        return None
    risk = abs(entry - stop) * abs(qty) * _contract_value(trade)
    if risk <= 0:
        return None
    return risk


def _trade_id(trade: Mapping) -> Any:
    tid = trade.get("trade_id")
    if tid is None:
        tid = trade.get("id")
    return tid


def net_r_for_trade(trade: Mapping) -> Optional[dict]:
    """Compute the net-of-cost R label for one resolved closed-trade row.

    ``trade`` fields consulted: ``gross_pnl`` (fallback ``pnl``),
    ``fee_taker_usd``, ``fee_maker_usd``, ``funding_paid_usd``, ``cost_source``,
    ``entry_price``, ``stop_loss``, ``position_size``, and
    ``contract_value_usd`` / ``contract_value`` (else 1.0). ``trade_id`` (or
    ``id``) identifies the row.

    Returns ``{trade_id, net_pnl_usd, net_R, cost_source, risk_usd, costed}`` or
    **None** when net_R is uncomputable — either the risk basis is missing
    (``risk_usd_for_trade`` is None) or the gross pnl is missing (an unresolved
    trade). ``costed`` is True only when ``cost_source ∈ {broker, estimate}``;
    a null/absent source means no cost was attributed (costs count as 0 and
    ``costed`` is False), never silently costed.
    """
    risk_usd = risk_usd_for_trade(trade)
    if risk_usd is None:
        return None

    gross = _num(trade.get("gross_pnl"))
    if gross is None:
        gross = _num(trade.get("pnl"))
    if gross is None:
        # No resolved pnl → net_R uncomputable (never treat a missing pnl as 0).
        return None

    raw_source = trade.get("cost_source")
    source = str(raw_source).strip().lower() if raw_source is not None else None
    costed = source in _COSTED_SOURCES

    fee_taker = _cost(trade.get("fee_taker_usd"))
    fee_maker = _cost(trade.get("fee_maker_usd"))
    funding = _cost(trade.get("funding_paid_usd"))

    net_pnl = gross - fee_taker - fee_maker - funding
    net_r = net_pnl / risk_usd

    return {
        "trade_id": _trade_id(trade),
        "net_pnl_usd": round(net_pnl, 8),
        "net_R": round(net_r, 8),
        "cost_source": source if costed else None,
        "risk_usd": round(risk_usd, 8),
        "costed": costed,
    }


def _empty_cell() -> dict:
    return {
        "total": 0,
        "broker_costed": 0,
        "estimate_costed": 0,
        "uncosted": 0,
        "r_uncomputable": 0,
    }


def net_r_coverage(trades: Iterable[Mapping]) -> dict:
    """Coverage report over a set of closed-trade rows (JSON-serializable).

    Partitions every trade into exactly one bucket:

    * ``broker_costed``   — net_R computable AND ``cost_source == 'broker'``
    * ``estimate_costed`` — net_R computable AND ``cost_source == 'estimate'``
    * ``uncosted``        — net_R computable but no cost was attributed
    * ``r_uncomputable``  — net_R is None (missing risk basis or gross pnl)

    Returned overall AND broken down per ``(strategy, symbol)`` cell (symbol
    folded via ``normalize_symbol``). ``by_cell`` is a list of cell dicts sorted
    by ``(strategy, symbol)`` for deterministic output.
    """
    overall = _empty_cell()
    cells: dict[tuple[str, str], dict] = {}

    for trade in trades:
        strategy = trade.get("strategy")
        strategy_key = str(strategy) if strategy is not None else ""
        symbol_key = normalize_symbol(trade.get("symbol"))
        cell = cells.setdefault((strategy_key, symbol_key), _empty_cell())

        result = net_r_for_trade(trade)
        if result is None:
            bucket = "r_uncomputable"
        elif result["cost_source"] == "broker":
            bucket = "broker_costed"
        elif result["cost_source"] == "estimate":
            bucket = "estimate_costed"
        else:
            bucket = "uncosted"

        for target in (overall, cell):
            target["total"] += 1
            target[bucket] += 1

    by_cell = [
        {"strategy": strat, "symbol": sym, **counts}
        for (strat, sym), counts in sorted(cells.items())
    ]

    report = dict(overall)
    report["by_cell"] = by_cell
    return report
