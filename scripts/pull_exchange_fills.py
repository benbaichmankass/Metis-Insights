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

from src.runtime.exchange_accounts import live_bybit_fill_accounts
from src.runtime.exchange_fills_puller import fetch_fills_window
from src.runtime.exchange_fills_store import upsert_fills

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
        "--all-bybit-accounts",
        action="store_true",
        help=(
            "Ignore --account/--category/--api-*-env and instead loop EVERY "
            "live Bybit account declared in config/accounts.yaml (each pulled "
            "with its own api_key_env + market_type category). Config-driven so "
            "a roster change needs no puller/unit edit — the daily broker-truth "
            "cost sweep uses this. Fail-soft: an account whose creds aren't set "
            "is skipped with a warning, not a hard abort."
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
    p.add_argument(
        "--fills-db",
        default=None,
        help=(
            "exchange_fills.sqlite path to write into (default: the store "
            "resolver — DATA_DIR-anchored runtime_state/). Pass the canonical "
            "path explicitly (scripts/ops/_lib.sh::fills_store_path) so the "
            "puller and the offline cost sweep never resolve to different "
            "absolute paths when the wrapper shell lacks DATA_DIR "
            "(BL-20260717-FILLS-STORE-PATH-SPLIT)."
        ),
    )
    return p.parse_args(argv)


def _pull_one_account(
    *,
    account_id: str,
    api_key: str,
    api_secret: str,
    category: str,
    days: int,
    symbols,
    fills_path,
) -> int:
    """Pull one Bybit account's fills into the store; return rows inserted.

    Builds a fresh ccxt client per account so each authenticates with its own
    key pair (a shared client can't switch credentials mid-loop).
    """
    # Local import: ccxt is heavy and the puller may run in a tight
    # cron cycle. Importing inside the call keeps `--help` snappy.
    import ccxt

    exchange = ccxt.bybit({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        # ccxt's Bybit V5 routing: perp fills need defaultType=swap AND an
        # explicit category param on the call (same convention as
        # src/exchange/bybit_connector.py — the construction-time default
        # alone is not load-bearing on the unified account).
        "options": {"defaultType": "swap" if category == "linear" else "spot"},
    })

    def _fetch_my_trades(sym, since, limit, params):
        merged = dict(params or {})
        merged["category"] = category
        return exchange.fetch_my_trades(sym, since, limit, merged)

    rows = fetch_fills_window(
        _fetch_my_trades,
        account_id=account_id,
        days=days,
        symbols=symbols,
    )
    inserted = upsert_fills(rows, path=fills_path)
    logger.info(
        "pull_exchange_fills: account=%s category=%s days=%d candidates=%d inserted=%d store=%s",
        account_id, category, days, len(rows), inserted,
        fills_path if fills_path is not None else "(default resolver)",
    )
    return inserted


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    fills_path = Path(args.fills_db) if args.fills_db else None

    if args.all_bybit_accounts:
        accounts = live_bybit_fill_accounts()
        if not accounts:
            logger.error(
                "--all-bybit-accounts: no live Bybit accounts in config/accounts.yaml"
            )
            return 2
        ran = 0
        total_inserted = 0
        for acct in accounts:
            api_key = os.environ.get(acct.key_env)
            api_secret = os.environ.get(acct.secret_env)
            if not api_key or not api_secret:
                logger.warning(
                    "pull_exchange_fills: skip %s — %s / %s not set",
                    acct.account_id, acct.key_env, acct.secret_env,
                )
                continue
            total_inserted += _pull_one_account(
                account_id=acct.account_id,
                api_key=api_key,
                api_secret=api_secret,
                category=acct.category,
                days=args.days,
                symbols=args.symbol,
                fills_path=fills_path,
            )
            ran += 1
        logger.info(
            "pull_exchange_fills: all-bybit-accounts done — ran=%d/%d total_inserted=%d",
            ran, len(accounts), total_inserted,
        )
        # A run that authenticated no account at all is a failure worth surfacing
        # (all creds missing); one that ran ≥1 is a success even if others skipped.
        return 0 if ran > 0 else 2

    api_key = os.environ.get(args.api_key_env)
    api_secret = os.environ.get(args.api_secret_env)
    if not api_key or not api_secret:
        logger.error(
            "Missing %s / %s — cannot authenticate. Aborting.",
            args.api_key_env, args.api_secret_env,
        )
        return 2

    _pull_one_account(
        account_id=args.account,
        api_key=api_key,
        api_secret=api_secret,
        category=args.category,
        days=args.days,
        symbols=args.symbol,
        fills_path=fills_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
