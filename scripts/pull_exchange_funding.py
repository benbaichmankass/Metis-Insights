"""Pull recent perp funding from Bybit and upsert into the local funding store.

Slice B / B1 (MB-20260629-ALLOC-COSTCAP). Read-only on the exchange side.
Idempotent — safe to re-run on overlapping windows (keyed on funding_id).
Sibling of ``scripts/pull_exchange_fills.py``: perp funding is not in the
execution list, so it needs its own pull. Populates the ``exchange_funding``
table consumed by the broker-truth cost sweep's ``funding_paid_usd`` attribution.

    python3 scripts/pull_exchange_funding.py [--days N] [--account ID] [--symbol S]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime.exchange_funding_puller import fetch_funding_window  # noqa: E402
from src.runtime.exchange_fills_store import upsert_funding  # noqa: E402

logger = logging.getLogger("pull_exchange_funding")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=7, help="Pull window in days (default: 7)")
    p.add_argument("--account", default="live", help="account_id label (default: live)")
    p.add_argument("--symbol", action="append", default=None,
                   help="Symbol to query (repeat; omitted = all-symbols query)")
    p.add_argument("--api-key-env", default="BYBIT_API_KEY")
    p.add_argument("--api-secret-env", default="BYBIT_API_SECRET")
    p.add_argument(
        "--fills-db",
        default=None,
        help=(
            "exchange_fills.sqlite path (holds the exchange_funding table) to "
            "write into (default: the store resolver — DATA_DIR-anchored "
            "runtime_state/). Pass the canonical path explicitly "
            "(scripts/ops/_lib.sh::fills_store_path) so the funding puller and "
            "the offline cost sweep never resolve to different absolute paths "
            "when the wrapper shell lacks DATA_DIR (BL-20260717-FILLS-STORE-PATH-SPLIT)."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    api_key = os.environ.get(args.api_key_env)
    api_secret = os.environ.get(args.api_secret_env)
    if not api_key or not api_secret:
        logger.error("Missing %s / %s — cannot authenticate. Aborting.",
                     args.api_key_env, args.api_secret_env)
        return 2

    import ccxt  # noqa: PLC0415

    exchange = ccxt.bybit({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        # Funding applies to USDT-margined perps → the swap/linear routing.
        "options": {"defaultType": "swap"},
    })

    def _fetch_funding_history(sym, since, limit, params):
        merged = dict(params or {})
        merged["category"] = "linear"
        return exchange.fetch_funding_history(sym, since, limit, merged)

    rows = fetch_funding_window(
        _fetch_funding_history,
        account_id=args.account,
        days=args.days,
        symbols=args.symbol,
    )
    funding_path = Path(args.fills_db) if args.fills_db else None
    inserted = upsert_funding(rows, path=funding_path)
    logger.info(
        "pull_exchange_funding: account=%s days=%d symbols=%s candidates=%d inserted=%d store=%s",
        args.account, args.days,
        ",".join(args.symbol) if args.symbol else "(all)",
        len(rows), inserted,
        funding_path if funding_path is not None else "(default resolver)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
