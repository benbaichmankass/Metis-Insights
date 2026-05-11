import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict

import ccxt
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# NOTE — dual-library design (intentional):
#   BybitConnector (this file) uses ccxt for market data + order placement.
#   telegram_query_bot.py uses pybit for wallet balance + positions display.
#   Both read the same BYBIT_API_KEY / BYBIT_API_SECRET env vars.
#   BYBIT_TESTNET=true  -> sandbox mode
#   BYBIT_TESTNET=false -> live mode (default if var is missing)


def _read_testnet_flag() -> bool:
    raw = os.getenv("BYBIT_TESTNET", "false").strip().lower()
    return raw == "true"


class BybitConnector:
    """
    Bybit connector for Unified Trading Account.
    Works with Cross Margin and linear perpetual contracts.

    Testnet / live mode is controlled by the BYBIT_TESTNET environment
    variable.  Set BYBIT_TESTNET=false in .env.live for live trading.
    If testnet param is omitted, the env var is read.
    """

    def __init__(self, api_key=None, api_secret=None, testnet=None):
        if testnet is None:
            testnet = _read_testnet_flag()
        self.testnet = testnet

        self.exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

        if testnet:
            self.exchange.set_sandbox_mode(True)

        # Operator directive 2026-05-03 — dry/live mode is per-account
        # (config/accounts.yaml `mode`, applied via RiskManager.dry_run).
        # The connector itself doesn't gate on a process-level flag.
        logger.info("Bybit market data environment: %s", "testnet" if testnet else "mainnet")

    def get_price(self, symbol="BTC/USDT:USDT"):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker["last"]
        except Exception as e:
            print(f"Error fetching price: {e}")
            return None

    def get_ohlcv(self, symbol="BTC/USDT:USDT", timeframe="15m", limit=100):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"Error fetching OHLCV: {e}")
            msg = str(e)
            if "Rate Limit" in msg or "Too many visits" in msg or 'retCode":10006' in msg:
                import time
                print("Rate limit hit, sleeping 15s...")
                time.sleep(15)
            return None

    def get_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            print(f"Error fetching balance: {e}")
            return None

    def get_positions(self):
        """Return only positions with non-zero size.

        On Bybit's Unified Trading Account (UTA), linear perpetuals require
        params={"category": "spot"} so ccxt routes to the v5 /position/list
        endpoint for the correct contract type.  Without this explicit param,
        ccxt may fall back to the spot endpoint (even with defaultType=linear
        set at construction time) and return an empty list for open perpetual
        positions.  The contracts > 0 filter matches the Binance connector's
        schema exactly.
        """
        try:
            positions = self.exchange.fetch_positions(params={"category": "spot"})
            return [p for p in positions if float(p.get("contracts", 0) or 0) > 0]
        except Exception as e:
            logger.warning("Bybit: error fetching positions — %s", e)
            return []

    def place_market_order(self, symbol, side, amount, params=None):
        try:
            if params is None:
                params = {}
            order = self.exchange.create_market_order(symbol=symbol, side=side, amount=amount, params=params)
            mode = "TESTNET" if self.testnet else "LIVE"
            print(f"[{mode}] Market {side.upper()}: {amount} {symbol}")
            return order
        except Exception as e:
            print(f"Error placing order: {e}")
            return None

    def set_leverage(self, symbol, leverage, category="linear"):
        """Set per-symbol leverage on Bybit V5 via a direct signed POST.

        History:

        * PR 3 (2026-05-10): ccxt's high-level ``exchange.set_leverage(
          leverage, symbol, params={...})`` → retCode=10003 every call.
        * PR 4 (2026-05-10): ccxt's auto-generated
          ``private_post_v5_position_set_leverage`` → retCode=10003 too.
        * 2026-05-11: operator confirmed the API key has every relevant
          permission (Contracts > Orders Positions, Unified Trading >
          Trade, etc.). Same client object successfully places orders
          via ``create_market_order`` — the auth IS valid for the order
          endpoints. The failure is specific to ccxt's routing of the
          set-leverage path. Suspected cause: ccxt's
          ``private_post_v5_*`` auto-generation falls back to a non-V5
          signer for this specific endpoint, producing a 10003 from
          Bybit's V5 path-aware auth.

        This implementation skips ccxt entirely for set-leverage and
        signs a direct POST to ``/v5/position/set-leverage`` per Bybit's
        V5 spec (HMAC-SHA256, X-BAPI-SIGN-TYPE: 2). The signing uses
        the same apiKey/secret ccxt is configured with — that auth is
        proven to work for V5 because ``fetch_positions`` (V5 GET) and
        ``create_market_order`` (V5 POST) both succeed against the same
        client. Idempotent on retCode=110043 (leverage already matches).

        UTA contract: buyLeverage and sellLeverage must be the same
        string-typed integer value or Bybit rejects with 110044.

        Args:
            symbol: Bybit symbol (e.g., "BTCUSDT" — no slash for V5).
            leverage: Integer leverage (1-100). 3x is the operator default.
            category: "linear" for USDT-margined perps. "inverse" not used.

        Returns:
            dict: Bybit V5 response. retCode==0 → newly set; ==110043
                  → already set (treated as success). Other retCodes
                  raise.
        """
        try:
            resp = self._v5_signed_post(
                "/v5/position/set-leverage",
                {
                    "category": category,
                    "symbol": symbol,
                    "buyLeverage": str(int(leverage)),
                    "sellLeverage": str(int(leverage)),
                },
            )
            ret_code = None
            ret_msg = None
            if isinstance(resp, dict):
                ret_code = resp.get("retCode")
                try:
                    ret_code = int(ret_code) if ret_code is not None else None
                except (TypeError, ValueError):
                    pass
                ret_msg = resp.get("retMsg")
            mode = "TESTNET" if self.testnet else "LIVE"
            if ret_code in (0, 110043):
                logger.info(
                    "[%s] set_leverage: %s x%d (category=%s) retCode=%s msg=%s",
                    mode, symbol, leverage, category, ret_code, ret_msg,
                )
                return resp
            raise RuntimeError(
                f"Bybit set-leverage rejected: retCode={ret_code} retMsg={ret_msg} "
                f"full={resp}"
            )
        except Exception as e:
            msg = str(e)
            if "110043" in msg or "leverage not modified" in msg.lower():
                logger.info(
                    "set_leverage: %s x%d already set (retCode=110043, idempotent)",
                    symbol, leverage,
                )
                return {"retCode": 110043, "retMsg": "leverage not modified"}
            logger.warning(
                "Bybit: set_leverage(%s, x%d) failed — %s", symbol, leverage, e,
            )
            raise

    # ── V5 direct-call helper (Bybit auth spec, no ccxt routing) ──
    #
    # ccxt's auto-generated bybit private_post_v5_* methods have a
    # historic class of bugs around path-aware signing for the V5
    # namespace — set-leverage hits one of them. Rather than waiting on
    # an upstream ccxt fix, sign the V5 request directly per Bybit's
    # published auth spec:
    #   https://bybit-exchange.github.io/docs/v5/guide
    #
    # Spec for POST:
    #   sign_string = timestamp + api_key + recv_window + json_body
    #   signature   = hex(HMAC-SHA256(secret, sign_string))
    # Headers:
    #   X-BAPI-API-KEY     <key>
    #   X-BAPI-SIGN-TYPE   2          (HMAC-SHA256)
    #   X-BAPI-TIMESTAMP   <ms>
    #   X-BAPI-RECV-WINDOW 5000
    #   X-BAPI-SIGN        <signature>
    #   Content-Type       application/json
    #
    # The body MUST be the exact same JSON string we signed — any
    # whitespace difference invalidates the signature. We use
    # ``json.dumps(..., separators=(",", ":"), sort_keys=False)`` to
    # produce a canonical compact form and reuse that string for both
    # signing and the HTTP body.

    _V5_RECV_WINDOW = "5000"

    def _v5_signed_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST ``path`` (e.g. ``/v5/position/set-leverage``) to Bybit
        with a manually-signed V5 request. Returns parsed JSON.
        """
        api_key = self.exchange.apiKey or ""
        secret = self.exchange.secret or ""
        if not api_key or not secret:
            raise RuntimeError(
                "Bybit V5 direct call requires apiKey + secret on the "
                "ccxt client. Got "
                f"apiKey={'set' if api_key else 'missing'} "
                f"secret={'set' if secret else 'missing'}"
            )
        body = json.dumps(payload, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        sign_string = timestamp + api_key + self._V5_RECV_WINDOW + body
        signature = hmac.new(
            secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        host = (
            "https://api-testnet.bybit.com"
            if self.testnet
            else "https://api.bybit.com"
        )
        url = host + path
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": self._V5_RECV_WINDOW,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }
        resp = requests.post(url, data=body, headers=headers, timeout=10)
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError(
                f"Bybit V5 {path}: non-JSON response (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )


if __name__ == "__main__":
    print("BybitConnector loaded")
    print(f"Testnet from env: {_read_testnet_flag()}")
