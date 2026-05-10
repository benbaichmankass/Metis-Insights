import logging
import os
import ccxt
import pandas as pd

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
        """Set per-symbol leverage on Bybit V5 via the V5 endpoint directly.

        PR 4 fix (2026-05-10): PR 3's first take used ccxt's high-level
        ``exchange.set_leverage(leverage, symbol, params={...})``. On
        unified-trading-account (UTA) keys, that path failed every call
        with ``retCode=10003 "API key is invalid."`` — not because the
        key was actually invalid (orders placed seconds later against
        the same client object succeeded), but because ccxt's wrapper
        routed the request through a non-V5 endpoint that Bybit signs
        differently. The result: the helper was a no-op, the trader
        used whatever leverage was last set on the account from the
        Bybit UI (could have been anything from 1× to 100×), and the
        operator had no way to enforce a policy value.

        This implementation calls the V5 endpoint directly via ccxt's
        auto-generated ``private_post_v5_position_set_leverage``
        method, which signs against the right path. Idempotent on
        retCode=110043 (leverage already matches).

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
            # private_post_v5_position_set_leverage is auto-generated
            # by ccxt from the bybit API definition. ccxt signs against
            # the V5 path correctly, which avoids the retCode=10003
            # rejection the high-level set_leverage() helper triggers.
            resp = self.exchange.private_post_v5_position_set_leverage({
                "category": category,
                "symbol": symbol,
                "buyLeverage": str(int(leverage)),
                "sellLeverage": str(int(leverage)),
            })
            ret_code = None
            ret_msg = None
            if isinstance(resp, dict):
                ret_code = resp.get("retCode")
                # Some ccxt versions normalise retCode to int, others
                # leave it as string. Coerce.
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
            # Anything else is a real failure. Don't swallow.
            raise RuntimeError(
                f"Bybit set-leverage rejected: retCode={ret_code} retMsg={ret_msg} "
                f"full={resp}"
            )
        except Exception as e:
            msg = str(e)
            # Idempotent case can also arrive as an exception from ccxt
            # depending on version. Detect by retCode substring and
            # surface as success — re-applying the same leverage on
            # every boot is the expected pattern.
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


if __name__ == "__main__":
    print("BybitConnector loaded")
    print(f"Testnet from env: {_read_testnet_flag()}")
