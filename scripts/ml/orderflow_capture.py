#!/usr/bin/env python3
"""Order-flow / microstructure capture side-car (S-MLOPT-S10, M14 Phase 2.2).

Polls Bybit public order-book + trades for one symbol at a fixed cadence,
aggregates the order-flow features PER BAR, and appends one row per completed
bar to a `market_microstructure` JSONL side-stream that `market_features` joins
as-of (the S11 funding/OI pattern). Storage-bounded: one row per bar, not raw
ticks.

**Runs on a build host, NEVER the Oracle live VM** (WS9 — no heavy capture on
the live trader). Guarded by `ICT_OFFVM_BUILD_HOST=1` (the trainer VM sets it;
the live VM must not). Public endpoints → anonymous fetch works.

Per-bar row: `{ts, symbol, ofi, buy_vol, sell_vol, rel_spread_mean,
microprice_dev, n_snapshots}` where
  - `ofi`            — Cont OFI summed over the bar's best-quote snapshots
  - `buy_vol/sell_vol` — taker buy/sell volume from the trade feed (Bybit `side`)
  - `rel_spread_mean`  — mean (ask-bid)/mid across the bar
  - `microprice_dev`   — mean (micro-price - mid)/mid across the bar (signed lean)

The expensive A/B-able features (VPIN over a volume-bucket window, ofi z-score,
order-imbalance) are derived from these at feature-build time in
`market_features`, keeping the capture dumb + cheap.

Resilient: every poll is wrapped so a transient exchange/network error is logged
and the loop continues; the current bar is flushed on rollover. Append-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.orderflow_features import (  # noqa: E402
    _finite_or_zero,
    _ofi_event,
    microprice,
    relative_spread,
)

OFFVM_ENV = "ICT_OFFVM_BUILD_HOST"


def _enforce_offvm() -> None:
    if os.environ.get(OFFVM_ENV, "") != "1":
        raise SystemExit(
            f"orderflow_capture requires {OFFVM_ENV}=1 — it must NOT run on the "
            "Oracle live VM. Set it only on a build host (the trainer VM)."
        )


def _bar_start_ms(ts_ms: int, bar_ms: int) -> int:
    return (ts_ms // bar_ms) * bar_ms


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _build_exchange(testnet: bool):
    import ccxt  # type: ignore[import-not-found]

    ex = ccxt.bybit(
        {
            "apiKey": os.environ.get("BYBIT_API_KEY"),
            "secret": os.environ.get("BYBIT_API_SECRET"),
            "enableRateLimit": True,
        }
    )
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


class _BarAccumulator:
    """Accumulates one bar's snapshots + trades, emits the aggregate row."""

    def __init__(self, bar_start_ms: int, symbol: str):
        self.bar_start_ms = bar_start_ms
        self.symbol = symbol
        self.ofi = 0.0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self._spread_sum = 0.0
        self._micro_dev_sum = 0.0
        self.n_snapshots = 0
        self._prev_quote: tuple[float, float, float, float] | None = None

    def add_snapshot(self, bid: float, bid_sz: float, ask: float, ask_sz: float) -> None:
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        if self._prev_quote is not None:
            pb0, vb0, pa0, va0 = self._prev_quote
            self.ofi += _ofi_event(pb0, vb0, pa0, va0, bid, bid_sz, ask, ask_sz)
        self._prev_quote = (bid, bid_sz, ask, ask_sz)
        rs = relative_spread(bid, ask)
        if rs is not None:
            self._spread_sum += rs
        mp = microprice(bid, bid_sz, ask, ask_sz)
        mid = 0.5 * (bid + ask)
        if mp is not None and mid > 0:
            self._micro_dev_sum += (mp - mid) / mid
        self.n_snapshots += 1

    def add_trade(self, amount: float, side: str) -> None:
        if amount <= 0:
            return
        if side == "buy":
            self.buy_vol += amount
        elif side == "sell":
            self.sell_vol += amount

    def row(self) -> dict:
        n = max(self.n_snapshots, 1)
        return {
            "ts": _iso(self.bar_start_ms),
            "symbol": self.symbol,
            "ofi": _finite_or_zero(self.ofi),
            "buy_vol": _finite_or_zero(self.buy_vol),
            "sell_vol": _finite_or_zero(self.sell_vol),
            "rel_spread_mean": _finite_or_zero(self._spread_sum / n),
            "microprice_dev": _finite_or_zero(self._micro_dev_sum / n),
            "n_snapshots": self.n_snapshots,
        }


def run(
    *,
    symbol: str,
    out_dir: Path,
    bar_seconds: int,
    poll_seconds: float,
    depth: int,
    testnet: bool,
    max_bars: int | None = None,
) -> int:
    _enforce_offvm()
    ex = _build_exchange(testnet)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "data.jsonl"
    bar_ms = bar_seconds * 1000
    acc: _BarAccumulator | None = None
    last_trade_ms = int(time.time() * 1000) - bar_ms
    bars_written = 0

    def _flush(a: _BarAccumulator) -> None:
        nonlocal bars_written
        with data_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(a.row()) + "\n")
        bars_written += 1

    while True:
        now_ms = int(time.time() * 1000)
        bstart = _bar_start_ms(now_ms, bar_ms)
        if acc is None:
            acc = _BarAccumulator(bstart, symbol)
        elif bstart != acc.bar_start_ms:
            _flush(acc)
            if max_bars is not None and bars_written >= max_bars:
                return 0
            acc = _BarAccumulator(bstart, symbol)
        try:
            ob = ex.fetch_order_book(symbol, limit=depth)
            bids, asks = ob.get("bids") or [], ob.get("asks") or []
            if bids and asks:
                acc.add_snapshot(
                    float(bids[0][0]), float(bids[0][1]),
                    float(asks[0][0]), float(asks[0][1]),
                )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[orderflow] order_book error: {type(exc).__name__}\n")
        try:
            trades = ex.fetch_trades(symbol, since=last_trade_ms, limit=200)
            for t in trades:
                tms = int(t.get("timestamp") or 0)
                if tms <= last_trade_ms:
                    continue
                acc.add_trade(float(t.get("amount") or 0.0), str(t.get("side") or ""))
                last_trade_ms = max(last_trade_ms, tms)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[orderflow] trades error: {type(exc).__name__}\n")
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--out", required=True, type=Path, help="market_microstructure dataset dir.")
    ap.add_argument("--bar-seconds", type=int, default=300, help="Bar size (default 5m).")
    ap.add_argument("--poll-seconds", type=float, default=2.0)
    ap.add_argument("--depth", type=int, default=5, help="Order-book levels to fetch.")
    ap.add_argument("--testnet", action="store_true")
    ap.add_argument("--max-bars", type=int, default=None, help="Stop after N bars (testing).")
    args = ap.parse_args(argv)
    return run(
        symbol=args.symbol,
        out_dir=args.out,
        bar_seconds=args.bar_seconds,
        poll_seconds=args.poll_seconds,
        depth=args.depth,
        testnet=args.testnet,
        max_bars=args.max_bars,
    )


if __name__ == "__main__":
    raise SystemExit(main())
