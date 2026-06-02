"""Async WebSocket client for Tradovate.

Frame protocol (SockJS-style):

- ``o``             → open
- ``h``             → heartbeat (server sends; we echo to keep alive)
- ``a[json,...]``   → array of JSON messages
- ``c[code,msg]``   → close

Application request frames look like::

    <topic>\n<requestId>\n<query-string>\n<json-body>

After connecting we send an ``authorize`` frame with the access token,
then subscribe to whatever topics the caller requests. On disconnect
we reconnect with exponential backoff and re-issue every subscription.

Surfaced events flow through an ``EventBus`` (``quotes``, ``orders``,
``fills``, ``positions``, ``raw``) and a ``Recorder`` if attached.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Callable

try:
    import websockets
    from websockets import WebSocketClientProtocol
except ImportError:  # pragma: no cover — soft dep
    websockets = None  # type: ignore[assignment]
    WebSocketClientProtocol = Any  # type: ignore[misc,assignment]

from .auth import TradovateAuth
from .config import TradovateConfig
from .endpoints import WS
from .event_bus import EventBus
from .exceptions import TradovateConnectionError
from .logging_utils import get_logger
from .models import Quote
from .recorder import Recorder
from .retry import exponential_backoff

_log = get_logger(__name__)


class TradovateWebSocket:
    def __init__(
        self,
        config: TradovateConfig,
        auth: TradovateAuth,
        bus: EventBus | None = None,
        recorder: Recorder | None = None,
        use_market_data_endpoint: bool = False,
    ):
        if websockets is None:
            raise TradovateConnectionError(
                "the `websockets` package is required; install with `pip install websockets`"
            )
        self._cfg = config
        self._auth = auth
        self._bus = bus or EventBus()
        self._recorder = recorder
        self._url = (
            config.urls.ws_market_data if use_market_data_endpoint else config.urls.ws_trading
        )

        self._ws: WebSocketClientProtocol | None = None
        self._req_id = 0
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._quote_handlers: dict[str, list[Callable[[Quote], None]]] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()

    # Lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="tradovate-ws")
        await self._connected.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # Public API ---------------------------------------------------

    def subscribe_quote(self, symbol: str, on_quote: Callable[[Quote], None]) -> None:
        """Register a quote handler and (re)issue the subscription.

        Safe to call before ``start()`` — the subscription is replayed
        whenever the socket connects.
        """
        sym = symbol.upper()
        self._quote_handlers.setdefault(sym, []).append(on_quote)
        self._subscriptions[f"quote:{sym}"] = {
            "topic": WS.md_subscribe_quote,
            "body": {"symbol": sym},
        }
        if self._ws is not None:
            asyncio.create_task(self._send_subscription(f"quote:{sym}"))

    # Internal loop -----------------------------------------------

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            try:
                await self._connect_and_pump()
                attempt = 0  # reset after a clean session
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reconnect on anything
                _log.warning("ws disconnected; will reconnect",
                             extra={"err": str(e), "attempt": attempt})
                self._connected.clear()
                await asyncio.sleep(
                    exponential_backoff(attempt, cap_s=self._cfg.ws_max_backoff_s)
                )

    async def _connect_and_pump(self) -> None:
        assert websockets is not None
        async with websockets.connect(
            self._url, ping_interval=None, close_timeout=2
        ) as ws:
            self._ws = ws
            _log.info("ws connecting", extra={"url": self._url})

            # Wait for the opening frame.
            opening = await asyncio.wait_for(ws.recv(), timeout=10)
            if not str(opening).startswith("o"):
                raise TradovateConnectionError(f"unexpected ws opening frame: {opening!r}")

            await self._send_frame(WS.authorize, body=self._auth.get_access_token(), body_is_text=True)
            self._connected.set()
            _log.info("ws connected")

            for key in list(self._subscriptions.keys()):
                await self._send_subscription(key)

            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                async for raw in ws:
                    await self._on_frame(raw)
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(Exception):
                    await heartbeat_task
                self._ws = None
                self._connected.clear()

    async def _heartbeat_loop(self) -> None:
        # Tradovate expects an empty "[]" frame approximately every 2.5s
        while not self._stop.is_set():
            await asyncio.sleep(self._cfg.ws_heartbeat_s)
            if self._ws is None:
                return
            with contextlib.suppress(Exception):
                await self._ws.send("[]")

    async def _on_frame(self, raw) -> None:
        text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        if not text:
            return
        if text == "h":
            return
        if text.startswith("o"):
            return
        if text.startswith("c"):
            _log.warning("ws close frame", extra={"raw": text[:200]})
            return
        if text.startswith("a"):
            try:
                payload = json.loads(text[1:])
            except json.JSONDecodeError:
                return
            for msg in payload if isinstance(payload, list) else [payload]:
                await self._dispatch(msg)

    async def _dispatch(self, msg: Any) -> None:
        if self._recorder is not None:
            self._recorder.record("ws_in", msg)
        self._bus.publish("raw", msg)
        if not isinstance(msg, dict):
            return
        event = msg.get("e") or msg.get("event")
        if event == "md":
            await self._dispatch_md(msg)
        elif event == "props":
            self._bus.publish("user_props", msg)
        elif event == "shutdown":
            self._bus.publish("shutdown", msg)
        elif msg.get("s") == 200 and "d" in msg:
            self._bus.publish("response", msg)

    async def _dispatch_md(self, msg: dict) -> None:
        body = msg.get("d", {})
        quotes = body.get("quotes") or [body] if isinstance(body, dict) else []
        for q in quotes:
            contract_id = q.get("contractId") or q.get("id") or 0
            symbol_guess = q.get("symbol") or _symbol_for_contract(
                contract_id, self._subscriptions
            )
            quote = Quote.from_md_frame(contract_id, q)
            self._bus.publish("quote", quote)
            if symbol_guess:
                for h in self._quote_handlers.get(symbol_guess.upper(), ()):
                    with contextlib.suppress(Exception):
                        h(quote)

    async def _send_subscription(self, key: str) -> None:
        sub = self._subscriptions[key]
        await self._send_frame(sub["topic"], body=sub["body"])

    async def _send_frame(self, topic: str, *, body: Any, body_is_text: bool = False) -> None:
        if self._ws is None:
            return
        self._req_id += 1
        body_str = body if body_is_text else json.dumps(body)
        frame = f"{topic}\n{self._req_id}\n\n{body_str}"
        if self._recorder is not None:
            self._recorder.record("ws_out", {"topic": topic, "req_id": self._req_id})
        await self._ws.send(frame)


def _symbol_for_contract(contract_id: int, subs: dict[str, dict[str, Any]]) -> str | None:
    for k, v in subs.items():
        if k.startswith("quote:") and v["body"].get("contractId") == contract_id:
            return k.split(":", 1)[1]
    return None
