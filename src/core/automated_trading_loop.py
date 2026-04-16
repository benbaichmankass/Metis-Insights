import pandas as pd

def pure_pandas_atr(high, low, close, length=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def turtle_soup_production(
    df: pd.DataFrame,
    risk_pct: float = 0.01,
    rr: float = 2.2
) -> list[dict]:
    """
    Production Turtle Soup - Iteration #5
    Pure pandas version for VM compatibility
    """
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

    trades = []
    in_trade = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    target_price = 0.0

    for i in range(lookback, len(df)):
        row = df.iloc[i]

        if not in_trade:
            if bool(row['ts_long']) and pd.notna(row['atr']):
                side = 1
                entry_price = float(row['close'])
                atr_stop = 1.4 * float(row['atr'])
                stop_price = entry_price - atr_stop
                target_price = entry_price + (atr_stop * rr)
                in_trade = True

            elif bool(row['ts_short']) and pd.notna(row['atr']):
                side = -1
                entry_price = float(row['close'])
                atr_stop = 1.4 * float(row['atr'])
                stop_price = entry_price + atr_stop
                target_price = entry_price - (atr_stop * rr)
                in_trade = True

        else:
            hit_stop = (side * (float(row['low']) - stop_price)) <= 0
            hit_target = (side * (float(row['high']) - target_price)) >= 0

            if hit_stop or hit_target:
                pnl_r = rr if hit_target else -1
                pnl_pct = pnl_r * risk_pct

                trades.append({
                    'timestamp': df.index[i],
                    'side': 'long' if side == 1 else 'short',
                    'entry_price': entry_price,
                    'stop_price': stop_price,
                    'target_price': target_price,
                    'exit_price': float(row['close']),
                    'pnl_pct': pnl_pct,
                    'r_multiple': pnl_r,
                    'exit_reason': 'target' if hit_target else 'stop'
                })
                in_trade = False

    return trades
