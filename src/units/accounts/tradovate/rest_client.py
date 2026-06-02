"""Thin synchronous REST wrapper.

Every public method funnels through ``_request`` so the retry/backoff
behaviour, auth header, correlation ID, and error translation live in
one place. Methods on the service layer (``AccountService``,
``OrderService``, etc.) call this client rather than hitting ``httpx``
directly — keeps the rest of the package free of HTTP details.

Retries cover idempotent verbs (GET) on transport errors. ``POST`` is
*not* auto-retried at this layer because a duplicate ``placeorder`` is
worse than a transient 5xx; idempotency for orders is enforced by
``order_service.OrderService`` via ``client_order_id``.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .auth import TradovateAuth
from .config import TradovateConfig
from .exceptions import TradovateAPIError, TradovateConnectionError
from .logging_utils import get_logger, new_correlation_id
from .retry import exponential_backoff

_log = get_logger(__name__)


class TradovateRestClient:
    """Synchronous Tradovate REST client.

    Sharing the underlying ``httpx.Client`` with ``TradovateAuth`` would
    couple their lifetimes; we deliberately keep a second client here so
    auth retries don't share connection state with order calls.
    """

    def __init__(
        self,
        config: TradovateConfig,
        auth: TradovateAuth,
        http: httpx.Client | None = None,
        max_get_retries: int = 3,
    ):
        self._cfg = config
        self._auth = auth
        self._http = http or httpx.Client(
            base_url=config.urls.rest_base,
            timeout=config.request_timeout_s,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        self._owns_http = http is None
        self._max_get_retries = max_get_retries

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # Public verbs -------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params, retries=self._max_get_retries)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body=body, retries=0)

    # Internal -----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        retries: int = 0,
    ) -> Any:
        cid = new_correlation_id()
        last_err: Exception | None = None

        for attempt in range(1, retries + 2):  # initial + retries
            try:
                resp = self._http.request(
                    method,
                    path,
                    params=params,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._auth.get_access_token()}",
                        "X-Correlation-Id": cid,
                    },
                )
            except httpx.HTTPError as e:
                last_err = e
                _log.warning(
                    "rest transport error",
                    extra={"cid": cid, "path": path, "attempt": attempt, "err": str(e)},
                )
                if attempt > retries:
                    raise TradovateConnectionError(f"{method} {path}: {e}") from e
                time.sleep(exponential_backoff(attempt, cap_s=10.0))
                continue

            if 200 <= resp.status_code < 300:
                return _safe_json(resp)

            # 401: try one re-auth then surface
            if resp.status_code == 401 and attempt == 1:
                _log.info("rest 401, forcing token refresh", extra={"cid": cid, "path": path})
                # The auth object refreshes lazily; bump expiry to force it.
                bundle = self._auth.current()
                if bundle is not None:
                    bundle.expires_at = bundle.expires_at.fromtimestamp(0, tz=bundle.expires_at.tzinfo)
                continue

            # 5xx on a GET we'll retry; everything else we raise.
            if method == "GET" and 500 <= resp.status_code < 600 and attempt <= retries:
                _log.warning(
                    "rest 5xx, will retry",
                    extra={"cid": cid, "path": path, "status": resp.status_code, "attempt": attempt},
                )
                time.sleep(exponential_backoff(attempt, cap_s=10.0))
                continue

            payload = _safe_json(resp)
            raise TradovateAPIError(resp.status_code, payload)

        # Loop exhausted without returning — surface the last transport error.
        raise TradovateConnectionError(f"{method} {path}: exhausted retries: {last_err}")


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text
