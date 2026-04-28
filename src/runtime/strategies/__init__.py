"""
Pure signal-builder factories for the runtime pipeline.

Each module here exposes a callable that takes a *settings* dict and a
*candles* DataFrame and returns a signal dict of the shape::

    {"symbol": str, "side": "buy" | "sell" | "none", "qty": float, "meta": dict}

Builders are intentionally **pure**: they do not fetch market data, place
orders, or write to the database. The runtime pipeline (``src/runtime/pipeline.py``)
is the only component that wires builders to live exchange/data sources.

Registering a builder in ``pipeline.py``'s ``_STRATEGY_BUILDERS`` is its
own checkpoint and is intentionally **not** done in this module. See
``docs/claude/checkpoints/CHECKPOINT_LOG.md`` for the M7 Phase 2 plan.
"""
