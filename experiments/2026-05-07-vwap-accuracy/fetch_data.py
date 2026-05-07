"""Pre-fetch BTCUSDT 5m + 4h candles for the 5m re-run.

Uses the existing ``scripts.training.data_loader.load_candles`` chain
(yfinance → Coinbase → Bybit). Run from a host with outbound internet
(GitHub Actions runner). Caches the parquet under
``results/5m/_cache/`` so ``run_5m.py`` finds them.

The 4h series for H3 is built by resampling 1h candles, since the
loader supports 1h but not 4h directly.
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.training.data_loader import load_candles  # noqa: E402

CACHE = Path(__file__).resolve().parent / "results" / "5m" / "_cache"
DAYS_5M = 365
DAYS_1H = 365


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)

    print(f"Fetching BTCUSDT 5m for {DAYS_5M}d...")
    df_5m = load_candles("BTCUSDT", "5m", DAYS_5M, CACHE)
    print(
        f"  5m: {len(df_5m)} bars "
        f"({df_5m['timestamp'].min()} → {df_5m['timestamp'].max()})"
    )

    print(f"Fetching BTCUSDT 1h for {DAYS_1H}d (for 4h resample)...")
    df_1h = load_candles("BTCUSDT", "1h", DAYS_1H, CACHE)
    print(
        f"  1h: {len(df_1h)} bars "
        f"({df_1h['timestamp'].min()} → {df_1h['timestamp'].max()})"
    )

    df_4h = (
        df_1h.set_index("timestamp")
        .resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    actual_days = (df_5m["timestamp"].max() - df_5m["timestamp"].min()).days or 1
    out_4h = CACHE / f"BTCUSDT_4h_{actual_days}d.parquet"
    df_4h.to_parquet(out_4h)
    print(f"  4h: {len(df_4h)} bars → {out_4h.name}")

    print("\nCache contents:")
    for p in sorted(CACHE.glob("*.parquet")):
        print(f"  {p.name} ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
