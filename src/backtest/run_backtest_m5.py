"""Subprocess entry point for the M5 strategy-test consumer (P2).

The P1 consumer ran ``ICTBacktester`` inline; that's safe for the
fixture CSV but a multi-MB production CSV would block the comms
poll loop for the duration of the run. P2 spawns this script in a
subprocess so the poller stays responsive and we get a hard wall
clock (``M5_BACKTEST_TIMEOUT_S``).

Contract:

  * argv: ``run_backtest_m5 <strategy>``.
  * env: ``BACKTEST_DATA_PATH`` (optional, candle CSV); ``TRADE_JOURNAL_DB``
    (optional, sqlite path).
  * stdout (success): one line of JSON ``{"db_row_id": int,
    "summary": {...}}`` — the consumer parses the LAST line of stdout
    so the underlying scripts can still ``print`` informational
    output without breaking the contract.
  * stderr: any error message; non-zero exit code on failure.
  * exit codes: ``0`` ok, ``1`` runtime failure (exception),
    ``2`` usage error.

Reuses the helpers from ``src.backtest.run_backtest`` so the
single-source-of-truth summary/persist logic lives in one place.
"""
from __future__ import annotations

import json
import sys
import traceback


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print("usage: run_backtest_m5 <strategy>", file=sys.stderr)
        return 2
    strategy = argv[1].strip()

    try:
        # Lazy-imported so a pure ``--help`` invocation doesn't pay
        # the pandas/sqlite import cost.
        from src.backtest.backtester import ICTBacktester
        from src.backtest.run_backtest import load_data, summarize
        from src.units.db.database import Database

        df, source_path = load_data()
        bt = ICTBacktester(df, {})
        trades = bt.run()
        start_date = str(df["timestamp"].iloc[0].date())
        end_date = str(df["timestamp"].iloc[-1].date())
        summary = summarize(trades, start_date, end_date, strategy)
        summary["data_source"] = str(source_path)

        # Database.save_backtest_results inserts every key it gets,
        # so strip the data-source path before writing — keep it on
        # the in-memory summary so the validation log records it.
        persistable = {k: v for k, v in summary.items() if k != "data_source"}
        db = Database()  # canonical resolver — never the bare-CWD fallback
        row_id = db.save_backtest_results(persistable)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return 1

    # Always the LAST line of stdout — consumer parses it as the
    # result envelope. Earlier lines (e.g. backtester progress
    # prints) are tolerated.
    print(json.dumps({"db_row_id": int(row_id), "summary": summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
