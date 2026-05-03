"""DXtrade client — Velotrade integration infrastructure.

This module owns the *shape* of the Velotrade DXtrade integration. The
class signature, error vocabulary, and routing contract are real; the
HTTP/SDK-level method bodies stay as ``NotImplementedError`` until the
operator drops the DXtrade API contract (endpoints, auth flow, request
schemas, error codes). When that happens, only the four method bodies
in :class:`DXtradeClient` need filling in — the rest of the codebase
(integrator, executor, coordinator, account loader, accounts_status)
already routes through this surface.

Why a dedicated file rather than inlining into ``integrator.py``:

- Mirrors the per-exchange separation we already use for Bybit
  (``pybit.unified_trading.HTTP``) and Binance
  (``src.exchange.binance_connector.BinanceConnector``). The
  accounts unit owns the shape; the SDK detail is hidden behind the
  client class.
- Lets tests construct a ``DXtradeClient`` without hitting the live
  endpoint — the constructor validates inputs synchronously and
  every method raises a deterministic, typed exception until the
  contract lands.
- Centralises the ``MissingCredentialsError`` vocabulary so the
  executor branch and the diagnostic ping speak the same language as
  the loader's "not configured" account state.

Hard rules respected (per CLAUDE.md and the phase-2 sprint prompt):

- No fake endpoints. Methods that would hit the network raise
  ``NotImplementedError`` with a precise pointer to where the
  contract should land.
- No new dependencies — pure standard library imports.
- No live-mode flag flips. The class only matters when an account is
  routed live, which already requires a valid client to be injected
  via the existing per-account credential path.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MissingCredentialsError(RuntimeError):
    """Raised when an action requires API credentials that aren't set.

    Surfaces a uniform vocabulary for the "account is loaded into the
    accounts unit but is missing its environment-variable creds" case
    that the operator wants the system to handle gracefully:

    - The account still appears in ``/accounts_status``
      (``configured=False``) so the operator can see it exists.
    - Any code path that requires the credentials (live placement,
      balance fetch, exchange-side modify/close) refuses to proceed
      and emits a diagnostic ping naming the missing env var.

    The message **must not** include the env var's *value* — only its
    *name* (per the no-secrets rule in
    ``src/runtime/execution_diagnostics.py``).
    """


class DXtradeClient:
    """Minimal DXtrade client surface — Velotrade integration infrastructure.

    The four methods (:meth:`place`, :meth:`cancel`, :meth:`status`,
    :meth:`balance`) define the contract the executor and coordinator
    expect. Their bodies stay as ``NotImplementedError`` until the
    operator drops the DXtrade API contract. The constructor is real:
    it validates that creds are present so the executor path raises
    :class:`MissingCredentialsError` *before* the order leaves the
    process.

    Parameters
    ----------
    api_key : str
        Operator's Velotrade DXtrade API key. Must be a non-empty
        string — empty values raise :class:`MissingCredentialsError`.
    api_secret : str
        Matching API secret. Same non-empty constraint as ``api_key``.
    base_url : str, optional
        DXtrade base URL (sandbox vs prod). Defaults to None — set at
        the factory layer once the contract document specifies the
        canonical URLs. Stored for the eventual SDK call site.
    timeout : float, optional
        Per-request timeout in seconds. Default 10.0. Stored for the
        eventual SDK call site; not used until the methods land.

    Raises
    ------
    MissingCredentialsError
        When ``api_key`` or ``api_secret`` is falsy.
    """

    _CONTRACT_PENDING_MSG = (
        "DXtrade SDK contract pending — operator to provide endpoints, "
        "auth flow, and request/response schemas. Once dropped, fill in "
        "the body of this method in src/units/accounts/dxtrade_client.py "
        "and remove this NotImplementedError."
    )

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise MissingCredentialsError(
                "DXtradeClient: api_key is empty — Velotrade account is "
                "not fully configured."
            )
        if not api_secret:
            raise MissingCredentialsError(
                "DXtradeClient: api_secret is empty — Velotrade account "
                "is not fully configured."
            )
        self._api_key = str(api_key)
        self._api_secret = str(api_secret)
        self.base_url = base_url
        self.timeout = float(timeout)

    # ------------------------------------------------------------------
    # SDK surface — bodies pending the DXtrade contract.
    # ------------------------------------------------------------------
    # Signatures mirror the bybit branch in
    # ``src.units.accounts.execute._submit_order`` so the executor can
    # speak to either client through the same shape (place returns a
    # response dict; the executor reads a ``retCode``-style status and
    # an order id).

    def place(self, order: dict) -> dict:
        """Place a single order through the DXtrade SDK.

        Expected return shape (operator to confirm against the contract):
        ``{"retCode": 0, "result": {"orderId": "<id>"}, "retMsg": "OK"}``
        on success; ``{"retCode": <non-zero>, "retMsg": "<reason>"}`` on
        rejection. The executor branch in ``_submit_order`` already
        reads this shape — see the bybit branch as the reference.
        """
        raise NotImplementedError(self._CONTRACT_PENDING_MSG)

    def cancel(self, order_id: str) -> dict:
        """Cancel an open order by id."""
        raise NotImplementedError(self._CONTRACT_PENDING_MSG)

    def status(self, order_id: str) -> dict:
        """Fetch the current status of an order by id."""
        raise NotImplementedError(self._CONTRACT_PENDING_MSG)

    def balance(self) -> dict:
        """Fetch the account's current balance / equity snapshot."""
        raise NotImplementedError(self._CONTRACT_PENDING_MSG)

    # ------------------------------------------------------------------
    # Diagnostics — usable today.
    # ------------------------------------------------------------------

    def fingerprint(self) -> str:
        """Return the last 4 chars of the API key for /accounts_status.

        Mirrors the existing ``api_key_fingerprint`` field so the
        operator can verify at a glance which Velotrade key is wired
        without ever logging the full secret.
        """
        return self._api_key[-4:] if len(self._api_key) >= 4 else ""

    def __repr__(self) -> str:  # pragma: no cover — repr is debug-only
        url = self.base_url or "<no base_url>"
        return f"<DXtradeClient base_url={url!r} key=…{self.fingerprint()}>"
