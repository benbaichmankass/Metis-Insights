"""Token acquisition + refresh for Tradovate.

Holds two tokens (``accessToken`` for trading + ``mdAccessToken`` for
market data) and the ``expirationTime`` the server returns. Refresh
fires when the remaining lifetime drops below ``REFRESH_MARGIN_S``.

The auth client is purely synchronous and uses ``httpx`` so it can be
shared by the REST client and the WS bootstrap. Both the REST and WS
layers fetch tokens through this object, so a refresh inside one is
visible to the other.

Never logs the password, secret, or full tokens — see
``logging_utils._scrub``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import TradovateConfig
from .endpoints import REST
from .exceptions import TradovateAuthError
from .logging_utils import get_logger

_log = get_logger(__name__)

# Refresh when fewer than this many seconds remain on the token.
REFRESH_MARGIN_S = 600.0  # 10 minutes


@dataclass
class TokenBundle:
    access_token: str
    md_access_token: str | None
    expires_at: datetime
    user_id: int | None = None

    def is_expiring(self, now: datetime | None = None, margin_s: float = REFRESH_MARGIN_S) -> bool:
        n = now or datetime.now(timezone.utc)
        return (self.expires_at - n).total_seconds() < margin_s


class TradovateAuth:
    """Thread-safe token holder. Call ``get_access_token()`` everywhere."""

    def __init__(self, config: TradovateConfig, http: httpx.Client | None = None):
        self._cfg = config
        self._http = http or httpx.Client(
            base_url=config.urls.rest_base,
            timeout=config.request_timeout_s,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        self._owns_http = http is None
        self._lock = threading.Lock()
        self._token: TokenBundle | None = None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def get_access_token(self) -> str:
        bundle = self._ensure_fresh()
        return bundle.access_token

    def get_md_access_token(self) -> str | None:
        bundle = self._ensure_fresh()
        return bundle.md_access_token

    def current(self) -> TokenBundle | None:
        return self._token

    def _ensure_fresh(self) -> TokenBundle:
        with self._lock:
            if self._token is None:
                self._token = self._fetch_new()
            elif self._token.is_expiring():
                try:
                    self._token = self._renew(self._token)
                except TradovateAuthError as e:
                    _log.warning("token renew failed, re-authing", extra={"err": str(e)})
                    self._token = self._fetch_new()
            return self._token

    def _fetch_new(self) -> TokenBundle:
        body = self._cfg.auth_payload()
        try:
            resp = self._http.post(REST.auth_token, json=body)
        except httpx.HTTPError as e:
            raise TradovateAuthError(f"auth request failed: {e}") from e

        data = _safe_json(resp)
        if resp.status_code != 200 or "accessToken" not in data:
            raise TradovateAuthError(
                f"auth rejected (status={resp.status_code}, "
                f"errorText={data.get('errorText') or data.get('p-ticket') or '?'})"
            )
        _log.info("auth ok", extra={"env": self._cfg.env.value, "user_id": data.get("userId")})
        return _bundle_from_payload(data)

    def _renew(self, existing: TokenBundle) -> TokenBundle:
        try:
            resp = self._http.get(
                REST.auth_renew,
                headers={"Authorization": f"Bearer {existing.access_token}"},
            )
        except httpx.HTTPError as e:
            raise TradovateAuthError(f"renew request failed: {e}") from e

        data = _safe_json(resp)
        if resp.status_code != 200 or "accessToken" not in data:
            raise TradovateAuthError(
                f"renew rejected (status={resp.status_code}): {data.get('errorText', '?')}"
            )
        _log.info("token renewed", extra={"env": self._cfg.env.value})
        return _bundle_from_payload(data)


def _bundle_from_payload(data: dict) -> TokenBundle:
    expires_raw = data.get("expirationTime")
    if isinstance(expires_raw, str):
        expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    elif isinstance(expires_raw, (int, float)):
        expires = datetime.fromtimestamp(expires_raw / 1000.0, tz=timezone.utc)
    else:
        # Tradovate tokens default to ~80 minutes; if the server didn't
        # tell us, be conservative and force a refresh in 30 minutes.
        expires = datetime.fromtimestamp(time.time() + 1800, tz=timezone.utc)

    return TokenBundle(
        access_token=data["accessToken"],
        md_access_token=data.get("mdAccessToken"),
        expires_at=expires,
        user_id=data.get("userId"),
    )


def _safe_json(resp: httpx.Response) -> dict:
    try:
        out = resp.json()
        return out if isinstance(out, dict) else {"raw": out}
    except ValueError:
        return {"raw": resp.text}
