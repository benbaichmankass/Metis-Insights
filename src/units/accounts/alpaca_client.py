"""Alpaca execution client — M15 Phase 2b (S-M15-PHASE2B-ALPACA).

Real REST integration for Alpaca's Trading API (paper host by default),
the migration's second platform per the Phase-0 verdict: the daily ETF
futures-replacements (trend1d QQQ/SPY ≈ ``mes_trend_long_1d``,
pullback1d GLD ≈ ``mgc_pullback_1d``) and the SPY intraday candidates
(docs/research/m15-phase0-results-2026-06-10.md).

Mirrors the OANDA contract: ``place()`` returns a
retCode-style envelope, missing creds raise
:class:`MissingCredentialsError` naming the env vars (never values),
and the factory (`clients.alpaca_client_for`) returns ``None`` when
creds are absent so the account loads ``configured: False``.

Orders are **bracket** market orders (entry + ``take_profit`` limit +
``stop_loss`` stop in one atomic request) so SL/TP protection is
broker-side from the first fill — surviving RTH closes, weekends, and
trader restarts. Bracket orders require whole-share quantities and
``time_in_force: day`` legs (Alpaca constraint); qty is floored at 1.

Auth: key id + secret from ``ALPACA_API_KEY_ID`` /
``ALPACA_API_SECRET_KEY`` (free paper keys). ``ALPACA_ENV`` picks the
host: ``paper`` (default) → paper-api.alpaca.markets, ``live`` →
api.alpaca.markets (an explicit flip, like ``OANDA_ENV``).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_HOSTS = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}


def _env_float(name: str, default: float) -> float:
    """Read-at-call-time float env var (mirrors ib_client.py's helper)."""
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


class MissingCredentialsError(RuntimeError):
    """Raised when an action requires the Alpaca key pair.

    Message carries env-var *names* only, never values (no-secrets rule).
    """


class AlpacaClient:
    """Thin Trading-API REST client (key-pair auth, paper host default)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        env_name = (env or os.environ.get("ALPACA_ENV", "paper")).strip().lower()
        self.env = env_name if env_name in _HOSTS else "paper"
        self.base_url = (base_url or _HOSTS[self.env]).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------ utils
    def _require_creds(self, action: str) -> None:
        if not self.api_key or not self.api_secret:
            raise MissingCredentialsError(
                f"alpaca {action}: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY unset."
            )

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        """One HTTP round-trip → retCode envelope (never raises on HTTP)."""
        url = f"{self.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=self.timeout
            )
        except Exception as exc:  # noqa: BLE001
            return {"retCode": -1, "retMsg": f"network error: {exc}"}
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {}
        if 200 <= resp.status_code < 300:
            return {"retCode": 0, "result": payload}
        msg = payload.get("message") or f"HTTP {resp.status_code}"
        return {"retCode": resp.status_code, "retMsg": str(msg)}

    # ------------------------------------------------------------ orders
    def place(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Place a bracket MARKET order; retCode envelope.

        Expects the executor's order dict: ``symbol``, ``side``
        (``Buy``/``Sell``, case-insensitive), ``qty`` (shares, floored
        at 1 whole share — bracket orders disallow fractionals),
        optional ``sl`` / ``tp`` prices (both present → bracket; one →
        OTO; none → plain market). Equity prices are 2dp.

        Bounded post-accept REJECTION confirmation (BL-20260707-ALPACA-
        ENTRY-FILL-CONFIRM-GAP), mirroring ``IBClient.place``'s
        ``IB_PLACE_CONFIRM_S`` contract: Alpaca's initial HTTP 2xx on POST
        ``/v2/orders`` means the order passed SYNCHRONOUS validation, not
        that it will actually execute — an async risk check (PDT rule,
        wash-trade prevention, extended-hours restriction, a deeper margin
        check) can still reject it moments later. Without this, a
        post-accept rejection was invisible to the caller: ``_submit_order``
        only ever saw the initial 2xx and ``_log_trade_to_journal`` wrote a
        phantom ``open`` trade row for a position that never actually
        existed on the broker. ``ALPACA_PLACE_CONFIRM_S`` (default 3.0s,
        matching ``IB_PLACE_CONFIRM_S``'s default; ``<= 0`` restores the
        legacy accept-is-success/no-poll behaviour): if the order reaches a
        terminal ``rejected``/``canceled``/``expired`` state within the
        window, that is surfaced as a real failure (``retCode != 0``) so the
        caller refuses the trade instead of journaling a phantom position.
        Still-pending or already-``filled`` at the deadline is reported as
        success unchanged. Deliberately does NOT attempt to capture/backfill
        the broker's real fill price into the journal — that is a separate,
        larger design question ``_submit_order``'s shared str-only return
        contract (identical across bybit/IB/alpaca/oanda) doesn't
        accommodate without a broader refactor, and
        ``RECONCILER_SNAPSHOT_MIN_FILL_AGE_S`` already exists as the
        downstream grace period for a genuinely-slow-but-good fill.
        """
        self._require_creds("place")
        side = str(order.get("side", "")).strip().lower()
        if side not in ("buy", "sell"):
            return {"retCode": -2, "retMsg": f"invalid side {order.get('side')!r}"}
        # Whole-share quantization via the shared helper (single source of truth
        # with the executor + partial-close paths) so the qty placed here can
        # never drift from the qty journaled (BL-20260622-ALPACA-FRACTIONAL-SIZE).
        from src.units.accounts.risk import whole_unit_qty

        qty = int(whole_unit_qty(order["qty"], min_one=True))
        body: Dict[str, Any] = {
            "symbol": str(order["symbol"]).upper(),
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        tp, sl = order.get("tp"), order.get("sl")
        if tp is not None and sl is not None:
            body["order_class"] = "bracket"
            body["take_profit"] = {"limit_price": f"{float(tp):.2f}"}
            body["stop_loss"] = {"stop_price": f"{float(sl):.2f}"}
        elif tp is not None or sl is not None:
            body["order_class"] = "oto"
            if tp is not None:
                body["take_profit"] = {"limit_price": f"{float(tp):.2f}"}
            if sl is not None:
                body["stop_loss"] = {"stop_price": f"{float(sl):.2f}"}
        env = self._request("POST", "/v2/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        order_id = str(result.get("id") or "")

        confirm_s = _env_float("ALPACA_PLACE_CONFIRM_S", 3.0)
        if confirm_s > 0 and order_id:
            deadline = time.monotonic() + confirm_s
            while True:
                status_env = self._request("GET", f"/v2/orders/{order_id}")
                if status_env.get("retCode") == 0:
                    st = str(
                        (status_env.get("result") or {}).get("status") or ""
                    ).lower()
                    if st in ("rejected", "canceled", "cancelled", "expired"):
                        return {
                            "retCode": 1,
                            "retMsg": (
                                f"alpaca order {order_id} for "
                                f"{body['symbol']} was accepted then {st} — "
                                "refusing to journal a phantom position"
                            ),
                        }
                    if st == "filled":
                        break
                # An unreadable status mid-poll is NOT treated as terminal —
                # keep polling until the deadline rather than guessing.
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.25)

        return {"retCode": 0, "result": {"orderId": order_id}}

    # ------------------------------------------------------------ account
    def balance(self) -> Optional[float]:
        """Account equity in USD, or ``None`` on any failure."""
        try:
            self._require_creds("balance")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/account")
        if env.get("retCode") != 0:
            logger.warning("alpaca balance: %s", env.get("retMsg"))
            return None
        acct = env.get("result") or {}
        # `or` here would treat a genuine equity=0.0 as falsy and silently
        # substitute cash instead — the same truthiness shape as the
        # already-fixed account_open_positions bug (BL-20260707). `equity`
        # is the authoritative figure; only fall back to `cash` when it is
        # actually ABSENT, not merely zero.
        equity = acct.get("equity")
        raw = equity if equity is not None else acct.get("cash")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def buying_power(self) -> Optional[float]:
        """Reg-T (overnight-safe) buying power in USD, or ``None`` on failure.

        Reads the broker's own ``regt_buying_power`` — which already bakes in
        the account's real margin multiplier (1x for a cash account, 2x for a
        Reg-T margin account) — so the sizer's margin pre-flight cap reflects
        TRUE buying power instead of defaulting to cash-only (the
        ``effective_leverage=1`` fallback when ``risk.leverage`` is unset). This
        is the same "prefer broker truth" pattern Bybit already uses via
        ``_fetch_linear_available_balance``. ``regt_buying_power`` is the
        OVERNIGHT figure (NOT the up-to-4x intraday day-trading buying power),
        which is the correct, conservative basis for the overnight-held swing
        strategies that route to Alpaca. Falls back to ``buying_power`` then
        ``cash``. Best-effort — ``None`` leaves the sizer on its buffer fallback.

        NOTE: this is fed to ``position_size(available_usd=...)``, where it is
        multiplied by ``effective_leverage``. The Alpaca accounts leave
        ``risk.leverage`` unset (→ effective_leverage 1) ON PURPOSE so this
        already-leveraged figure is not multiplied a second time; do not set a
        ``leverage`` on an Alpaca account while this path is live.
        """
        try:
            self._require_creds("buying_power")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/account")
        if env.get("retCode") != 0:
            logger.warning("alpaca buying_power: %s", env.get("retMsg"))
            return None
        acct = env.get("result") or {}
        for key in ("regt_buying_power", "buying_power", "cash"):
            raw = acct.get(key)
            if raw is None:
                continue
            try:
                bp = float(raw)
            except (TypeError, ValueError):
                continue
            # BL-20260707: `if bp > 0:` treated a genuinely-zero buying power
            # (fully invested / no free margin — a real, common account
            # state) the same as "couldn't parse", falling through to a LESS
            # authoritative key and, if all three are absent, returning None
            # — which the caller (Coordinator.multi_account_execute) reads as
            # "couldn't determine" and falls back to a MORE PERMISSIVE sizing
            # basis. That's backwards: a genuine 0.0 is the most conservative,
            # most correct answer and must be returned as-is, not papered
            # over. Only an ABSENT or unparseable key should fall through.
            return bp
        return None

    def account_status(self) -> Optional[Dict[str, Any]]:
        """Broker account authorization/health flags, or ``None`` on read failure.

        Reads ``GET /v2/account`` and returns the fields that answer *"can this
        account actually place an order right now?"* — distinct from whether the
        creds merely authenticate for reads:

          ``{status, trading_blocked, account_blocked, trade_suspended_by_user,
             transfers_blocked, shorting_enabled, crypto_status, currency}``

        Motivation (BL-20260701-ALPACA-STATUS-VISIBILITY): when order placement
        returns 401/403 while balance reads succeed, there was no read path
        exposing WHY — the balance()/buying_power() helpers only extract
        ``equity``/``regt_buying_power``. A live account that is restricted (not
        ``ACTIVE``, ``trading_blocked``, ``account_blocked``) reads fine but
        cannot trade; surfacing these flags makes that one diag call instead of
        a code trace. **Read-only** — never places an order. ``None`` on any
        failure so the caller degrades gracefully (missing field → key absent).
        """
        try:
            self._require_creds("account_status")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/account")
        if env.get("retCode") != 0:
            logger.warning("alpaca account_status: %s", env.get("retMsg"))
            return None
        acct = env.get("result") or {}
        keys = (
            "status", "trading_blocked", "account_blocked",
            "trade_suspended_by_user", "transfers_blocked",
            "shorting_enabled", "crypto_status", "currency",
        )
        return {k: acct.get(k) for k in keys if k in acct}

    def positions(self) -> Optional[list]:
        """Open positions as ``[{symbol, side, qty, avg_price, unrealized_pnl}]``.

        Returns ``None`` on a READ FAILURE (missing creds, network error,
        non-2xx) so callers can distinguish "could not read" from "genuinely
        flat" (``[]``) — mirroring the IB read path. Collapsing a failed read
        into ``[]`` is what let a transient Alpaca outage read as "flat" and
        the position-snapshot reconciler false-close a live position
        (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE).
        """
        try:
            self._require_creds("positions")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/positions")
        if env.get("retCode") != 0:
            logger.warning("alpaca positions: %s", env.get("retMsg"))
            return None
        out = []
        for pos in env.get("result") or []:
            try:
                qty = abs(float(pos.get("qty") or 0))
            except (TypeError, ValueError):
                continue
            if qty == 0:
                continue
            out.append(
                {
                    "symbol": pos.get("symbol"),
                    "side": "buy" if str(pos.get("side", "")).lower() == "long" else "sell",
                    "qty": qty,
                    "avg_price": float(pos.get("avg_entry_price") or 0) or None,
                    "unrealized_pnl": float(pos.get("unrealized_pl") or 0),
                    # asset_class ("us_equity" / "us_option") is carried through so a
                    # shared paper account holding BOTH equity shares and option legs
                    # can be filtered per bot-account in account_open_positions
                    # (options-expression isolation; avoids cross-account orphan flap).
                    "asset_class": str(pos.get("asset_class") or "").lower() or None,
                }
            )
        return out

    def _position_raw(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Raw ``/v2/positions/{symbol}`` object, or ``None`` (404 = flat, or read fail).

        Unlike :meth:`positions` this exposes the fields the close path needs to
        gate on — notably ``qty_available`` (shares NOT reserved by a resting
        order). ``None`` is returned both for a genuine 404 (position gone) and
        for a read failure; the caller distinguishes by context (the close loop
        treats ``None`` as "can't confirm held → fall through to the DELETE").
        """
        sym = str(symbol).upper()
        env = self._request("GET", f"/v2/positions/{sym}")
        if env.get("retCode") != 0:
            return None
        result = env.get("result")
        return result if isinstance(result, dict) else None

    def position_present(self, symbol: str) -> Optional[bool]:
        """POSITIVE per-symbol open/flat confirmation via ``GET /v2/positions/{symbol}``.

        Three-valued — the discriminator the reverse reconciler needs before it
        dares close a DB row on *absence from the batch* ``positions()`` list:

          * ``True``  — the position is OPEN on the broker (2xx, non-zero qty).
          * ``False`` — the position is CONFIRMED FLAT (Alpaca returns **404**
                        for a symbol with no open position).
          * ``None``  — could NOT confirm (missing creds / network / any non-404
                        error). The caller must **not** treat this as flat.

        Distinct from :meth:`_position_raw`, which collapses 404 and read-failure
        both into ``None``. Here a 404 is a POSITIVE "flat" signal, so the
        reconciler can require ``is False`` to close: a partial / stale batch
        ``positions()`` LIST that merely omits a still-open symbol no longer
        false-closes it (this per-symbol check returns ``True``), and a read
        failure (``None``) is never a close (RISK-1,
        BL-20260707-ALPACA-PAPER-NEGATIVE-EQUITY / -RECONCILER-MASS-FALSE-CLOSE).
        """
        try:
            self._require_creds("position_present")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        sym = str(symbol).upper()
        env = self._request("GET", f"/v2/positions/{sym}")
        rc = env.get("retCode")
        if rc == 0:
            result = env.get("result")
            if isinstance(result, dict):
                try:
                    qty = abs(float(result.get("qty") or 0))
                except (TypeError, ValueError):
                    qty = 0.0
                return qty > 0
            # 2xx with an unexpected body — cannot positively confirm either way.
            return None
        if rc == 404:
            # Alpaca 404s a symbol it holds no open position for → confirmed flat.
            return False
        # Network (-1) / auth (401/403) / server (5xx) / other — unconfirmed.
        logger.warning(
            "alpaca position_present(%s): unconfirmed (retCode=%s retMsg=%s)",
            sym, rc, env.get("retMsg"),
        )
        return None

    def _await_qty_available(self, symbol: str, want_qty: float, deadline: float) -> Optional[float]:
        """Poll until the position's ``qty_available`` reaches ``want_qty``.

        The deterministic replacement for the blind post-cancel sleep: Alpaca
        drops a cancelled order from ``status=open`` a beat BEFORE it restores
        the position's ``qty_available``, so "open orders cleared" is NOT the
        same signal as "shares released". This waits for the ACTUAL release —
        the exact precondition the flatten DELETE needs — bounded by *deadline*
        (``time.monotonic()``). Returns the last-observed ``qty_available``
        (or ``None`` if the position could not be read / is already gone), so
        the caller can log why it gave up. Fail-open: an unreadable position
        returns immediately (let the DELETE / confirm path decide).
        """
        last: Optional[float] = None
        while True:
            pos = self._position_raw(symbol)
            if pos is None:
                return last  # 404/gone or read fail — don't spin, let DELETE run
            try:
                last = abs(float(pos.get("qty_available") or 0))
            except (TypeError, ValueError):
                last = 0.0
            if last >= want_qty:
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(0.4)

    def close(self, symbol: str) -> Dict[str, Any]:
        """Close the full position on *symbol*; retCode envelope.

        A 404 (no open position) maps to retCode 0 — idempotent close,
        matching the reconciler's expectations.

        Cancels resting orders on the symbol FIRST (e.g. the entry
        bracket's still-open SL/TP legs) before the flatten DELETE —
        mirrors ``IBClient.close``'s cancel-then-close contract and
        ``place_protective``'s cancel-before-re-arm idempotency guard.
        Without this, the resting protective leg still holds the full
        qty as ``held_for_orders`` and Alpaca rejects the flatten with
        "insufficient qty available for order (requested: N,
        available: 0)" — the DB row then never closes and the monitor
        retries the same failing close every tick.

        Bounded post-DELETE FLATTEN CONFIRMATION (BL-20260707-ALPACA-CLOSE-
        NOT-CONFIRMED-FLAT), mirroring ``IBClient.close``'s
        ``IB_CLOSE_CONFIRM_S`` contract exactly: Alpaca returning HTTP 2xx
        for the DELETE means the close order was ACCEPTED, not that the
        position actually flattened. Treating acceptance as success let a
        close accepted right at/after the market close never actually fill —
        the trade was journaled `closed` with a FABRICATED local mark-to-
        market PnL while the position was still fully open (and, because the
        cancel step above already removed its protective bracket, naked) on
        the broker; the reverse reconciler then re-adopted it minutes later
        as a brand-new orphan. So we now re-read the live position and
        require the symbol to actually disappear within ``ALPACA_CLOSE_
        CONFIRM_S`` (default 6.0s; ``<= 0`` restores the legacy accept-is-
        success behaviour) before reporting success. On timeout the retMsg
        carries the exact substring "not confirmed flat" that order_monitor's
        existing IB-built cooldown/retry/consecutive-failure-alert machinery
        already keys on generically (by (account, symbol, direction), not by
        exchange) — so this activates that whole safety net with no changes
        needed anywhere else.
        """
        self._require_creds("close")
        sym = str(symbol).upper()
        n_cancelled = self._cancel_open_orders_for_symbol(symbol)

        # Alpaca order cancels are ASYNCHRONOUS — `DELETE /v2/orders/{id}`
        # returns immediately but the order sits in `pending_cancel` for a
        # moment, during which its shares stay `held_for_orders`. Firing the
        # position-flatten DELETE before the cancels settle RACES them, and
        # Alpaca rejects the flatten with "insufficient qty available for order
        # (requested: N, available: 0)" — the exact spam seen on the 2026-07-07
        # reset-seeded QQQ (BL-20260708-ALPACA-CLOSE-CANCEL-RACE): the protective
        # bracket held all 16 shares, the cancel was in-flight, and every tick's
        # flatten hit available:0 and re-alerted. Wait (bounded) for the symbol's
        # open orders to actually clear before flattening. Only when the pre-pass
        # actually cancelled something (nothing cancelled ⇒ no held shares to
        # release). `ALPACA_CANCEL_SETTLE_S` <= 0 restores fire-immediately.
        settle_s = _env_float("ALPACA_CANCEL_SETTLE_S", 3.0)
        if n_cancelled and settle_s > 0:
            settle_deadline = time.monotonic() + settle_s
            while True:
                # `[]` = cancels settled (shares freed); `None` = read failure
                # (proceed anyway — the post-flatten confirm below is the guard).
                if not self._open_orders_for_symbol(sym):
                    break
                if time.monotonic() >= settle_deadline:
                    break
                time.sleep(0.4)

        # Flatten the position, gating each DELETE on the position's ACTUAL
        # share-release signal (`qty_available`) — deterministic, not a blind
        # timer. Waiting for the cancel to leave the OPEN-ORDERS list (above) is
        # NOT the same signal: Alpaca drops the cancelled order from
        # `status=open` a beat BEFORE it restores `qty_available`, so a DELETE
        # fired on "orders cleared" still races and fails "insufficient qty
        # available for order (requested: N, available: 0)". And a naked-
        # protection re-arm on an INTERVENING monitor tick (no close running →
        # the #5984 same-tick guard doesn't apply) places a FRESH OCO that
        # re-reserves all the shares. The blind re-cancel+sleep retry shipped in
        # PR #5997 did NOT fix the perpetual QQQ #3269 close-failure (verified
        # still failing post-deploy, BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE), so:
        #   1. before each DELETE, WAIT for `qty_available` to actually reach the
        #      position size (the real precondition), re-cancelling any resting
        #      (incl. freshly re-armed) order each pass;
        #   2. if it never releases within the window, return the broker error
        #      AND log the residual open orders so the failure is self-diagnosing
        #      (answers "what is holding the shares?" from the logs, no separate
        #      endpoint needed).
        # `ALPACA_FLATTEN_RETRY_S` <= 0 restores the single-shot DELETE.
        flatten_s = _env_float("ALPACA_FLATTEN_RETRY_S", 6.0)
        flatten_deadline = time.monotonic() + flatten_s
        # Position size we need released before the flatten can fill. Read once;
        # fail-open to 0 (skip the await, let the DELETE + confirm path decide).
        want_qty = 0.0
        if flatten_s > 0:
            _pos0 = self._position_raw(sym)
            if _pos0 is None:
                # Already gone (404) or unreadable — if truly flat the DELETE
                # returns 404 → retCode 0 below.
                pass
            else:
                try:
                    want_qty = abs(float(_pos0.get("qty") or 0))
                except (TypeError, ValueError):
                    want_qty = 0.0
        while True:
            # Wait for the shares to actually be released before flattening.
            if flatten_s > 0 and want_qty > 0:
                self._await_qty_available(sym, want_qty, flatten_deadline)
            env = self._request("DELETE", f"/v2/positions/{sym}")
            if env.get("retCode") == 404:
                return {"retCode": 0, "result": {"note": "no open position"}}
            if env.get("retCode") == 0:
                break
            msg = str(env.get("retMsg") or "").lower()
            held = "insufficient qty" in msg or "available: 0" in msg
            if not held or flatten_s <= 0 or time.monotonic() >= flatten_deadline:
                if held:
                    # Give-up path: surface WHAT is holding the shares so the
                    # "won't flatten" failure is diagnosable from the logs.
                    residual = self._open_orders_for_symbol(sym)
                    logger.warning(
                        "alpaca close %s still insufficient-qty after ~%.1fs "
                        "(want_qty=%s); residual open orders=%s",
                        sym, flatten_s, want_qty, residual,
                    )
                return env
            # A resting order still holds the shares — cancel it (catches a
            # freshly re-armed protective OCO too); the next loop re-awaits the
            # `qty_available` release before retrying the flatten.
            self._cancel_open_orders_for_symbol(sym)

        confirm_s = _env_float("ALPACA_CLOSE_CONFIRM_S", 6.0)
        if confirm_s > 0:
            deadline = time.monotonic() + confirm_s
            last_qty: Optional[float] = None
            flat = False
            while True:
                positions = self.positions()
                # A read failure (None) is NOT treated as flat — we don't
                # know, so keep polling until the deadline rather than
                # confirming a close we can't actually see.
                if positions is not None:
                    match = next(
                        (p for p in positions if p.get("symbol") == sym), None
                    )
                    if match is None:
                        flat = True
                        break
                    last_qty = match.get("qty")
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
            if not flat:
                return {
                    "retCode": 1,
                    "retMsg": (
                        f"alpaca close not confirmed flat: symbol={sym} "
                        f"last_qty={last_qty} after ~{confirm_s}s — the "
                        "flatten was accepted but the position is still "
                        "open; leaving DB row open to retry next tick"
                    ),
                }

        result = env.get("result") or {}
        return {"retCode": 0, "result": {"orderId": str(result.get("id") or "")}}

    def _open_orders_for_symbol(self, symbol: str) -> Optional[list]:
        """Open (working) orders on *symbol*, including bracket child legs.

        Returns the flattened order list (parents + nested ``legs``) filtered
        to *symbol*, or ``None`` on a read failure (so the caller can refuse
        to act rather than assume "no legs"). ``nested=true`` so an
        un-triggered bracket's children come back attached to the parent;
        once the entry fills the legs surface as top-level open orders too.
        """
        sym = str(symbol).upper()
        env = self._request(
            "GET", f"/v2/orders?status=open&nested=true&symbols={sym}"
        )
        if env.get("retCode") != 0:
            return None
        out: list = []
        for o in env.get("result") or []:
            out.append(o)
            for leg in o.get("legs") or []:
                out.append(leg)
        return [o for o in out if str(o.get("symbol") or "").upper() == sym]

    def modify_protective(
        self,
        symbol: str,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Replace the resting SL/TP legs of the open bracket on *symbol*.

        S2 of the live-trade management contract (BL-20260616-LTMGMT-MODIFY).
        Alpaca bracket legs are independent working orders, so a SL/TP modify
        is a leg **replace** (``PATCH /v2/orders/{id}``): the stop leg's
        ``stop_price`` (when *sl* is given) and the limit leg's ``limit_price``
        (when *tp* is given). Only the leg(s) the verdict changed are touched,
        so the other leg's protection is never dropped — no naked re-arm
        window, unlike the IB cancel-and-replace path.

        retCode envelope: ``{"retCode": 0, "result": {"orderId": <last>,
        "patched": [...]}}`` when at least one leg was patched;
        ``{"retCode": <non-zero>, "retMsg": ...}`` on a read failure, a PATCH
        failure, or no matching protective leg found.
        """
        self._require_creds("modify")
        if sl is None and tp is None:
            return {"retCode": -2, "retMsg": "no sl/tp provided — nothing to modify"}
        legs = self._open_orders_for_symbol(symbol)
        if legs is None:
            return {"retCode": -1, "retMsg": "could not read open orders"}
        patched: list = []
        errors: list = []
        for o in legs:
            otype = str(o.get("type") or o.get("order_type") or "").lower()
            oid = o.get("id")
            if not oid:
                continue
            if sl is not None and "stop" in otype:
                env = self._request(
                    "PATCH", f"/v2/orders/{oid}",
                    {"stop_price": f"{float(sl):.2f}"},
                )
                (patched if env.get("retCode") == 0 else errors).append(str(oid))
            elif tp is not None and otype == "limit":
                env = self._request(
                    "PATCH", f"/v2/orders/{oid}",
                    {"limit_price": f"{float(tp):.2f}"},
                )
                (patched if env.get("retCode") == 0 else errors).append(str(oid))
        if errors:
            return {"retCode": 1,
                    "retMsg": f"leg replace failed for orders {errors}"}
        if not patched:
            return {"retCode": 1,
                    "retMsg": "no matching protective leg found for "
                              f"{str(symbol).upper()}"}
        return {"retCode": 0,
                "result": {"orderId": patched[-1], "patched": patched}}

    # --------------------------------------------------- naked re-arm (GTC OCO)
    def has_protective_orders(self, symbol: str) -> Optional[bool]:
        """Does *symbol* have a resting protective leg (a stop OR a limit) open?

        For the naked-position sweep on an EQUITY account: the journal row keeps
        its sl/tp, but the broker-side day-TIF bracket legs are cancelled at the
        RTH close — so naked-ness is BROKER-side and invisible to the DB-driven
        naked check. Returns ``True`` if at least one resting stop/limit leg
        exists, ``False`` if the position is broker-naked, or ``None`` on a read
        failure (so the caller refuses to act rather than re-arm blindly / treat
        a transient outage as naked — mirrors the ``positions() -> None`` rule).
        """
        legs = self._open_orders_for_symbol(symbol)
        if legs is None:
            return None
        for o in legs:
            otype = str(o.get("type") or o.get("order_type") or "").lower()
            if "stop" in otype or otype == "limit":
                return True
        return False

    def _cancel_open_orders_for_symbol(self, symbol: str) -> int:
        """Cancel every resting order on *symbol* (best-effort, never raises).

        Returns the number of distinct orders a cancel was ISSUED for (0 when
        there were none) so ``close()`` can skip its cancel-settle wait when
        nothing was held. Callers that don't care about the count (e.g.
        ``place_protective``'s re-arm pre-pass) can ignore it.

        Called before placing a fresh protective OCO so repeated re-arms can't
        STACK multiple live OCO groups on the one position — the same
        accumulation guard ``IBClient.place_protective`` applies before each
        re-arm (BL-20260624-MHG-FLIP). A cancel failure must not block arming
        protection on a live naked position.
        """
        legs = self._open_orders_for_symbol(symbol)
        if not legs:
            return 0
        seen: set = set()
        for o in legs:
            oid = o.get("id")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            try:
                self._request("DELETE", f"/v2/orders/{oid}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "alpaca _cancel_open_orders_for_symbol(%s): cancel %s "
                    "failed: %s", symbol, oid, exc,
                )
        return len(seen)

    def place_protective(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Attach a **GTC OCO** SL/TP to an ALREADY-OPEN position (no entry).

        The re-arm counterpart to ``IBClient.place_protective`` for Alpaca
        (BL-20260629-ALPACA-NAKED-BRACKET). The entry bracket's protective legs
        are ``time_in_force: day`` (an Alpaca market-entry-bracket constraint)
        so they are CANCELLED at the RTH close — a multi-session ETF hold then
        sits broker-naked. This places a fresh **GTC** OCO (one-cancels-other:
        a take-profit limit + a stop, ``time_in_force: gtc``) on the
        closing side so protection persists across closes/weekends/restarts.

        Cancels any resting orders on the symbol first (no OCO stacking). Needs
        BOTH ``sl`` and ``tp`` (an OCO is a limit+stop pair). ``order`` keys:
        ``symbol``, ``direction`` (the POSITION side, ``long``/``short`` or
        ``buy``/``sell`` — the OCO takes the reverse, closing side), ``qty``
        (whole shares), ``sl``, ``tp``. Envelope mirrors :meth:`place`.

        Alpaca OCO request schema (verified live 2026-06-29): ``order_class:
        oco``, ``type: limit``, ``time_in_force: gtc``, and BOTH legs as nested
        objects — ``take_profit.limit_price`` + ``stop_loss.stop_price`` (a
        top-level ``limit_price`` is refused: "oco orders require
        take_profit.limit_price"). Fail-safe: a refusal returns a non-zero
        envelope and the naked sweep simply retries next tick.
        """
        self._require_creds("place_protective")
        direction = str(order.get("direction") or order.get("side") or "").lower()
        if direction in ("buy", "long"):
            pos_long = True
        elif direction in ("sell", "short"):
            pos_long = False
        else:
            return {"retCode": -2, "retMsg": f"invalid direction {direction!r}"}
        tp, sl = order.get("tp"), order.get("sl")
        if tp is None or sl is None:
            return {"retCode": -2,
                    "retMsg": "OCO needs both sl and tp (got "
                              f"sl={sl!r} tp={tp!r})"}
        try:
            qty = max(1, int(round(float(order["qty"]))))
        except (KeyError, TypeError, ValueError) as exc:
            return {"retCode": -2, "retMsg": f"invalid qty: {exc}"}
        sym = str(order["symbol"]).upper()
        close_side = "sell" if pos_long else "buy"
        # Idempotency: clear any resting legs first so re-arms don't stack OCOs.
        self._cancel_open_orders_for_symbol(sym)
        body: Dict[str, Any] = {
            "symbol": sym,
            "qty": str(qty),
            "side": close_side,
            "type": "limit",
            "time_in_force": "gtc",
            "order_class": "oco",
            # OCO legs (Alpaca requires BOTH as nested objects, NOT a top-level
            # limit_price — verified live 2026-06-29: a top-level limit_price was
            # refused with "oco orders require take_profit.limit_price"). The
            # take-profit limit + the stop one-cancels-other; type=limit is the
            # OCO's primary leg.
            "take_profit": {"limit_price": f"{float(tp):.2f}"},
            "stop_loss": {"stop_price": f"{float(sl):.2f}"},
        }
        env = self._request("POST", "/v2/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        return {"retCode": 0, "result": {"orderId": str(result.get("id") or "")}}
