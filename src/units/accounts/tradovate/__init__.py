"""Tradovate broker integration (demo-first, live-promotable).

A self-contained Python package for talking to Tradovate's REST and
WebSocket APIs from a Linux VM. Demo is the default environment;
switching to live is a config change (``TRADOVATE_ENV=live``) and does
not require code edits.

This package is intentionally NOT yet wired into the ICT bot's order
intent layer or risk counters. It exposes a broker-agnostic adapter
(``adapter.TradovateAdapter``) so the bot's runtime can later plug it
in alongside ``ib_client`` / ``dxtrade_client`` without touching this
module.

Layout
------
- ``config``                 — env-driven settings; demo by default
- ``endpoints``              — single source of truth for every REST
                               path and WS topic (uncertain names are
                               flagged here so corrections are local)
- ``exceptions``             — typed error vocabulary
- ``auth``                   — token acquisition + refresh
- ``rest_client``            — thin HTTP wrapper with retry/backoff
- ``websocket_client``       — SockJS-style frame parser + reconnect
- ``models``                 — pydantic models for the trading domain
- ``account_service``        — list accounts, pick a sim account
- ``market_data_service``    — quote subscriptions over WS
- ``order_service``          — place/cancel/modify + idempotency
- ``position_service``       — read positions
- ``risk_manager``           — pre-trade guardrails (whitelist, caps)
- ``retry``                  — exponential backoff helper
- ``logging_utils``          — secret-safe structured logger
- ``recorder``               — NDJSON event recorder for debugging
- ``event_bus``              — in-process pub/sub for order/fill events
- ``adapter``                — broker-agnostic adapter face
"""
from __future__ import annotations

from .config import TradovateConfig, TradovateEnv
from .exceptions import (
    TradovateError,
    TradovateAuthError,
    TradovateAPIError,
    TradovateRiskRejection,
    TradovateConfigError,
    TradovateConnectionError,
)

__all__ = [
    "TradovateConfig",
    "TradovateEnv",
    "TradovateError",
    "TradovateAuthError",
    "TradovateAPIError",
    "TradovateRiskRejection",
    "TradovateConfigError",
    "TradovateConnectionError",
]
