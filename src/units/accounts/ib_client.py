"""Interactive Brokers client — TWS API integration via ib_insync.

This module owns the *shape* of the IB integration for MES (Micro
E-mini S&P 500) futures. It mirrors the per-exchange separation already
used for Bybit (``pybit.unified_trading.HTTP``): the accounts unit
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
import math
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
# Grace gap before a single bounded retry of the liveness probe
# (BL-20260610-009). A freshly-established connection over the cross-host
# socat relay to the isolated gateway VM can miss the very first round-trip
# on a cold TCP flow even though the session is genuinely healthy — every
# OTHER call on the same socket (reqHistoricalData, accountSummary) works
# fine once the connection has exchanged a few messages; only this probe
# fires as the very first thing sent right after connect(). One retry after
# this gap absorbs a cold-start miss without abandoning detection: a
# genuinely logged-out/wedged Gateway still never answers either attempt.
_IB_PROBE_RETRY_GAP_S = _env_float("IB_PROBE_RETRY_GAP_S", 1.5)
# How long connect() fast-fails after a probe/connect failure before retrying
# the gateway again. Long enough that a wedged gateway can't be hammered every
# tick; short enough that a genuine recovery is picked up promptly.
_IB_BREAKER_COOLDOWN_S = _env_float("IB_BREAKER_COOLDOWN_S", 120.0)

# Hard cap on the post-connect account/portfolio WARM-UP
# (BL-20260706-IBWARMUP). ib_insync's ``accountSummary()`` (used by
# :meth:`IBClient.balance`) only ever returns data from an explicit
# ``reqAccountSummary()`` subscription — it is lazily triggered on first
# call and, per ``IB.RequestTimeout`` defaulting to ``0`` (unbounded),
# that first call has NO timeout of its own. Likewise ``portfolio()``
# (used by :meth:`IBClient.positions` on the trader's own, non-readonly
# connection) only fills once ``reqAccountUpdates`` has actually been
# answered; ``ib.connect()``'s own init requests race a silently-swallowed
# timeout (a slow response there is logged, not raised, and does not fail
# connect()). So a caller's FIRST balance()/positions() call after a fresh
# connect — or a reconnect after a silent idle-timeout drop — could race an
# empty/never-populated cache and misreport "gateway not logged in" even on
# a perfectly healthy gateway. This bound converts that race into "wait,
# bounded, for the real data" so every subsequent read is served from an
# already-warm cache. ``<= 0`` opts out (skips the warm-up entirely,
# restoring the pre-fix racy behaviour) — the same escape hatch shape as
# ``IB_PROBE_TIMEOUT_S``.
#
# BL-20260706-IBACCTUPDATES-COLLISION: ``reqAccountUpdates`` is a
# persistent PER-ACCOUNT subscription. The live trader's own execution
# connection (clientId 496/497) already holds it for every account it
# trades — sizing and naked-position checks depend on it. A second,
# concurrent client (:func:`ib_read_client_for`'s readonly, PID-salted
# clientId — used for diagnostics, the dashboard/reconciler position read,
# and every other out-of-band probe) subscribing to ``reqAccountUpdates``
# for the SAME account is the documented IB-API multi-client collision:
# the Gateway does not reliably deliver a fresh ``accountDownloadEnd`` to
# the second subscriber, so the warm-up (and any later ``portfolio()``
# read) can time out indefinitely for that connection alone — even though
# the trader's own connection stays perfectly healthy throughout (verified
# live 2026-07-06: the diag read client's warm-up timed out twice while the
# trader's connection kept evaluating mgc_trend_1h on fresh prices). Rather
# than fight that contention with a longer timeout, readonly clients never
# subscribe to ``reqAccountUpdates`` at all — see :meth:`_warm_account_data`
# and :meth:`IBClient.positions`, which route them through ``reqPositions()``
# instead: a stateless, one-shot request IBKR documents as safe for any
# number of concurrent clients against the same account.
_IB_ACCOUNT_WARMUP_TIMEOUT_S = _env_float("IB_ACCOUNT_WARMUP_TIMEOUT_S", 8.0)

# Bounded post-place rejection window (seconds; <= 0 restores the legacy
# fire-and-forget behaviour). ``IB.placeOrder`` is asynchronous — IBKR's
# accept/reject arrives on the event loop AFTER the call returns, so a
# fire-and-forget place() reported retCode 0 even when IBKR rejected the
# order outright; the journal row stayed open with no position behind it
# until the stuck-strategy watchdog orphaned it ~30 min later
# (BL-20260611-001, trade #2531). place() now pumps the event loop up to
# this many seconds and surfaces an immediately-rejected/cancelled parent
# as retCode 1 so the executor journals a real failure. An order still
# pending at the deadline is treated as accepted — the bound exists so a
# slow gateway can never stall the trading tick.
_IB_PLACE_CONFIRM_S = "IB_PLACE_CONFIRM_S"  # env name; read at call time

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
        # Set True only once the post-connect account/portfolio warm-up
        # (BL-20260706-IBWARMUP) has actually landed real data for the
        # CURRENT underlying ``ib`` object. Reset to False whenever a fresh
        # ``ib`` is created (fresh connect / reconnect after a dropped
        # socket) or the handle is torn down, so the next connect() always
        # re-warms before declaring success.
        self._account_data_ready: bool = False

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
            # Fresh handle (new socket or first-ever connect) — its wrapper
            # caches are empty, so the warm-up below must run again.
            self._account_data_ready = False

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

        # Post-connect account/portfolio warm-up (BL-20260706-IBWARMUP). Runs
        # once per underlying ``ib`` handle (guarded by
        # ``_account_data_ready``, reset above whenever a fresh handle is
        # created) — a still-connected, already-warm cached handle skips
        # straight through on every subsequent connect() call.
        if not self._account_data_ready:
            if not self._warm_account_data(ib):
                self._trip_breaker()
                self._safe_disconnect(ib)
                raise IBConnectionError(
                    f"IBClient: Gateway at {self.host}:{self.port} "
                    f"(account={self._masked_account()}) answered the liveness "
                    f"probe but never delivered account/portfolio data within "
                    f"{_IB_ACCOUNT_WARMUP_TIMEOUT_S:.0f}s (likely logged out or "
                    "no account resolved). Tripping circuit breaker so IB "
                    "calls do not block the trader loop."
                )
            self._account_data_ready = True

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
        # The next connect() builds a brand-new ``ib`` with empty wrapper
        # caches, so the warm-up must run again — never trust a torn-down
        # handle's stale "ready" state.
        self._account_data_ready = False

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
        # Two attempts (see _IB_PROBE_RETRY_GAP_S above): a timeout on the
        # first, cold-connection attempt is retried once after a short grace
        # gap before condemning the connection. Any non-timeout exception, or
        # a second timeout, fails immediately — a real wedge never answers
        # either attempt, so detection is unaffected; only a one-off
        # cold-start relay miss on a genuinely healthy session is absorbed.
        for attempt in (1, 2):
            try:
                loop.run_until_complete(
                    asyncio.wait_for(req(), timeout=_IB_PROBE_TIMEOUT_S)
                )
                return True
            except asyncio.TimeoutError:
                if attempt == 1:
                    logger.info(
                        "IBClient: liveness probe attempt 1/2 timed out after "
                        "%.0fs for %s:%s (account=%s) — retrying once after "
                        "%.1fs before concluding the gateway is wedged.",
                        _IB_PROBE_TIMEOUT_S, self.host, self.port,
                        self._masked_account(), _IB_PROBE_RETRY_GAP_S,
                    )
                    time.sleep(_IB_PROBE_RETRY_GAP_S)
                    continue
                logger.warning(
                    "IBClient: liveness probe timed out twice (%.0fs + "
                    "%.0fs retry) for %s:%s (account=%s) — gateway likely "
                    "logged out.",
                    _IB_PROBE_TIMEOUT_S, _IB_PROBE_TIMEOUT_S, self.host,
                    self.port, self._masked_account(),
                )
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "IBClient: liveness probe error for %s:%s (account=%s): "
                    "%s: %s",
                    self.host, self.port, self._masked_account(),
                    type(exc).__name__, exc,
                )
                return False
        return False  # pragma: no cover — loop always returns/raises above

    def _warm_account_data(self, ib: Any) -> bool:
        """Bounded post-connect account/portfolio warm-up (BL-20260706-IBWARMUP).

        ``ib.accountSummary()`` (:meth:`balance`) only ever returns data
        populated by an explicit ``reqAccountSummary()`` subscription — it
        is triggered lazily on first call, and the underlying
        ``IB.RequestTimeout`` defaults to ``0`` (unbounded), so that first
        call has no bound of its own. ``ib.portfolio()`` (:meth:`positions`)
        similarly only fills once ``reqAccountUpdates`` has actually been
        answered; ``ib.connect()``'s own init requests race a
        silently-swallowed timeout that never fails ``connect()`` itself.
        Net effect: the FIRST balance()/positions() call after a fresh
        connect — or a reconnect after a silent idle-timeout drop — could
        race an empty/never-populated cache and misreport "gateway not
        logged in" on a perfectly healthy gateway.

        This explicitly (re-)subscribes to both accountSummary and (once an
        account is known) accountUpdates and BLOCKS — bounded, with the
        same retry-then-condemn shape as :meth:`_probe_liveness` — until the
        first real data lands. By the time :meth:`connect` returns success,
        every subsequent balance()/positions() read is served from an
        already-warm cache — there is no cold race left to lose.

        Built to be safe in every context, mirroring :meth:`_probe_liveness`:

        * An IB stand-in with no ``reqAccountSummaryAsync`` (the plain test
          fakes that predate this warm-up, and any unrecognised
          implementation) skips the warm-up rather than assuming it's dead.
        * If we don't own a usable, non-running loop, skip rather than risk
          breaking a healthy gateway.
        * Any unexpected error is treated as "not warm" — fail safe, the
          breaker trips, IB is isolated, the loop keeps going.

        Returns True when warm (data landed, or the warm-up was
        skipped/opted-out), False when the gateway never answered within
        the bound.
        """
        if _IB_ACCOUNT_WARMUP_TIMEOUT_S <= 0:
            # Operator opt-out: skip the warm-up, restoring the pre-fix
            # racy behaviour (see the env var's module-level docstring).
            return True
        req_summary = getattr(ib, "reqAccountSummaryAsync", None)
        if req_summary is None:
            # Unknown/stub IB implementation — don't assume it's dead.
            return True
        import asyncio

        loop = self._loop
        if loop is None or loop.is_closed():
            return True
        try:
            if loop.is_running():
                return True
        except Exception:  # noqa: BLE001
            return True

        async def _warm() -> None:
            tasks = [req_summary()]
            if self.readonly:
                # Never subscribe a readonly client to reqAccountUpdates —
                # see BL-20260706-IBACCTUPDATES-COLLISION above. positions()
                # reads via reqPositions() instead, which this warms too
                # (under the same bounded retry-then-condemn budget) so a
                # hang surfaces here rather than as an unbounded call later.
                req_positions = getattr(ib, "reqPositionsAsync", None)
                if req_positions is not None:
                    tasks.append(req_positions())
            else:
                account = self.account or ""
                if not account:
                    try:
                        accounts = ib.client.getAccounts()
                    except Exception:  # noqa: BLE001
                        accounts = []
                    if len(accounts) == 1:
                        account = accounts[0]
                req_updates = getattr(ib, "reqAccountUpdatesAsync", None)
                if account and req_updates is not None:
                    tasks.append(req_updates(account))
            await asyncio.gather(*tasks)

        # Two attempts, same shape as _probe_liveness: a timeout on the
        # first (cold-connection) attempt is retried once after the grace
        # gap before condemning the connection.
        for attempt in (1, 2):
            try:
                loop.run_until_complete(
                    asyncio.wait_for(_warm(), timeout=_IB_ACCOUNT_WARMUP_TIMEOUT_S)
                )
                return True
            except asyncio.TimeoutError:
                if attempt == 1:
                    logger.info(
                        "IBClient: account/portfolio warm-up attempt 1/2 "
                        "timed out after %.0fs for %s:%s (account=%s) — "
                        "retrying once after %.1fs before concluding the "
                        "gateway is wedged.",
                        _IB_ACCOUNT_WARMUP_TIMEOUT_S, self.host, self.port,
                        self._masked_account(), _IB_PROBE_RETRY_GAP_S,
                    )
                    time.sleep(_IB_PROBE_RETRY_GAP_S)
                    continue
                logger.warning(
                    "IBClient: account/portfolio warm-up timed out twice "
                    "(%.0fs + %.0fs retry) for %s:%s (account=%s) — gateway "
                    "likely logged out.",
                    _IB_ACCOUNT_WARMUP_TIMEOUT_S, _IB_ACCOUNT_WARMUP_TIMEOUT_S,
                    self.host, self.port, self._masked_account(),
                )
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "IBClient: account/portfolio warm-up error for %s:%s "
                    "(account=%s): %s: %s",
                    self.host, self.port, self._masked_account(),
                    type(exc).__name__, exc,
                )
                return False
        return False  # pragma: no cover — loop always returns/raises above

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
                self._account_data_ready = False

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
        # IBKR futures fill in WHOLE contracts only. The futures-aware
        # sizer (RiskManager.position_size, market_type="futures") already
        # emits integers; this floor is defence-in-depth for any caller
        # that bypasses it (BL-20260611-001: a 3.643-contract MHG order was
        # transmitted, rejected asynchronously by IBKR, and the journal row
        # was orphaned 30 min later). Refusing — rather than silently
        # transmitting an unfillable order — turns the failure into a
        # journaled exchange rejection the executor can act on.
        whole_qty = math.floor(qty)
        if whole_qty < 1:
            return {
                "retCode": 1,
                "retMsg": (
                    f"qty {qty} is below 1 whole contract after flooring — "
                    "refusing fractional futures order"
                ),
            }
        if whole_qty != qty:
            logger.warning(
                "IBClient.place: flooring fractional futures qty %s -> %d "
                "for %s (caller bypassed futures-aware sizing?)",
                qty, whole_qty, order.get("symbol"),
            )
        qty = float(whole_qty)

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
            # Set the TIF EXPLICITLY. With an unset TIF, IBKR applies the
            # account's GUI order-preset default (DAY) and emits a spurious
            # "Error 10349 — Order TIF was set to DAY based on order preset"
            # that arrives as a parent `Cancelled` event on the event loop —
            # even though the order actually goes live and fills (a known
            # ib_async/TWS inconsistency). That fooled the post-place
            # rejection check below into journaling the fill as
            # `exchange_rejected` while the position stayed open at IBKR,
            # producing the BL-20260612-001 naked adopted-orphan desync
            # (trade #2539 rejected-in-journal, #2540 filled-at-broker).
            # An explicit TIF is no longer "based on order preset", so 10349
            # never fires. The market entry uses DAY (it fills immediately);
            # the protective legs use GTC so they survive past the session
            # for multi-day holds (a DAY stop would cancel at the close and
            # leave the position naked overnight).
            parent.tif = "DAY"
            if self.account:
                parent.account = self.account

            children = []
            if tp_price is not None:
                tp = LimitOrder(reverse, qty, tp_price)
                tp.orderId = ib.client.getReqId()
                tp.parentId = parent.orderId
                tp.transmit = sl_price is None  # last leg transmits the bracket
                tp.tif = "GTC"
                if self.account:
                    tp.account = self.account
                children.append(tp)
            if sl_price is not None:
                sl = StopOrder(reverse, qty, sl_price)
                sl.orderId = ib.client.getReqId()
                sl.parentId = parent.orderId
                sl.transmit = True
                sl.tif = "GTC"
                if self.account:
                    sl.account = self.account
                children.append(sl)

            parent_trade = ib.placeOrder(contract, parent)
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

        # Bounded post-place rejection check (see _IB_PLACE_CONFIRM_S note
        # at the top of the module). Pump the event loop briefly so an
        # immediate IBKR rejection of the parent surfaces here as a
        # retCode 1 instead of vanishing into a fire-and-forget success.
        rejected = self._await_parent_rejection(ib, parent_trade)
        if rejected is not None:
            return {"retCode": 1, "retMsg": rejected}

        return {
            "retCode": 0,
            "result": {"orderId": str(parent.orderId)},
            "retMsg": "OK",
        }

    def place_protective(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Attach SL/TP to an **already-open** position (no market entry).

        Unlike :meth:`place`, this never opens or adds to a position — it
        places only the reverse-side protective legs for a position that
        already exists at IBKR (e.g. a reconciler-adopted orphan that lost
        its bracket). The legs are an OCA pair (one fills → the other
        cancels): a stop at ``sl`` and a limit at ``tp``, both ``GTC`` so
        they persist until the position closes. At least one of ``sl`` /
        ``tp`` must be provided.

        ``order`` keys: ``symbol``, ``direction`` (``"long"`` / ``"short"``
        — the **position's** side; the protective legs are the reverse),
        ``qty`` (whole contracts), ``sl``, ``tp``. Return envelope mirrors
        :meth:`place`: ``{"retCode": 0, "result": {"orderId": ...}, ...}``
        on success, ``{"retCode": <non-zero>, "retMsg": ...}`` on refusal.
        """
        if self.readonly:
            raise IBConnectionError(
                "IBClient.place_protective: client is read-only — refusing "
                "to transmit an order. Construct without readonly=True."
            )

        tp_raw = order.get("tp")
        sl_raw = order.get("sl")
        if (tp_raw in (None, 0) or float(tp_raw) <= 0) and (
            sl_raw in (None, 0) or float(sl_raw) <= 0
        ):
            return {
                "retCode": 1,
                "retMsg": "no protective levels (need at least one of sl/tp)",
            }

        direction = str(order.get("direction") or "").lower()
        if direction not in ("long", "short"):
            side = str(order.get("side") or "").lower()
            direction = "long" if side in ("buy", "long") else "short"
        # Protective legs CLOSE the position, so they take the reverse side.
        reverse = "SELL" if direction == "long" else "BUY"

        try:
            qty = float(order["qty"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"retCode": 1, "retMsg": f"invalid qty: {exc}"}
        whole_qty = math.floor(qty)
        if whole_qty < 1:
            return {
                "retCode": 1,
                "retMsg": f"qty {qty} below 1 whole contract — refusing",
            }
        qty = float(whole_qty)

        ib = self.connect()
        sym = str(order.get("symbol") or self.symbol or "").upper()
        # Accumulation guard (BL-20260624-MHG-FLIP). Cancel any resting protective
        # legs for this symbol BEFORE placing the fresh OCA pair. place_protective
        # is reached on every re-arm — orphan adopt/reattach
        # (_rearm_broker_protection_after_recovery) and naked-autoprotect — and
        # each call makes a NEW independent OCA group (oca-protect-<reqId>). Without
        # a pre-cancel, repeated re-arms across an orphan flap STACK multiple live
        # OCA brackets on the same position; their stops later fire together and
        # FLIP a (by-then flat) position into a reverse orphan — the MHG long that
        # closed clean then reappeared as a short (2026-06-24). This mirrors the
        # cancel-then-re-arm discipline already in modify_protective(); making
        # place_protective itself idempotent fixes every direct caller. Best-effort:
        # a cancel failure must not block arming protection on a live naked position.
        try:
            self._cancel_resting_orders_for_symbol(ib, sym)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "place_protective: pre-cancel of resting %s legs failed "
                "(proceeding to arm fresh bracket): %s", sym, exc,
            )
        contract = self._build_contract(order.get("symbol"))
        tick = tick_size_for(order.get("symbol") or self.symbol)
        tp_price = _round_to_tick(float(tp_raw), tick) if tp_raw else None
        sl_price = _round_to_tick(float(sl_raw), tick) if sl_raw else None

        try:
            from ib_insync import LimitOrder, StopOrder  # type: ignore
        except ImportError:
            from ib_async import LimitOrder, StopOrder  # type: ignore

        try:
            # OCA group ties the legs together with no parent: when one
            # fills, IBKR cancels the remaining leg (ocaType=1).
            oca_group = f"oca-protect-{ib.client.getReqId()}"
            legs = []
            if tp_price is not None:
                tp = LimitOrder(reverse, qty, tp_price)
                tp.orderId = ib.client.getReqId()
                legs.append(tp)
            if sl_price is not None:
                sl = StopOrder(reverse, qty, sl_price)
                sl.orderId = ib.client.getReqId()
                legs.append(sl)
            for i, leg in enumerate(legs):
                leg.ocaGroup = oca_group
                leg.ocaType = 1
                leg.tif = "GTC"
                # Transmit only on the final leg so IBKR receives the OCA
                # pair atomically (mirrors the bracket-transmit discipline
                # in place()).
                leg.transmit = i == len(legs) - 1
                if self.account:
                    leg.account = self.account

            for leg in legs:
                ib.placeOrder(contract, leg)
            try:
                ib.sleep(0)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}

        return {
            "retCode": 0,
            "result": {"orderId": str(legs[-1].orderId), "ocaGroup": oca_group},
            "retMsg": "OK",
        }

    def modify_protective(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Re-arm the GTC OCA protective bracket at new SL/TP levels.

        S2 of the live-trade management contract (BL-20260616-LTMGMT-MODIFY) —
        the IB analogue of Bybit's in-place ``set_trading_stop``. IB working
        orders can't be price-patched through this stateless client (no
        cross-tick order references survive a fresh monitor build), so a SL/TP
        modify is a **re-arm**: cancel the resting protective legs for *symbol*,
        then place a fresh OCA pair at the supplied levels via
        :meth:`place_protective`. The caller (``execute.modify_open_order``)
        supplies BOTH the changed leg and the current value of the unchanged
        one (merged from the order package) so neither stop nor target is
        dropped — re-arming only the changed leg would leave the position half
        protected. Reusing ``place_protective`` keeps the leg shape (GTC OCA,
        reverse side, whole-contract qty, transmit-on-last) byte-identical to
        the naked-autoprotect / entry path.

        ``order`` keys mirror :meth:`place_protective`: ``symbol``,
        ``direction`` (the **position's** side), ``qty`` (whole contracts),
        ``sl``, ``tp``. Return envelope mirrors :meth:`place` /
        :meth:`place_protective`: ``{"retCode": 0, "result": {"orderId": ...}}``
        on success, ``{"retCode": <non-zero>, "retMsg": ...}`` on refusal.
        Bounded + best-effort — never raises; a connect / cancel failure
        returns ``retCode != 0`` so the monitor leaves the DB row + the
        existing (un-cancelled) bracket in place and the strategy re-emits the
        verdict next tick.
        """
        if self.readonly:
            return {
                "retCode": 1,
                "retMsg": "IBClient.modify_protective: client is read-only — "
                          "refusing to transmit a modify.",
            }
        sym = str(order.get("symbol") or self.symbol or "").upper()
        # Validate the connection up front so a connect failure returns a clean
        # retCode (never raises). We no longer cancel resting legs here:
        # place_protective now cancels the symbol's resting legs itself before
        # arming (BL-20260624-MHG-FLIP), so cancelling here too would
        # DOUBLE-cancel. Delegating keeps it a single cancel-then-arm.
        try:
            self.connect()
        except IBConnectionError as exc:
            return {"retCode": 1, "retMsg": f"IB connect failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}
        return self.place_protective({**order, "symbol": sym})

    def close(
        self,
        symbol: Optional[str],
        side: str,
        qty: float,
    ) -> Dict[str, Any]:
        """Flatten an open IB position with an opposing reduce market order.

        P3 of the live-trade management contract — the IB analogue of
        Bybit's reduce-only close. IB futures have **no** ``reduceOnly``
        flag, so a flatten is two steps that must both happen or the
        position is left in a bad state:

          1. **Cancel any resting protective orders for *symbol*** — the
             entry bracket's GTC stop + take-profit (or a
             ``place_protective`` OCA pair). If we placed an opposing
             market order while those still rested, a later fill of one
             leg would *re-open* a position in the opposite direction
             (the bracket was sized to the now-closed position). Cancelling
             first makes the close idempotent and naked-order-free.
          2. **Place an opposing market order sized to the open position.**
             ``side`` is the side of the original entry (``"long"`` /
             ``"short"``); the close takes the reverse. ``qty`` is the
             position size to flatten (whole contracts). We size the close
             to ``min(requested_qty, live_exchange_qty)`` read from
             :meth:`positions` so a stale DB qty can never transmit an
             order larger than what IB actually holds (which on a one-way
             futures account would *open* a reverse position rather than
             flatten).

        Bounded + best-effort, mirroring the rest of this client: never
        raises, reuses the existing connect / circuit-breaker / fetch
        timeouts, and returns the same envelope shape as :meth:`place` so
        ``execute.close_open_position`` reads IB and Bybit identically:
        ``{"retCode": 0, "result": {"orderId": ...}, "retMsg": "OK"}`` on
        success; ``{"retCode": <non-zero>, "retMsg": "<reason>"}`` on any
        refusal / failure (the monitor leaves the DB row open + retries).
        """
        if self.readonly:
            return {
                "retCode": 1,
                "retMsg": "IBClient.close: client is read-only — refusing "
                          "to transmit a close order.",
            }
        sym = str(symbol or self.symbol or "").upper()
        try:
            requested_qty = float(qty)
        except (TypeError, ValueError) as exc:
            return {"retCode": 1, "retMsg": f"invalid qty: {exc}"}
        if requested_qty <= 0:
            return {"retCode": 1, "retMsg": f"non-positive qty {requested_qty}"}

        direction = str(side or "").lower()
        if direction not in ("long", "short"):
            explicit = str(side or "").lower()
            direction = "long" if explicit in ("buy", "long") else "short"
        # A close takes the REVERSE side of the held position.
        close_action = "SELL" if direction == "long" else "BUY"

        try:
            ib = self.connect()
        except IBConnectionError as exc:
            return {"retCode": 1, "retMsg": f"IB connect failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}

        # Step 0 — clamp the close qty to the live exchange position so a
        # stale/oversized DB qty can never flip the position (one-way
        # futures). When the read fails we keep the requested qty (the
        # caller's best knowledge) rather than refusing to flatten.
        try:
            live_qty = self._live_position_qty(sym)
        except Exception:  # noqa: BLE001
            live_qty = None
        if live_qty is not None:
            if live_qty <= 0:
                # IB reports flat already — nothing to close. Still cancel
                # any stray resting protective orders, then return success
                # (idempotent close, matching the Alpaca 404 → ok mapping).
                self._cancel_resting_orders_for_symbol(ib, sym)
                return {
                    "retCode": 0,
                    "result": {"orderId": None, "note": "already flat"},
                    "retMsg": "OK",
                }
            close_qty = min(requested_qty, live_qty)
        else:
            close_qty = requested_qty

        close_qty = float(math.floor(close_qty))
        if close_qty < 1:
            return {
                "retCode": 1,
                "retMsg": f"close qty {close_qty} below 1 whole contract — "
                          "refusing fractional futures close",
            }

        # Step 1 — cancel resting protective orders for the symbol so the
        # opposing market order can't leave a naked working order behind.
        self._cancel_resting_orders_for_symbol(ib, sym)

        # Step 2 — opposing market order to flatten.
        try:
            from ib_insync import MarketOrder  # type: ignore
        except ImportError:
            from ib_async import MarketOrder  # type: ignore

        try:
            contract = self._build_contract(sym)
            close_order = MarketOrder(close_action, close_qty)
            close_order.orderId = ib.client.getReqId()
            close_order.transmit = True
            close_order.tif = "DAY"
            if self.account:
                close_order.account = self.account
            close_trade = ib.placeOrder(contract, close_order)
            try:
                ib.sleep(0)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}

        # Bounded post-place rejection check (same contract as place()): an
        # immediate IBKR reject of the close surfaces as retCode 1 so the
        # monitor leaves the DB row open and retries, rather than marking a
        # phantom close.
        rejected = self._await_parent_rejection(ib, close_trade)
        if rejected is not None:
            return {"retCode": 1, "retMsg": rejected}

        # Bounded post-place FLATTEN confirmation (BL-20260624-MHG-CLOSE-CONFIRM).
        # Unlike place() — where "not rejected" suffices, since a non-filling OPEN
        # just means no position — a close that is *accepted but does not fill*
        # leaves a REAL position open while the monitor marks the DB row closed.
        # The position is then orphaned and, because Step 1 already cancelled its
        # protective bracket, left NAKED until a later reconcile re-adopts it (the
        # perpetual MHG/ib_paper adopt → sl_cross "close" → re-orphan flap). So we
        # do NOT report success on acceptance alone: re-read the live position and
        # require it to actually reach flat. If it does not within the window,
        # return retCode 1 so the monitor leaves the DB row open, naked-autoprotect
        # re-arms a bracket next tick, and the close is retried — i.e. "DB closed"
        # always means "broker confirmed flat". ``IB_CLOSE_CONFIRM_S <= 0`` skips
        # the check (legacy accept-is-success behaviour).
        confirm_s = _env_float("IB_CLOSE_CONFIRM_S", 6.0)
        if confirm_s > 0:
            deadline = time.monotonic() + confirm_s
            last_qty: Optional[float] = None
            flat = False
            while True:
                try:
                    last_qty = self._live_position_qty(sym)
                except Exception:  # noqa: BLE001
                    last_qty = None
                # A read failure (None) is NOT treated as flat — we don't know,
                # so keep polling until the deadline rather than confirming a
                # close we can't see.
                if last_qty is not None and last_qty <= 0:
                    flat = True
                    break
                if time.monotonic() >= deadline:
                    break
                try:
                    ib.sleep(0.25)
                except Exception:  # noqa: BLE001
                    break
            if not flat:
                return {
                    "retCode": 1,
                    "retMsg": (
                        f"close not confirmed flat: live_qty={last_qty} after "
                        f"~{confirm_s}s — close order {close_order.orderId} was "
                        "accepted but the position is still open; leaving DB row "
                        "open to re-arm protection and retry next tick"
                    ),
                }

        return {
            "retCode": 0,
            "result": {"orderId": str(close_order.orderId)},
            "retMsg": "OK",
        }

    def _live_position_qty(self, symbol: str) -> Optional[float]:
        """Absolute open-position size for *symbol* from IB's portfolio.

        Returns the absolute contract count (``0.0`` when flat) or
        ``None`` when the read fails — so :meth:`close` can distinguish
        "IB says flat" from "couldn't read IB". Matches by the generic
        root symbol (``MES`` / ``MGC`` / ``MHG``), the same normalisation
        :meth:`positions` applies.
        """
        sym = str(symbol or "").upper()
        for pos in self.positions():
            if str(pos.get("symbol") or "").upper() == sym:
                try:
                    return abs(float(pos.get("size") or 0.0))
                except (TypeError, ValueError):
                    return None
        return 0.0

    def _cancel_resting_orders_for_symbol(self, ib: Any, symbol: str) -> None:
        """Cancel every open (resting) order on *symbol* — best-effort.

        Sweeps the protective bracket / OCA legs so a subsequent opposing
        market close can't leave a naked working order that later fills and
        re-opens a position. Matches by the contract's generic root symbol
        (``contract.symbol``), the same axis the journal + reconciler use.
        Never raises — a cancel failure on one leg must not block the close.
        """
        sym = str(symbol or "").upper()
        for trade in self._open_trades(ib):
            try:
                contract = getattr(trade, "contract", None)
                trade_sym = str(getattr(contract, "symbol", "") or "").upper()
                if sym and trade_sym and trade_sym != sym:
                    continue
                ib.cancelOrder(trade.order)
            except Exception:  # noqa: BLE001
                # Best-effort: a single un-cancellable leg must not abort
                # the flatten; the naked-autoprotect / reconciler paths
                # converge the remainder.
                continue
        try:
            ib.sleep(0)
        except Exception:  # noqa: BLE001
            pass

    def cancel_resting_protection(self, symbol: Optional[str]) -> Dict[str, Any]:
        """Cancel every resting (working) order for *symbol* — public, best-effort.

        The reconciler concludes a position is flat on the exchange and closes
        the DB row WITHOUT going through :meth:`close` (the position is already
        flat, so no opposing order is sent) — but that path never cancelled the
        symbol's resting protective bracket legs. On IB those GTC OCA stops then
        sit on a flat position and can later fire, SELLING into a reverse
        position → a fresh orphan (the MHG long→short flip, BL-20260624-MHG-FLIP).
        This sweeps them after a reconciler flat-close. Never raises; returns a
        retCode envelope mirroring the other client methods.
        """
        if self.readonly:
            return {"retCode": 1, "retMsg": "client is read-only"}
        sym = str(symbol or self.symbol or "").upper()
        try:
            ib = self.connect()
        except IBConnectionError as exc:
            return {"retCode": 1, "retMsg": f"IB connect failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}
        try:
            self._cancel_resting_orders_for_symbol(ib, sym)
        except Exception as exc:  # noqa: BLE001
            return {"retCode": 1, "retMsg": f"cancel-resting failed: {exc}"}
        return {"retCode": 0, "result": {"symbol": sym}, "retMsg": "OK"}

    @staticmethod
    def _await_parent_rejection(ib: Any, parent_trade: Any) -> Optional[str]:
        """Watch a just-placed parent order for an immediate IBKR reject.

        Returns a human-readable rejection reason when the parent order
        reaches a terminal dead state (``Cancelled`` / ``ApiCancelled`` /
        ``Inactive``) within ``IB_PLACE_CONFIRM_S`` seconds (default 3.0;
        ``<= 0`` skips the check entirely — the legacy fire-and-forget
        behaviour). Returns ``None`` when the order is accepted
        (``PreSubmitted`` / ``Submitted`` / ``Filled``) or still pending at
        the deadline — a pending market order is treated as accepted so a
        slow gateway can never stall the trading tick beyond the bound.
        """
        confirm_s = _env_float("IB_PLACE_CONFIRM_S", 3.0)
        if confirm_s <= 0 or parent_trade is None:
            return None
        _ACCEPTED = ("PreSubmitted", "Submitted", "Filled")
        _DEAD = ("Cancelled", "ApiCancelled", "Inactive")
        deadline = time.monotonic() + confirm_s
        while True:
            status = str(
                getattr(getattr(parent_trade, "orderStatus", None), "status", "")
                or ""
            )
            if status in _ACCEPTED:
                return None
            if status in _DEAD:
                # Pull the last gateway message (ib_insync logs IBKR error
                # strings onto trade.log) so the journal carries the cause.
                detail = ""
                try:
                    entries = list(getattr(parent_trade, "log", None) or [])
                    if entries:
                        detail = str(getattr(entries[-1], "message", "") or "")
                except Exception:  # noqa: BLE001
                    detail = ""
                return (
                    f"IBKR rejected/cancelled parent order: status={status}"
                    + (f" — {detail}" if detail else "")
                )
            if time.monotonic() >= deadline:
                return None
            try:
                ib.sleep(0.25)
            except Exception:  # noqa: BLE001
                return None

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
        non-zero positions are returned; raises :class:`IBConnectionError`
        if the read fails so callers map it to "could not read" rather than
        "no positions".

        Two distinct sources depending on the client:

        * The trader's own, non-readonly execution connection reads IB's
          ``portfolio()`` (per-account market value + unrealised PnL, fed
          by its own persistent ``reqAccountUpdates`` subscription).
        * A **readonly** client (:func:`ib_read_client_for` — diagnostics,
          the dashboard/reconciler read path, every other out-of-band
          probe) instead calls ``reqPositions()`` — see
          BL-20260706-IBACCTUPDATES-COLLISION: ``reqAccountUpdates`` is a
          persistent per-account subscription the trader's own connection
          already holds, and a second concurrent subscriber for the SAME
          account is a documented IB-API collision that can leave the
          second client's data never delivered. ``reqPositions()`` is a
          stateless, one-shot request IBKR documents as safe for any
          number of concurrent clients. The cost: it carries no
          ``unrealizedPNL`` (only ``portfolio()``'s ``PortfolioItem`` does)
          — ``unrealised_pnl`` is ``None`` on this path, which the rest of
          the stack already treats as "not measured" (broker-truth
          unavailable), not a fabricated zero.
        """
        ib = self.connect()
        try:
            if self.readonly:
                items = self._req_positions_snapshot(ib)
            else:
                items = ib.portfolio()
        except Exception as exc:  # noqa: BLE001
            source = "reqPositions" if self.readonly else "portfolio()"
            raise IBConnectionError(
                f"IBClient.positions: {source} failed for "
                f"{self._masked_account()}: {type(exc).__name__}: {exc}"
            ) from exc
        out = []
        for it in items or []:
            size = float(getattr(it, "position", 0) or 0)
            if size == 0:
                continue
            # A multi-account login returns items for every account on the
            # login; keep only this client's account when one is configured
            # so a shared Gateway doesn't leak positions.
            if self.account:
                it_acct = getattr(it, "account", None)
                if it_acct and str(it_acct) != str(self.account):
                    continue
            contract = getattr(it, "contract", None)
            # Normalise the IB item to the SAME shape every other consumer
            # (journal, reconcilers, dashboard, SL/TP math) speaks — the
            # generic root symbol + a per-unit entry price, exactly like
            # the Bybit/Binance position rows this method mirrors:
            #
            #  * Symbol — IB's ``localSymbol`` carries the expiry month code
            #    (``MHGN6``, ``MESM6``); the bot trades by the generic root
            #    (``MHG``, ``MES``), which IB exposes as ``contract.symbol``.
            #    Emitting the localSymbol meant an owned IB trade (symbol
            #    ``MHG``) could never reconcile against its own exchange
            #    position (symbol ``MHGN6``) — it orphaned, and the adopted
            #    orphan diverged from the strategy symbol forever.
            #  * Entry price — IB's per-unit average cost for a future is
            #    TIMES the contract multiplier (MHG: 6.396 × 2500 =
            #    15989.72). Divide by the multiplier so ``entry_price`` is
            #    the per-unit price like every other exchange, instead of a
            #    multiplier-inflated number that corrupts the adopted-orphan
            #    entry, PnL display, and any entry-based math.
            #    (BL-20260613-IBPOS: the 15989.72-entry / MHGN6-symbol
            #    adopted-orphan corruption on ib_paper.)
            symbol = (
                getattr(contract, "symbol", None)
                or getattr(contract, "localSymbol", None)
                or self.symbol
            )
            # ``PortfolioItem`` (portfolio(), the non-readonly path) exposes
            # ``averageCost``; the plain ``Position`` object returned by
            # reqPositions() (the readonly path) exposes the same figure as
            # ``avgCost`` instead — check both.
            avg_cost_raw = getattr(it, "averageCost", None)
            if avg_cost_raw is None:
                avg_cost_raw = getattr(it, "avgCost", 0)
            avg_cost = float(avg_cost_raw or 0)
            try:
                multiplier = float(getattr(contract, "multiplier", "") or 0)
            except (TypeError, ValueError):
                multiplier = 0.0
            entry_price = avg_cost / multiplier if multiplier > 0 else avg_cost
            # ``Position`` (reqPositions(), the readonly path) carries no
            # unrealizedPNL — only ``PortfolioItem`` does. ``None`` is the
            # honest "not measured" value, matching the broker-truth-else-
            # unavailable contract the rest of the stack already applies
            # (see dashboard.py::_broker_unrealised_for_trade).
            upnl_raw = getattr(it, "unrealizedPNL", None)
            out.append({
                "symbol": symbol,
                "side": "long" if size > 0 else "short",
                "size": abs(size),
                "entry_price": entry_price,
                "unrealised_pnl": float(upnl_raw) if upnl_raw is not None else None,
            })
        return out

    def _req_positions_snapshot(self, ib: Any) -> list:
        """Fresh, bounded ``reqPositions()`` snapshot for a readonly client.

        Stateless and multi-client-safe (unlike ``portfolio()``, which
        depends on the persistent, single-effective-subscriber
        ``reqAccountUpdates`` — see :meth:`_warm_account_data`). Bounded the
        same way as the warm-up so a mid-session Gateway hiccup can't hang
        this call forever; falls back to the (possibly stale/empty) cached
        ``ib.positions()`` when the client has no async variant (test
        stubs) or this thread doesn't own a usable, non-running loop.
        """
        req = getattr(ib, "reqPositionsAsync", None)
        loop = self._loop
        if req is None or loop is None or loop.is_closed():
            return list(ib.positions() or [])
        try:
            if loop.is_running():
                return list(ib.positions() or [])
        except Exception:  # noqa: BLE001
            return list(ib.positions() or [])
        import asyncio

        timeout = (
            _IB_ACCOUNT_WARMUP_TIMEOUT_S if _IB_ACCOUNT_WARMUP_TIMEOUT_S > 0
            else _IB_PROBE_TIMEOUT_S if _IB_PROBE_TIMEOUT_S > 0
            else 8.0
        )
        loop.run_until_complete(asyncio.wait_for(req(), timeout=timeout))
        return list(ib.positions() or [])

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
