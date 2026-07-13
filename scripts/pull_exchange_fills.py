#!/usr/bin/env python3
"""Pull recent fills from Bybit and upsert into the local fills store.

S-067 follow-up #6. Read-only on the exchange side. Idempotent — safe
to re-run on overlapping windows; duplicate ``exec_id`` rows are
silently dropped at the store level.

Usage:
    python3 scripts/pull_exchange_fills.py [--days N] [--account ID]
                                            [--symbol SYMBOL] ...

The default --days=2 window over-samples by 24h vs the daily cron
cadence so that a missed run still picks up the previous day's
fills on the next successful run.

When run without --account, the script defaults to ``live`` (the
legacy account label per ``Database.insert_trade``). For multi-account
hosts, run once per account; the store's primary key is ``exec_id``
so cross-account fills won't collide unless the exchange itself
reuses execution IDs across accounts (it doesn't).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Make the ``src`` package importable when run as
# ``python3 scripts/pull_exchange_fills.py`` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.exchange_fills_puller import fetch_fills_window  # noqa: E402
from src.runtime.exchange_fills_store import upsert_fills  # noqa: E402

logger = logging.getLogger("pull_exchange_fills")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--days",
        type=int,
        default=2,
        help="Pull window in days (default: 2 for daily cron over-sampling)",
    )
    p.add_argument(
        "--account",
        default="live",
        help="account_id label to attribute fills to (default: live)",
    )
    p.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="Symbol to query (repeat for multiple); omitted = all-symbols query",
    )
    p.add_argument(
        "--category",
        choices=("spot", "linear"),
        default="spot",
        help=(
            "Bybit V5 product category to pull fills from (default: spot, "
            "the historical behaviour). Use 'linear' for USDT-margined "
            "perpetuals — bybit_2's market_type per config/accounts.yaml. "
            "Without it the V5 execution endpoint defaults to spot and "
            "returns nothing for perp fills (why the store stayed empty, "
            "BL-20260713-EXCHANGE-FILLS-STORE-EMPTY)."
        ),
    )
    p.add_argument(
        "--api-key-env",
        default="BYBIT_API_KEY",
        help="Env var holding the Bybit API key (default: BYBIT_API_KEY)",
    )
    p.add_argument(
        "--api-secret-env",
        default="BYBIT_API_SECRET",
        help="Env var holding the Bybit API secret (default: BYBIT_API_SECRET)",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    api_key = os.environ.get(args.api_key_env)
    api_secret = os.environ.get(args.api_secret_env)
    if not api_key or not api_secret:
        logger.error(
            "Missing %s / %s — cannot authenticate. Aborting.",
            args.api_key_env, args.api_secret_env,
        )
        return 2

    # Local import: ccxt is heavy and the puller may run in a tight
    # cron cycle. Importing inside main keeps `--help` snappy.
    import ccxt  # noqa: PLC0415

    exchange = ccxt.bybit({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        # ccxt's Bybit V5 routing: perp fills need defaultType=swap AND an
        # explicit category param on the call (same convention as
        # src/exchange/bybit_connector.py — the construction-time default
        # alone is not load-bearing on the unified account).
        "options": {"defaultType": "swap" if args.category == "linear" else "spot"},
    })

    def _fetch_my_trades(sym, since, limit, params):
        merged = dict(params or {})
        merged["category"] = args.category
        return exchange.fetch_my_trades(sym, since, limit, merged)

    rows = fetch_fills_window(
        _fetch_my_trades,
        account_id=args.account,
        days=args.days,
        symbols=args.symbol,
    )
    inserted = upsert_fills(rows)
    logger.info(
        "pull_exchange_fills: account=%s days=%d candidates=%d inserted=%d",
        args.account, args.days, len(rows), inserted,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
