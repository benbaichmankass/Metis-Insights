"""S-015 sprint package: backtest harness, data sources, parameter search.

All modules in this package are pure-function and do **not** import from
``src/runtime/orders.py`` or any live-trading code path. The harness
runs strategies against historical OHLCV via adapters in
``data_sources.py`` and computes deterministic metrics — never sending
orders, never reading runtime credentials.
"""
