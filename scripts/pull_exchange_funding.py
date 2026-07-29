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

from src.runtime.exchange_accounts import live_bybit_fill_accounts
from src.runtime.exchange_fills_store import upsert_funding
from src.runtime.exchange_funding_puller import fetch_funding_window

logger = logging.getLogger("pull_exchange_funding")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=7, help="Pull window in days (default: 7)")
    p.add_argument("--account", default="live", help="account_id label (default: live)")
    p.add_argument("--symbol", action="append", default=None,
                   help="Symbol to query (repeat; omitted = all-symbols query)")
    p.add_argument(
        "--all-bybit-accounts",
        action="store_true",
        help=(
            "Ignore --account/--symbol/--api-*-env and loop EVERY live Bybit "
            "account in config/accounts.yaml, pulling each account's own "
            "declared symbols (Bybit funding is served per-contract). "
            "Config-driven; fail-soft on a missing-cred account."
        ),
    )
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


def _pull_one_account(
    *,
    account_id: str,
    api_key: str,
    api_secret: str,
    days: int,
    symbols,
    funding_path,
) -> int:
    """Pull one Bybit account's perp funding into the store; return rows inserted.

    Funding applies only to USDT-margined perps, so the routing is fixed to
    swap/linear regardless of account; a fresh ccxt client per account keeps the
    credentials isolated across the loop.
    """
    import ccxt

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
        account_id=account_id,
        days=days,
        symbols=symbols,
    )
    inserted = upsert_funding(rows, path=funding_path)
    logger.info(
        "pull_exchange_funding: account=%s days=%d symbols=%s candidates=%d inserted=%d store=%s",
        account_id, days,
        ",".join(symbols) if symbols else "(all)",
        len(rows), inserted,
        funding_path if funding_path is not None else "(default resolver)",
    )
    return inserted


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    funding_path = Path(args.fills_db) if args.fills_db else None

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
                    "pull_exchange_funding: skip %s — %s / %s not set",
                    acct.account_id, acct.key_env, acct.secret_env,
                )
                continue
            # Bybit funding is served per-contract, so pass this account's own
            # declared symbols; an account with none declared falls back to the
            # all-symbols query (which Bybit returns empty for → harmless).
            total_inserted += _pull_one_account(
                account_id=acct.account_id,
                api_key=api_key,
                api_secret=api_secret,
                days=args.days,
                symbols=list(acct.symbols) or None,
                funding_path=funding_path,
            )
            ran += 1
        logger.info(
            "pull_exchange_funding: all-bybit-accounts done — ran=%d/%d total_inserted=%d",
            ran, len(accounts), total_inserted,
        )
        return 0 if ran > 0 else 2

    api_key = os.environ.get(args.api_key_env)
    api_secret = os.environ.get(args.api_secret_env)
    if not api_key or not api_secret:
        logger.error("Missing %s / %s — cannot authenticate. Aborting.",
                     args.api_key_env, args.api_secret_env)
        return 2

    _pull_one_account(
        account_id=args.account,
        api_key=api_key,
        api_secret=api_secret,
        days=args.days,
        symbols=args.symbol,
        funding_path=funding_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
