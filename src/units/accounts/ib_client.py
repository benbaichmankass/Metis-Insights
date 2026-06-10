"""Interactive Brokers client — TWS API integration via ib_insync.

This module owns the *shape* of the IB integration for MES (Micro
E-mini S&P 500) futures. It mirrors the per-exchange separation already
used for Bybit (``pybit.unified_trading.HTTP``), Binance
(``src.exchange.binance_connector.BinanceConnector``) and Velotrade
(``src.units.accounts.dxtrade_client.DXtradeClient``): the accounts unit
owns the connection shape; the SDK detail is hidden behind a client
class. The executor (`_submit_order`), coordinator (client-construction
switch) and account loader all route through this surface.

Why IB has no API keys
----------------------
The IB TWS API does **not** authenticate with API key/secret pairs the
way REST exchanges do. Authentication is the IB Gateway / TWS *login
session* itself — a desktop process the operator (or an automated
IBC/headless Gateway) keeps logged in. A client connects to that process
over a local socket (default ``127.0.0.1:7496`` for live, ``7497`` for
paper) and is identified only by a numeric ``clientId``. So there is no
``api_key_env`` for IB accounts: connection identity is host + port +
clientId + the IB account code (e.g. ``U25907316`` / ``DUQ325724``).

Connection model
----------------
ib_insync keeps a persistent socket to the Gateway and runs its own
asyncio event loop. Reconnecting on every tick is wasteful and risks
``clientId already in use`` rejections, so connections are cached in a
module-level registry keyed by ``(host, port, client_id)`` and reused
across calls (reconnecting only if the socket dropped). Use
:func:`get_ib_client` rather than constructing :class:`IBClient`
directly so callers share one connection per gateway endpoint.

ib_insync vs ib_async
---------------------
``ib_insync`` is the library requested by the operator and is what
``requirements.txt`` pins. The original project is no longer actively
maintained; the API-compatible community fork is ``ib_async``. The
lazy import below prefers ``ib_insync`` and transparently falls back to
``ib_async`` if only the fork is installed, so the integration keeps
working on Python versions where the original package stops importing.

Hard rules respected (per CLAUDE.md):

- The live (real-money) account is configured ``mode: dry_run`` in
  ``config/accounts.yaml`` — the coordinator never even constructs a
  client for a dry account, so no socket is opened against the live
  gateway until the operator promotes it (Tier-3). The paper account is
  ``mode: live`` so it executes against the IB *paper* gateway (paper
  money), exactly mirroring how ``bybit_1`` runs ``mode: live`` against
  Bybit's demo endpoint.
- No secret values are ever logged — only the masked account code.
- ib_insync is imported lazily inside methods so this module imports
  cleanly (and the test suite runs) without the package installed.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_IB_HOST = "127.0.0.1"

# ── IB-isolation guard rails (restart-loop incident, 2026-06-05) ────────────
# A logged-out IB Gateway ACCEPTS the TCP socket (so ib.connect() returns
# within its own timeout) but then never answers a single request —
# accountSummary / portfolio / reqHistoricalData / status all hang with no
# timeout. Because every IB method funnels through IBClient.connect(), and the
# trader's main loop runs those synchronously, one wedged gateway used to hang
# the WHOLE pipeline tick (incl. Bybit/BTCUSDT) AND starve the liveness
# heartbeat → the watchdog autohealed in a perpetual restart loop. These bounds
# keep IB fully isolated: connect() verifies the session with a hard-bounded
# liveness probe and trips a per-endpoint circuit breaker so a dead gateway
# fast-fails (raising IBConnectionError, which the executor/coordinator already
# treat as "account not usable this tick") instead of blocking the loop.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Hard cap on the post-connect liveness probe (reqCurrentTime round-trip). A
# healthy gateway answers in milliseconds; a logged-out one never answers, so
# this is the bound that converts "hang forever" into "fail in N seconds".
_IB_PROBE_TIMEOUT_S = _env_float("IB_PROBE_TIMEOUT_S", 5.0)
# How long connect() fast-fails after a probe/connect failure before retrying
# the gateway again. Long enough that a wedged gateway can't be hammered every
# tick; short enough that a genuine recovery is picked up promptly.
_IB_BREAKER_COOLDOWN_S = _env_float("IB_BREAKER_COOLDOWN_S", 120.0)

# MES (CME) trades in 0.25 index-point ticks. SL/TP prices sent to IB
# must be on the tick grid or the order is rejected.
MES_TICK_SIZE = 0.25
# MGC (Micro Gold, COMEX) trades in 0.10 ticks ($1.00/tick on 10 troy oz).
MGC_TICK_SIZE = 0.10
# MHG (Micro Copper, COMEX) trades in 0.0005 ticks ($1.25/tick on 2,500 lb).
MHG_TICK_SIZE = 0.0005

# Per-symbol tick-size lookup. SL/TP prices sent to IB must sit on the
# contract's tick grid or the order is rejected; the lookup keeps the
# rounding correct per instrument (MES / MGC / MHG all differ).
TICK_SIZES: Dict[str, float] = {
    "MES": MES_TICK_SIZE,
    "MGC": MGC_TICK_SIZE,
    "MHG": MHG_TICK_SIZE,
}


def tick_size_for(symbol: Optional[str]) -> float:
    """Return the contract tick size for *symbol* (defaults to MES)."""
    return TICK_SIZES.get(str(symbol or "MES").upper(), MES_TICK_SIZE)


class IBConnectionError(RuntimeError):
    """Raised when an IB action cannot reach a usable Gateway connection.

    Surfaces a uniform vocabulary for "the IB account is loaded but the
    Gateway is unreachable / the ib_insync package is missing / the
    connection params are incomplete." The executor branch converts it
    into a ``RuntimeError`` the coordinator's diagnostic-ping wrapper can
    format; the coordinator's client-construction guard treats it as the
    "account not usable this tick" path.

    The message must never include secret values — IB has no API
    secrets, but the account code is masked in diagnostics regardless.
    """


def _round_to_tick(price: float, tick: float = MES_TICK_SIZE) -> float:
    """Round *price* to the nearest valid *tick* increment.

    IB rejects futures orders whose price is off the contract's tick
    grid (MES = 0.25, MGC = 0.10, MHG = 0.0005). Rounds to the nearest
    tick and trims float noise to enough decimals to preserve the tick
    (e.g. MHG's 0.0005 needs 4 dp; a fixed 4-dp trim would corrupt a
    finer grid, so derive the precision from the tick itself).
    """
    if tick <= 0:
        return float(price)
    # Decimal places needed to represent the tick exactly (capped at 8).
    tick_str = f"{tick:.8f}".rstrip("0")
    ndigits = len(tick_str.split(".", 1)[1]) if "." in tick_str else 0
    return round(round(float(price) / tick) * tick, max(ndigits, 4))


class IBClient:
    """Minimal Interactive Brokers client surface for MES futures.

    The four order-path methods (:meth:`place`, :meth:`cancel`,
    :meth:`status`, :meth:`balance`) define the contract the executor and
    coordinator expect — they return Bybit-style ``retCode`` envelopes so
    the existing ``_submit_order`` dispatch reads either client through
    the same shape.

    Connections are lazy: the socket is opened on first use (or via an
    explicit :meth:`connect`) and cached for reuse. Prefer
    :func:`get_ib_client` so one connection is shared per gateway
    endpoint rather than reconnecting per call.

    Parameters
    ----------
    host : str
        Gateway host. Defaults to ``127.0.0.1``.
    port : int
        Gateway API port — ``7496`` (live) / ``7497`` (paper) by IB
        convention, but whatever the operator's Gateway listens on.
    client_id : int
        Numeric API client id. Must be unique per concurrent connection
        to the same Gateway.
    account : str, optional
        IB account code (e.g. ``U25907316`` / ``DUQ325724``). Stamped on
        every order so a multi-account Gateway login routes the fill to
        the right account. Recommended even for single-account logins.
    symbol : str
        Tradeable symbol this client builds contracts for. Only ``MES``
        is wired today; any other symbol raises ``ValueError`` from
        :meth:`_build_contract`.
    readonly : bool
        When True, connect in IB read-only mode (no order transmission).
        Used by the connection self-test so a probe never risks a fill.
    timeout : float
        Per-connect timeout in seconds (default 10.0).
    _ib_factory : callable, optional
        Test seam — returns a stand-in ``IB`` instance. When None the
        real ib_insync / ib_async ``IB`` class is imported lazily.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_IB_HOST,
        port: int,
        client_id: int,
        account: Optional[str] = None,
        symbol: str = "MES",
        readonly: bool = False,
        timeout: float = 10.0,
        _ib_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        try:
            self.port = int(port)
        except (TypeError, ValueError) as exc:
            raise IBConnectionError(
                f"IBClient: port must be an integer, got {port!r}"
            ) from exc
        try:
            self.client_id = int(client_id)
        except (TypeError, ValueError) as exc:
            raise IBConnectionError(
                f"IBClient: client_id must be an integer, got {client_id!r}"
            ) from exc
        self.host = str(host or DEFAULT_IB_HOST)
        self.account = str(account) if account else None
        self.symbol = str(symbol or "MES").upper()
        self.readonly = bool(readonly)
        self.timeout = float(timeout)
        self._ib_factory = _ib_factory
        self._ib: Any = None
        self._contract: Any = None
        self._loop: Any = None  # persistent asyncio loop the IB binds to
        # Circuit-breaker state (restart-loop incident, 2026-06-05). While
        # monotonic() < _breaker_open_until, connect() fast-fails without
        # touching the socket, so a wedged gateway can't stall the loop.
        self._breaker_open_until: float = 0.0
        self._breaker_fail_count: int = 0

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _new_ib(self) -> Any:
        """Construct a fresh ``IB`` instance (lazy import, fork-tolerant)."""
        if self._ib_factory is not None:
            return self._ib_factory()
        try:
            from ib_insync import IB  # type: ignore
        except ImportError:
            try:
                from ib_async import IB  # type: ignore
            except ImportError as exc:
                raise IBConnectionError(
                    "ib_insync is not installed — add 'ib_insync' to the "
                    "environment (pip install ib_insync) to enable the IB "
                    "execution path. The maintained fork 'ib_async' is "
                    "accepted as a drop-in fallback."
                ) from exc
        return IB()

    def connect(self) -> Any:
        """Open (or reuse) the Gateway connection and return the ``IB`` handle.

        Idempotent: returns the existing handle when already connected.
        Raises :class:`IBConnectionError` when the Gateway is unreachable
        (e.g. not running, wrong port, or the clientId is taken).
        """
        # Re-assert the loop FIRST, on every call — including the cached-return
        # path below. ib_insync resolves the loop afresh on each sync call, and
        # other code (e.g. Telegram alerts via asyncio.run) sets the thread's
        # current loop to None on exit, so a cached connection's next request
        # would otherwise raise "There is no current event loop in thread
        # 'MainThread'". _ensure_event_loop re-asserts THIS client's persistent
        # loop (the one the IB is bound to) so every downstream call resolves it.
        self._ensure_event_loop()

        # Circuit breaker: a recently-wedged gateway fast-fails here without
        # touching the socket or issuing a request, so a logged-out IB Gateway
        # can NEVER stall the trader loop (incl. Bybit) — see the module-level
        # guard-rails note. The raised IBConnectionError is the same "account
        # not usable this tick" signal the executor/coordinator already handle.
        now = time.monotonic()
        if now < self._breaker_open_until:
            raise IBConnectionError(
                f"IBClient: circuit breaker OPEN for {self.host}:{self.port} "
                f"(account={self._masked_account()}); gateway was unresponsive, "
                f"retrying in {self._breaker_open_until - now:.0f}s. Suppressing "
                "IB calls so the trader loop is not blocked."
            )

        if self._ib is not None and self._is_connected(self._ib):
            ib = self._ib
        else:
            ib = self._new_ib()
            try:
                ib.connect(
                    self.host,
                    self.port,
                    clientId=self.client_id,
                    timeout=self.timeout,
                    readonly=self.readonly,
                )
            except Exception as exc:  # noqa: BLE001 — normalise every connect failure
                self._trip_breaker()
                raise IBConnectionError(
                    f"IBClient: failed to connect to IB Gateway at "
                    f"{self.host}:{self.port} (clientId={self.client_id}, "
                    f"account={self._masked_account()}): {type(exc).__name__}: {exc}. "
                    "Is IB Gateway / TWS running with the API enabled on this port?"
                ) from exc
            self._ib = ib

        # Post-connect liveness probe. ib.connect() succeeding only proves the
        # socket was accepted — a logged-out Gateway accepts the socket but then
        # hangs every request forever. Bound a cheap reqCurrentTime round-trip
        # so a wedged gateway is caught here (and trips the breaker) instead of
        # hanging the first real request (accountSummary / portfolio / bars).
        if not self._probe_liveness(ib):
            self._trip_breaker()
            self._safe_disconnect(ib)
            raise IBConnectionError(
                f"IBClient: Gateway at {self.host}:{self.port} "
                f"(account={self._masked_account()}) accepted the socket but did "
                f"not answer a liveness probe within {_IB_PROBE_TIMEOUT_S:.0f}s "
                "(likely logged out). Tripping circuit breaker so IB calls do "
                "not block the trader loop."
            )

        # Healthy round-trip — clear any prior failure streak.
        self._breaker_fail_count = 0
        return ib

    def _trip_breaker(self) -> None:
        """Open the circuit breaker for the cooldown window."""
        self._breaker_fail_count += 1
        self._breaker_open_until = time.monotonic() + _IB_BREAKER_COOLDOWN_S
        logger.warning(
            "IBClient: circuit breaker tripped for %s:%s (account=%s, "
            "consecutive failures=%d); IB calls suppressed for %.0fs.",
            self.host, self.port, self._masked_account(),
            self._breaker_fail_count, _IB_BREAKER_COOLDOWN_S,
        )

    def _safe_disconnect(self, ib: Any) -> None:
        """Drop a dead handle so the next connect() reconnects fresh."""
        try:
            ib.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._ib = None
        self._contract = None

    def _probe_liveness(self, ib: Any) -> bool:
        """Hard-bounded liveness check on the client's persistent loop.

        Returns True when the gateway answered ``reqCurrentTime`` within
        :data:`_IB_PROBE_TIMEOUT_S`, False otherwise. Built to be safe in
        every context:

        * Stub clients (``_ib_factory`` set, i.e. the test suite) skip the
          probe — there is no real socket to verify.
        * If we don't own a usable, non-running loop (the trader's
          synchronous main thread always does), fall back to trusting
          ``isConnected()`` rather than risk breaking a healthy gateway.
        * Any unexpected error is treated as "not alive" — fail safe, the
          breaker trips, IB is isolated, the loop keeps going.
        """
        if self._ib_factory is not None:
            return True
        if _IB_PROBE_TIMEOUT_S <= 0:
            # Operator opt-out (IB_PROBE_TIMEOUT_S=0): skip the post-connect
            # liveness probe. Added 2026-06-10 for the gateway-isolation
            # topology: with the Gateway on its own VM reached over a socat
            # relay, reqCurrentTime does not resolve on the persistent loop even
            # though the connection is healthy (logs on, data farms OK,
            # synchronisation completes, and the read path works) — so the probe
            # false-trips the breaker and blocks MES. The per-fetch
            # IB_FETCH_TIMEOUT_S bound is the backstop against a genuinely wedged
            # gateway, and the gateway can no longer starve the trader's CPU
            # (separate VM), so skipping the probe is safe here. Default (5s)
            # keeps the probe ON for the same-box / loopback case.
            return True
        import asyncio

        loop = self._loop
        if loop is None or loop.is_closed():
            return True
        try:
            if loop.is_running():
                # Can't run_until_complete on a running loop; don't break it.
                return True
        except Exception:  # noqa: BLE001
            return True

        req = getattr(ib, "reqCurrentTimeAsync", None)
        if req is None:
            # Unknown IB implementation — don't assume it's dead.
            return True
        try:
            loop.run_until_complete(
                asyncio.wait_for(req(), timeout=_IB_PROBE_TIMEOUT_S)
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "IBClient: liveness probe timed out after %.0fs for %s:%s "
                "(account=%s) — gateway likely logged out.",
                _IB_PROBE_TIMEOUT_S, self.host, self.port,
                self._masked_account(),
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "IBClient: liveness probe error for %s:%s (account=%s): %s: %s",
                self.host, self.port, self._masked_account(),
                type(exc).__name__, exc,
            )
            return False

    def _ensure_event_loop(self) -> None:
        """Make this client's persistent asyncio loop the thread's current loop.

        ib_insync resolves the event loop afresh on every sync call (connect,
        qualifyContracts, reqHistoricalData, …) via
        ``asyncio.get_event_loop_policy().get_event_loop()``. On Python 3.10+ a
        synchronous worker thread has no loop by default, and — worse — any code
        in the process that runs ``asyncio.run(...)`` (e.g. Telegram alerts)
        sets the thread's current loop to None on exit. Either case makes the
        NEXT ib_insync call raise "There is no current event loop in thread
        'MainThread'".

        We keep ONE persistent loop per client — the loop the IB instance is
        built on — and re-assert it as current. Re-using the SAME loop (never a
        fresh one once the IB exists) is essential: the IB's socket transport
        lives on it, so dispatching a request on a different loop would hang
        instead of returning bars.
        """
        import asyncio
        try:
            # A loop already running on this thread is always correct to use.
            asyncio.get_running_loop()
            return
        except RuntimeError:
            pass
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    @staticmethod
    def _is_connected(ib: Any) -> bool:
        try:
            return bool(ib.isConnected())
        except Exception:  # noqa: BLE001
            return False

    @property
    def connected(self) -> bool:
        return self._ib is not None and self._is_connected(self._ib)

    def disconnect(self) -> None:
        """Close the Gateway connection if open (best-effort)."""
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("IBClient.disconnect(%s): %s", self._masked_account(), exc)
            finally:
                self._ib = None
                self._contract = None

    # ------------------------------------------------------------------
    # Contract construction
    # ------------------------------------------------------------------

    def _build_contract(self, symbol: Optional[str] = None) -> Any:
        """Resolve and cache the front-month future contract for *symbol*.

        Uses ib_insync's continuous-future lookup to find the active
        front month, then qualifies the concrete ``Future`` so it carries
        a ``conId`` IB will accept on an order. Three symbols are wired:
        ``MES`` (Micro E-mini S&P 500, CME) and the WS-A metals sleeve
        ``MGC`` (Micro Gold, COMEX) + ``MHG`` (Micro Copper, COMEX). Any
        other symbol raises ``ValueError`` (a stray BTCUSDT package must
        never reach here). The resolved contract is cached on the client.
        """
        sym = str(symbol or self.symbol or "MES").upper()
        # symbol → IB primary exchange. CME for the equity-index micro;
        # COMEX for the metals micros.
        ib_exchanges = {"MES": "CME", "MGC": "COMEX", "MHG": "COMEX"}
        ib_exchange = ib_exchanges.get(sym)
        if ib_exchange is None:
            raise ValueError(
                f"IBClient: only {sorted(ib_exchanges)} are wired for the IB "
                f"execution path; got symbol={sym!r}. Add the contract spec to "
                f"_build_contract before routing it to an IB account."
            )
        # Cache is per-symbol — guard against a stale cache from a different
        # symbol when one client is reused across instruments.
        cached = self._contract
        if cached is not None and getattr(cached, "symbol", sym) == sym:
            return cached
        ib = self.connect()
        try:
            from ib_insync import ContFuture, Future  # type: ignore
        except ImportError:
            from ib_async import ContFuture, Future  # type: ignore
        cont = ContFuture(sym, ib_exchange, currency="USD")
        ib.qualifyContracts(cont)
        con_id = getattr(cont, "conId", 0)
        if not con_id:
            raise IBConnectionError(
                f"IBClient: could not resolve the {sym} front-month contract "
                "from the Gateway (empty conId). Check market-data / "
                "contract permissions on the IB account "
                f"(COMEX metals data is needed for MGC/MHG)."
            )
        contract = Future(conId=con_id, exchange=ib_exchange)
        ib.qualifyContracts(contract)
        self._contract = contract
        return contract

    # ------------------------------------------------------------------
    # Order surface — Bybit-style retCode envelopes
    # ------------------------------------------------------------------

    def place(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Place a bracket (market entry + TP + SL) for *order*.

        Builds a native IB bracket: a market parent plus child take-profit
        (limit) and stop-loss (stop) orders linked by ``parentId`` with
        OCA semantics, all stamped with the account code. SL/TP prices are
        snapped to the MES 0.25 tick grid.

        Return shape mirrors the Bybit branch so ``_submit_order`` reads
        either client identically:
        ``{"retCode": 0, "result": {"orderId": "<id>"}, "retMsg": "OK"}``
        on success; ``{"retCode": <non-zero>, "retMsg": "<reason>"}`` on
        rejection.
        """
        if self.readonly:
            raise IBConnectionError(
                "IBClient.place: client is read-only — refusing to transmit "
                "an order. Construct without readonly=True to place orders."
            )
        ib = self.connect()
        contract = self._build_contract(order.get("symbol"))

        direction = str(order.get("direction") or "").lower()
        if direction not in ("long", "short"):
            # Fall back to the explicit Buy/Sell side if present.
            side = str(order.get("side") or "").lower()
            direction = "long" if side in ("buy", "long") else "short"
        action = "BUY" if direction == "long" else "SELL"
        reverse = "SELL" if action == "BUY" else "BUY"

        try:
            qty = float(order["qty"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"retCode": 1, "retMsg": f"invalid qty: {exc}"}
        if qty <= 0:
            return {"retCode": 1, "retMsg": f"non-positive qty {qty}"}

        tick = tick_size_for(order.get("symbol") or self.symbol)
        tp_price = _round_to_tick(float(order["tp"]), tick) if order.get("tp") else None
        sl_price = _round_to_tick(float(order["sl"]), tick) if order.get("sl") else None

        try:
            from ib_insync import LimitOrder, MarketOrder, StopOrder  # type: ignore
        except ImportError:
            from ib_async import LimitOrder, MarketOrder, StopOrder  # type: ignore

        try:
            parent = MarketOrder(action, qty)
            parent.orderId = ib.client.getReqId()
            parent.transmit = sl_price is None and tp_price is None
            if self.account:
                parent.account = self.account

            children = []
            if tp_price is not None:
                tp = LimitOrder(reverse, qty, tp_price)
                tp.orderId = ib.client.getReqId()
                tp.parentId = parent.orderId
                tp.transmit = sl_price is None  # last leg transmits the bracket
                if self.account:
                    tp.account = self.account
                children.append(tp)
            if sl_price is not None:
                sl = StopOrder(reverse, qty, sl_price)
                sl.orderId = ib.client.getReqId()
                sl.parentId = parent.orderId
                sl.transmit = True
                if self.account:
                    sl.account = self.account
                children.append(sl)

            ib.placeOrder(contract, parent)
            for child in children:
                ib.placeOrder(contract, child)
            # Let the event loop flush the placements to the Gateway.
            try:
                ib.sleep(0)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            return {
                "retCode": 1,
                "retMsg": f"{type(exc).__name__}: {exc}",
            }

        return {
            "retCode": 0,
            "result": {"orderId": str(parent.orderId)},
            "retMsg": "OK",
        }

    def cancel(self, order_id: str) -> Dict[str, Any]:
        """Cancel an open order by its (parent) order id."""
        ib = self.connect()
        target = None
        for trade in self._open_trades(ib):
            if str(getattr(trade.order, "orderId", "")) == str(order_id):
                target = trade.order
                break
        if target is None:
            return {"retCode": 1, "retMsg": f"order {order_id} not found among open trades"}
        try:
            ib.cancelOrder(target)
            try:
                ib.sleep(0)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}
        return {"retCode": 0, "result": {"orderId": str(order_id)}, "retMsg": "OK"}

    def status(self, order_id: str) -> Dict[str, Any]:
        """Return the normalised status of an order by id.

        Shape mirrors ``account_order_status``'s Bybit return so the
        reconciler can consume IB the same way:
        ``{"order_id", "status", "filled_qty", "avg_price", "exec_time"}``.
        """
        ib = self.connect()
        for trade in self._all_trades(ib):
            if str(getattr(trade.order, "orderId", "")) == str(order_id):
                st = trade.orderStatus
                return {
                    "order_id": str(order_id),
                    "status": str(getattr(st, "status", "") or ""),
                    "filled_qty": float(getattr(st, "filled", 0) or 0),
                    "avg_price": float(getattr(st, "avgFillPrice", 0) or 0),
                    "exec_time": None,
                }
        return {
            "order_id": str(order_id),
            "status": "not_found",
            "filled_qty": 0.0,
            "avg_price": 0.0,
            "exec_time": None,
        }

    def balance(self) -> Dict[str, Any]:
        """Return a balance snapshot for the connected account.

        ``{"net_liquidation": float, "available_funds": float,
        "currency": str, "account": str}``. Reads IB's account summary
        tags; falls back to 0.0 when a tag is absent.
        """
        ib = self.connect()
        net_liq = 0.0
        avail = 0.0
        currency = "USD"
        try:
            rows = ib.accountSummary(self.account) if self.account else ib.accountSummary()
        except Exception as exc:  # noqa: BLE001
            raise IBConnectionError(
                f"IBClient.balance: accountSummary failed for "
                f"{self._masked_account()}: {type(exc).__name__}: {exc}"
            ) from exc
        for row in rows or []:
            tag = getattr(row, "tag", "")
            val = getattr(row, "value", "")
            cur = getattr(row, "currency", "") or currency
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if tag == "NetLiquidation":
                net_liq = fval
                currency = cur
            elif tag == "AvailableFunds":
                avail = fval
        return {
            "net_liquidation": net_liq,
            "available_funds": avail,
            "currency": currency,
            "account": self.account or "",
        }

    def positions(self) -> list:
        """Return open positions for the connected account.

        Shape mirrors ``account_open_positions``'s Bybit/Binance return so
        the hourly report, dashboard and reconciler consume IB the same
        way: ``[{symbol, side, size, entry_price, unrealised_pnl}]``. Only
        non-zero positions are returned. Reads IB's portfolio (per-account
        market value + unrealised PnL); raises :class:`IBConnectionError`
        if the read fails so callers map it to "could not read" rather than
        "no positions".
        """
        ib = self.connect()
        try:
            items = ib.portfolio()
        except Exception as exc:  # noqa: BLE001
            raise IBConnectionError(
                f"IBClient.positions: portfolio() failed for "
                f"{self._masked_account()}: {type(exc).__name__}: {exc}"
            ) from exc
        out = []
        for it in items or []:
            size = float(getattr(it, "position", 0) or 0)
            if size == 0:
                continue
            # A multi-account login returns portfolio items for every
            # account on the login; keep only this client's account when
            # one is configured so a shared Gateway doesn't leak positions.
            if self.account:
                it_acct = getattr(it, "account", None)
                if it_acct and str(it_acct) != str(self.account):
                    continue
            contract = getattr(it, "contract", None)
            symbol = (
                getattr(contract, "localSymbol", None)
                or getattr(contract, "symbol", None)
                or self.symbol
            )
            out.append({
                "symbol": symbol,
                "side": "long" if size > 0 else "short",
                "size": abs(size),
                "entry_price": float(getattr(it, "averageCost", 0) or 0),
                "unrealised_pnl": float(getattr(it, "unrealizedPNL", 0) or 0),
            })
        return out

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def self_test(self) -> Dict[str, Any]:
        """Connect and report a non-mutating connectivity snapshot.

        Returns ``{"connected", "host", "port", "client_id", "account",
        "server_version", "accounts", "net_liquidation", "error"}``. Never
        places an order. Used by ``scripts/ib_connect_check.py`` and the
        operator runbook to verify the Gateway is reachable.
        """
        snap: Dict[str, Any] = {
            "connected": False,
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "account": self._masked_account(),
            "server_version": None,
            "accounts": [],
            "net_liquidation": None,
            "error": None,
        }
        try:
            ib = self.connect()
            snap["connected"] = self.connected
            try:
                snap["server_version"] = ib.client.serverVersion()
            except Exception:  # noqa: BLE001
                pass
            try:
                snap["accounts"] = list(ib.managedAccounts() or [])
            except Exception:  # noqa: BLE001
                pass
            try:
                snap["net_liquidation"] = self.balance().get("net_liquidation")
            except Exception:  # noqa: BLE001
                pass
        except IBConnectionError as exc:
            snap["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            snap["error"] = f"{type(exc).__name__}: {exc}"
        return snap

    def _masked_account(self) -> str:
        if not self.account:
            return "<none>"
        if len(self.account) <= 4:
            return self.account
        return f"…{self.account[-4:]}"

    def fingerprint(self) -> str:
        """Last 4 chars of the account code for /accounts_status display."""
        if not self.account:
            return ""
        return self.account[-4:] if len(self.account) >= 4 else self.account

    @staticmethod
    def _open_trades(ib: Any) -> list:
        try:
            return list(ib.openTrades() or [])
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _all_trades(ib: Any) -> list:
        try:
            return list(ib.trades() or [])
        except Exception:  # noqa: BLE001
            return []

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<IBClient {self.host}:{self.port} clientId={self.client_id} "
            f"account={self._masked_account()} symbol={self.symbol}>"
        )


# ---------------------------------------------------------------------------
# Connection registry — one shared IBClient per (host, port, client_id)
# ---------------------------------------------------------------------------

_CONN_REGISTRY: Dict[Tuple[str, int, int], IBClient] = {}
_REGISTRY_LOCK = threading.Lock()


def get_ib_client(
    *,
    host: str = DEFAULT_IB_HOST,
    port: int,
    client_id: int,
    account: Optional[str] = None,
    symbol: str = "MES",
    readonly: bool = False,
    timeout: float = 10.0,
    _ib_factory: Optional[Callable[[], Any]] = None,
) -> IBClient:
    """Return a cached :class:`IBClient` for the gateway endpoint.

    Connections are expensive and clientIds must be unique per concurrent
    socket, so one client is shared per ``(host, port, client_id)`` and
    reused across ticks. The cached client lazily (re)connects on first
    use; a dropped socket transparently reconnects on the next call.

    A test factory (``_ib_factory``) bypasses the cache so unit tests get
    an isolated client with an injected fake ``IB``.
    """
    if _ib_factory is not None:
        return IBClient(
            host=host,
            port=int(port),
            client_id=int(client_id),
            account=account,
            symbol=symbol,
            readonly=readonly,
            timeout=timeout,
            _ib_factory=_ib_factory,
        )
    key = (str(host or DEFAULT_IB_HOST), int(port), int(client_id))
    with _REGISTRY_LOCK:
        client = _CONN_REGISTRY.get(key)
        if client is None:
            client = IBClient(
                host=key[0],
                port=key[1],
                client_id=key[2],
                account=account,
                symbol=symbol,
                readonly=readonly,
                timeout=timeout,
            )
            _CONN_REGISTRY[key] = client
        return client
