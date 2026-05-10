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
        """Set per-symbol leverage on Bybit V5 before placing linear perp orders.

        PR 3 (spot-margin → perpetuals cutover): linear perpetuals require
        ``/v5/position/set-leverage`` to be set once per (symbol, account)
        before any order. Bybit returns retCode=110043 ("leverage not
        modified") when the value is already what we asked for — this is
        idempotent success, not an error, so callers can re-invoke on every
        boot without consequence. Other retCodes propagate.

        Unified Trading Account (UTA): set buyLeverage and sellLeverage
        symmetrically; both must equal the same value or Bybit rejects.

        Args:
            symbol: Bybit symbol (e.g., "BTCUSDT" — no slash for V5).
            leverage: Integer leverage (1-100). 3x is the operator default.
            category: "linear" for USDT-margined perps. "inverse" not used.

        Returns:
            dict: ccxt response with ``retCode`` / ``retMsg``. Caller should
                  treat retCode in (0, 110043) as success.
        """
        try:
            # ccxt exposes set_leverage as ``set_leverage(leverage, symbol, params)``.
            # The V5 endpoint requires buyLeverage + sellLeverage as strings.
            params = {
                "category": category,
                "buyLeverage": str(int(leverage)),
                "sellLeverage": str(int(leverage)),
            }
            resp = self.exchange.set_leverage(int(leverage), symbol, params=params)
            mode = "TESTNET" if self.testnet else "LIVE"
            logger.info(
                "[%s] set_leverage: %s x%d (category=%s) -> %s",
                mode, symbol, leverage, category, resp,
            )
            return resp
        except Exception as e:
            # Idempotent case: ccxt may raise on retCode=110043 ("leverage
            # not modified"). Detect by message substring and surface as
            # success — re-applying the same leverage every boot is normal.
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


if __name__ == "__main__":
    print("BybitConnector loaded")
    print(f"Testnet from env: {_read_testnet_flag()}")
