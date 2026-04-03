import os
import pandas as pd
from src.backtest.backtester import ICTBacktester
from alert_manager import AlertManager

CURRENT_VERSION_NAME = "v1_baseline"
CURRENT_CONFIG = {}

NEW_VERSION_NAME = "v2_test"
NEW_CONFIG = {}

DATA_FILE = os.path.expanduser("~/ict-trading-bot/data/bybit_btcusdt_1m.csv")
NUM_PERIODS = 5
DAYS_PER_PERIOD = 5

def run_version(name, config, period_df):
    bt = ICTBacktester(period_df.copy(), config)
    results = bt.run()
    if isinstance(results, dict):
        return float(results.get("total_pnl", 0))
    return 0.0

def main():
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"Data file not found: {DATA_FILE}")

    df = pd.read_csv(DATA_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    start_ts = df["timestamp"].min()
    end_ts = df["timestamp"].max()

    print(f"Data range: {start_ts} -> {end_ts}")
    print(f"Rows: {len(df)}")

    total_days = (end_ts - start_ts).days
    if total_days < DAYS_PER_PERIOD:
        raise ValueError("Not enough data for even one test period")

    periods = []
    cursor = start_ts

    while cursor + pd.Timedelta(days=DAYS_PER_PERIOD) <= end_ts and len(periods) < NUM_PERIODS:
        p_start = cursor
        p_end = cursor + pd.Timedelta(days=DAYS_PER_PERIOD)
        periods.append((p_start, p_end))
        cursor = p_end

    if not periods:
        raise ValueError("No valid periods created from available data")

    alert_manager = AlertManager()

    v1_wins = 0
    v2_wins = 0
    v1_total = 0.0
    v2_total = 0.0

    for i, (p_start, p_end) in enumerate(periods, 1):
        print(f"Running period {i}/{len(periods)}: {p_start} -> {p_end}")

        period_df = df[(df["timestamp"] >= p_start) & (df["timestamp"] < p_end)].copy()

        if period_df.empty:
            print("  Skipping empty period")
            continue

        v1_pnl = run_version(CURRENT_VERSION_NAME, CURRENT_CONFIG, period_df)
        v2_pnl = run_version(NEW_VERSION_NAME, NEW_CONFIG, period_df)

        v1_total += v1_pnl
        v2_total += v2_pnl

        print(f"  {CURRENT_VERSION_NAME} PnL: {v1_pnl}")
        print(f"  {NEW_VERSION_NAME} PnL: {v2_pnl}")

        if v1_pnl > v2_pnl:
            v1_wins += 1
        elif v2_pnl > v1_pnl:
            v2_wins += 1

    print("\nFINAL SUMMARY\n")
    print(f"{CURRENT_VERSION_NAME} wins: {v1_wins}")
    print(f"{NEW_VERSION_NAME} wins: {v2_wins}")
    print()
    print(f"{CURRENT_VERSION_NAME} total PnL: {v1_total}")
    print(f"{NEW_VERSION_NAME} total PnL: {v2_total}")
    print()

    recommendation = CURRENT_VERSION_NAME if v1_total >= v2_total else NEW_VERSION_NAME
    print(f"Recommendation: Keep {recommendation}")

    try:
        alert_manager.send_alert(
            f"Comparison complete. {CURRENT_VERSION_NAME}: {v1_total}, {NEW_VERSION_NAME}: {v2_total}. Keep {recommendation}"
        )
    except Exception as e:
        print(f"⚠️ Alert failed: {e}")

if __name__ == "__main__":
    main()
