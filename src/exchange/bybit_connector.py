import logging
import os
import time

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

    def __init__(self, api_key=None, api_secret=None, testnet=None,
                 market_type="linear"):
        if testnet is None:
            testnet = _read_testnet_flag()
        self.testnet = testnet
        # The account's config/accounts.yaml ``market_type`` — decides the V5
        # ``category`` for position reads (see get_positions). Default
        # ``linear``: every Bybit account in config/accounts.yaml is linear
        # (bybit_1, bybit_2, bybit_portfolio; 2026-10-01).
        self.market_type = market_type

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

    def get_ohlcv(self, symbol="BTC/USDT:USDT", timeframe="15m", limit=100, since=None):
        # ``since`` (epoch MILLISECONDS, CCXT convention) fetches candles FORWARD
        # from that time — the historical-range read the M30 P5 exit panel needs
        # to reconstruct MFE/MAE over a closed trade's holding window. Default
        # None = the exchange's most-recent ``limit`` candles (unchanged behaviour).
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"Error fetching OHLCV: {e}")
            msg = str(e)
            if "Rate Limit" in msg or "Too many visits" in msg or 'retCode":10006' in msg:
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

        The V5 ``category`` comes from this connector's ``market_type``, via
        the same resolver the order path uses (``execute._bybit_category``).
        It was hardcoded ``"spot"`` — for a linear-perp account that is the
        wrong book, so the read silently returned ``[]``
        (AUD-20260927-CA-A15-bybit-positions-spot-category, ORDER-AUDIT-2
        item 6). Spot has no positions on V5 (a spot holding is a wallet
        balance), so a spot connector returns ``[]`` without a call.

        A failed read RAISES. It used to return ``[]``, which made "could not
        read" and "no positions" the same answer; the only consumer
        (``risk_counters.inject_runtime_counters``) already catches, reports,
        and leaves the counter absent. The contracts > 0 filter matches the
        Binance connector's schema exactly.
        """
        from src.units.accounts.execute import _bybit_category
        category = _bybit_category({"market_type": self.market_type})
        if category == "spot":
            return []
        positions = self.exchange.fetch_positions(params={"category": category})
        return [p for p in positions if float(p.get("contracts", 0) or 0) > 0]

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



if __name__ == "__main__":
    print("BybitConnector loaded")
    print(f"Testnet from env: {_read_testnet_flag()}")
