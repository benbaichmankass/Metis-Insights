from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def pure_pandas_atr(high, low, close, length=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def turtle_soup_signal(df: pd.DataFrame, rr: float = 2.2):
    df = df.copy()
    df['hour_utc'] = df.index.hour
    df['atr'] = pure_pandas_atr(df['high'], df['low'], df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']

    lookback = 12
    killzone = df['hour_utc'].between(0, 23)

    df['prior_low'] = df['low'].rolling(lookback).min().shift(1)
    df['prior_high'] = df['high'].rolling(lookback).max().shift(1)

    df['ts_long'] = (
        (df['low'] < df['prior_low']) &
        (df['close'] > df['prior_low']) &
        killzone &
        (df['close'] > df['open']) &
        (df['vol_ratio'] > 1.1)
    )

    df['ts_short'] = (
        (df['high'] > df['prior_high']) &
        (df['close'] < df['prior_high']) &
        killzone &
        (df['close'] < df['open']) &
        (df['vol_ratio'] > 1.1)
    )

    if len(df) < lookback + 1:
        return None, None, None

    row = df.iloc[-1]

    if bool(row['ts_long']) and pd.notna(row['atr']):
        entry_price = float(row['close'])
        atr_stop = 1.4 * float(row['atr'])
        stop_price = entry_price - atr_stop
        target_price = entry_price + (atr_stop * rr)
        return 'long', entry_price, {'stop_price': stop_price, 'target_price': target_price}

    if bool(row['ts_short']) and pd.notna(row['atr']):
        entry_price = float(row['close'])
        atr_stop = 1.4 * float(row['atr'])
        stop_price = entry_price + atr_stop
        target_price = entry_price - (atr_stop * rr)
        return 'short', entry_price, {'stop_price': stop_price, 'target_price': target_price}

    return None, None, None


class KillZoneScalperBot:
    def __init__(self, exchange, symbol='BTC/USDT:USDT'):
        self.exchange = exchange
        self.symbol = symbol

    def _fetch_ohlcv_df(self, limit: int = 250) -> pd.DataFrame:
        candles_df = self.exchange.get_ohlcv(self.symbol, timeframe='1h', limit=limit)
        if candles_df is None:
            return pd.DataFrame(columns=['open','high','low','close','volume'])
        if 'timestamp' in candles_df.columns:
            candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'], utc=True)
            candles_df = candles_df.set_index('timestamp')
        return candles_df[['open','high','low','close','volume']]
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df.set_index('timestamp')
        return df

    def analyze_market(self) -> Tuple[Optional[str], Optional[float], Optional[dict]]:
        try:
            df = self._fetch_ohlcv_df()
            signal, price, meta = turtle_soup_signal(df)
            if signal:
                logger.info('Turtle Soup signal generated: %s at %s', signal, price)
            else:
                logger.info('No Turtle Soup signal this tick.')
            return signal, price, meta
        except Exception as exc:
            logger.exception('analyze_market failed: %s', exc)
            return None, None, None


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    )
    logger.info('src.core.automated_trading_loop is now a strategy module. Run python -m src.main for the runtime loop.')


if __name__ == '__main__':
    main()
