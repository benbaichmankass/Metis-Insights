
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import ccxt
import pandas as pd
import yaml


def fetch_btc_ohlcv(days_back=30):
    config_path = Path('ml/config/v1_btc_breakout.yaml')
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)

    exchange = ccxt.bybit({
        'enableRateLimit': True,
    })

    symbol = cfg['symbol']
    timeframe = cfg['timeframe']
    output_file = cfg['raw_data_file']

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp() * 1000)

    all_rows = []
    current_since = since_ms
    limit_per_call = 1000

    while True:
        batch = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=current_since,
            limit=limit_per_call,
            params={'category': 'linear'}
        )

        if not batch:
            break

        all_rows.extend(batch)

        last_ts = batch[-1][0]
        next_since = last_ts + 60_000

        if next_since <= current_since:
            break

        current_since = next_since

        if len(batch) < limit_per_call:
            break

        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    df['datetime_utc'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_file, index=False)

    print(f'Saved {len(df)} rows to {output_file}')
    return df


if __name__ == '__main__':
    fetch_btc_ohlcv(days_back=30)
