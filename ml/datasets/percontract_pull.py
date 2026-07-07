"""Per-contract IBKR pull writer (roll-adjustment increment 2).

Runs ON THE LIVE VM (the only host on the IB-Gateway's private subnet) — the
same deployment as the `market_raw` pull, driven by
`scripts/ops/pull_mes_ibkr_history.sh` with `PER_CONTRACT=1`. Where that script
normally runs `python -m ml build-dataset market_raw` (which dedups across
contracts into a canonical `market_raw` shard), the per-contract mode runs THIS
module instead: it calls `IBKRHistoricalMarketRawAdapter.iter_contract_bars`
(no cross-contract dedup, each bar tagged with its `contract` month) and writes
a flat jsonl to a SEPARATE artifact family:

    <out_dir>/market_raw_percontract/<SYM>/<tf>/<ver>/data.jsonl

That per-contract stream is the input to
`scripts/research/build_continuous_contract.py` (via
`ml/datasets/continuous.py::group_bars_by_contract`), which back-adjusts it into
a continuous `market_raw` series a breakout/trend backtest can trust.

It is NOT a `market_raw` shard (it carries the extra `contract` key), so it
deliberately does not go through the `market_raw` builder / its schema check.

Guarded by `ICT_IB_HISTORICAL_OK=1` (the adapter's opt-in) exactly like the
`market_raw` pull; the shell sets it. Tests monkeypatch `iter_contract_bars`,
so this never opens a socket in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable, Mapping

from .adapters.ibkr_offvm import IBKRHistoricalMarketRawAdapter


def write_percontract_jsonl(
    rows: Iterable[Mapping[str, Any]],
    out_path: str,
) -> int:
    """Write per-contract rows to `out_path` (one JSON object per line).

    Returns the row count written. Creates parent dirs. The rows are whatever
    `iter_contract_bars` yields (ts/contract/symbol/timeframe/OHLCV/source).
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    n = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(dict(r)) + "\n")
            n += 1
    return n


def pull_and_write(
    *,
    symbol: str,
    timeframe: str,
    start: str,
    end: str | None,
    out_dir: str,
    version: str,
    host: str,
    port: int,
    client_id: int,
    pause_s: float,
    max_contracts: int,
    use_rth: bool = False,
    adapter: IBKRHistoricalMarketRawAdapter | None = None,
) -> tuple[str, int]:
    """Pull per-contract bars and write the shard. Returns (out_path, rows)."""
    adapter = adapter or IBKRHistoricalMarketRawAdapter()
    out_path = os.path.join(
        out_dir, "market_raw_percontract", symbol, timeframe, version, "data.jsonl"
    )
    rows = adapter.iter_contract_bars(
        symbol=symbol, timeframe=timeframe, start=start, end=end,
        host=host, port=int(port), client_id=int(client_id),
        use_rth=use_rth, pause_s=float(pause_s), max_contracts=int(max_contracts),
    )
    n = write_percontract_jsonl(rows, out_path)
    return out_path, n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-contract IBKR historical pull writer.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out-dir", required=True, help="e.g. $DATA_DIR/ibkr_datasets")
    ap.add_argument("--version", default="v001")
    ap.add_argument("--host", default="10.0.0.251")
    ap.add_argument("--port", type=int, default=4002)
    ap.add_argument("--client-id", type=int, default=450)
    ap.add_argument("--pause-s", type=float, default=20.0)
    ap.add_argument("--max-contracts", type=int, default=28)
    args = ap.parse_args(argv)

    out_path, n = pull_and_write(
        symbol=args.symbol, timeframe=args.timeframe, start=args.start, end=args.end,
        out_dir=args.out_dir, version=args.version, host=args.host, port=args.port,
        client_id=args.client_id, pause_s=args.pause_s, max_contracts=args.max_contracts,
    )
    print(f"per-contract pull: {n} rows -> {out_path}", file=sys.stderr)
    # Non-zero exit on an empty pull so the shell can flag it (mirrors the
    # market_raw pull's rows>0 success criterion).
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
