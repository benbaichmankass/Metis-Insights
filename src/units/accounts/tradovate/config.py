"""Tradovate config — env-driven, demo by default.

The environment toggle is the *single* switch that maps demo↔live; no
other module hardcodes a host. ``TradovateConfig.load()`` reads env
once and validates that the credentials required for the current env
are present. Missing creds raise ``TradovateConfigError`` with the
*name* of the env var (never the value) so logs stay safe.

Demo is default. Asking for live with a partial cred set fails fast.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .exceptions import TradovateConfigError


class TradovateEnv(str, Enum):
    DEMO = "demo"
    LIVE = "live"

    @classmethod
    def parse(cls, raw: str | None) -> "TradovateEnv":
        if not raw:
            return cls.DEMO
        v = raw.strip().lower()
        if v not in {"demo", "live"}:
            raise TradovateConfigError(
                f"TRADOVATE_ENV must be 'demo' or 'live' (got {raw!r})"
            )
        return cls(v)


@dataclass(frozen=True)
class TradovateUrls:
    rest_base: str
    ws_trading: str
    ws_market_data: str


_DEMO_URLS = TradovateUrls(
    rest_base="https://demo.tradovateapi.com/v1",
    ws_trading="wss://demo.tradovateapi.com/v1/websocket",
    ws_market_data="wss://md-demo.tradovateapi.com/v1/websocket",
)
_LIVE_URLS = TradovateUrls(
    rest_base="https://live.tradovateapi.com/v1",
    ws_trading="wss://live.tradovateapi.com/v1/websocket",
    ws_market_data="wss://md.tradovateapi.com/v1/websocket",
)


@dataclass(frozen=True)
class TradovateConfig:
    env: TradovateEnv
    username: str
    password: str
    app_id: str
    app_version: str
    cid: str
    secret: str
    device_id: str

    # Runtime knobs (not credentials)
    dry_run: bool = True
    allowed_symbols: frozenset[str] = field(default_factory=frozenset)
    max_position_per_symbol: int = 1
    max_open_orders: int = 5
    request_timeout_s: float = 15.0
    ws_heartbeat_s: float = 2.5
    ws_max_backoff_s: float = 30.0

    @property
    def urls(self) -> TradovateUrls:
        return _DEMO_URLS if self.env is TradovateEnv.DEMO else _LIVE_URLS

    @property
    def is_demo(self) -> bool:
        return self.env is TradovateEnv.DEMO

    def auth_payload(self) -> dict:
        """Body for ``/auth/accesstokenrequest``."""
        return {
            "name": self.username,
            "password": self.password,
            "appId": self.app_id,
            "appVersion": self.app_version,
            "cid": int(self.cid) if self.cid.isdigit() else self.cid,
            "sec": self.secret,
            "deviceId": self.device_id,
        }

    @classmethod
    def load(cls, environ: dict | None = None) -> "TradovateConfig":
        env_map = environ if environ is not None else os.environ
        env = TradovateEnv.parse(env_map.get("TRADOVATE_ENV"))

        required = {
            "TRADOVATE_USERNAME": env_map.get("TRADOVATE_USERNAME"),
            "TRADOVATE_PASSWORD": env_map.get("TRADOVATE_PASSWORD"),
            "TRADOVATE_APP_ID": env_map.get("TRADOVATE_APP_ID"),
            "TRADOVATE_APP_VERSION": env_map.get("TRADOVATE_APP_VERSION"),
            "TRADOVATE_CID": env_map.get("TRADOVATE_CID"),
            "TRADOVATE_SECRET": env_map.get("TRADOVATE_SECRET"),
            "TRADOVATE_DEVICE_ID": env_map.get("TRADOVATE_DEVICE_ID"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise TradovateConfigError(
                f"Missing required env vars for TRADOVATE_ENV={env.value}: "
                f"{', '.join(sorted(missing))}"
            )

        return cls(
            env=env,
            username=required["TRADOVATE_USERNAME"],
            password=required["TRADOVATE_PASSWORD"],
            app_id=required["TRADOVATE_APP_ID"],
            app_version=required["TRADOVATE_APP_VERSION"],
            cid=required["TRADOVATE_CID"],
            secret=required["TRADOVATE_SECRET"],
            device_id=required["TRADOVATE_DEVICE_ID"],
            dry_run=_truthy(env_map.get("TRADOVATE_DRY_RUN"), default=True),
            allowed_symbols=frozenset(_split_csv(env_map.get("TRADOVATE_ALLOWED_SYMBOLS"))),
            max_position_per_symbol=int(env_map.get("TRADOVATE_MAX_POS_PER_SYMBOL", "1")),
            max_open_orders=int(env_map.get("TRADOVATE_MAX_OPEN_ORDERS", "5")),
            request_timeout_s=float(env_map.get("TRADOVATE_REQUEST_TIMEOUT_S", "15")),
        )


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str | None) -> Iterable[str]:
    if not raw:
        return ()
    return tuple(p.strip().upper() for p in raw.split(",") if p.strip())
