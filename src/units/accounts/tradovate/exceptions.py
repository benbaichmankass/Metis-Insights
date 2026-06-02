"""Typed exception hierarchy for the Tradovate integration.

Every public surface raises one of these — never a bare ``RuntimeError``
— so callers (and the risk layer) can branch on cause without parsing
error strings.
"""
from __future__ import annotations


class TradovateError(Exception):
    """Base class for all Tradovate integration errors."""


class TradovateConfigError(TradovateError):
    """Raised when the environment/config is invalid or missing creds."""


class TradovateAuthError(TradovateError):
    """Raised when auth fails or a token cannot be refreshed.

    Distinct from ``TradovateAPIError`` because callers may want to
    trigger a re-login rather than back off on the same call.
    """


class TradovateAPIError(TradovateError):
    """Raised when the REST API returns a non-2xx response.

    ``status`` is the HTTP status; ``payload`` is the parsed JSON body
    (or raw text if not JSON). Endpoints that legitimately return 4xx
    in normal flow should be caught and translated to a domain result
    by the service layer rather than bubbling this up.
    """

    def __init__(self, status: int, payload, message: str | None = None):
        self.status = status
        self.payload = payload
        super().__init__(message or f"Tradovate API {status}: {payload!r}")


class TradovateConnectionError(TradovateError):
    """Raised on transport-level failures (DNS, TCP, WS handshake)."""


class TradovateRiskRejection(TradovateError):
    """Raised by ``RiskManager`` when an order violates a guardrail.

    Always carries a machine-readable ``reason`` code so the order
    service can log it without leaking the rejected payload.
    """

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"risk_rejected:{reason}" + (f" ({detail})" if detail else ""))
