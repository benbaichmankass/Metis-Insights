#!/usr/bin/env python3
"""Pull recent FILL activities from Alpaca into the exchange-fills store.

Broker-truth cost coverage (rec #7) — the Alpaca sibling of
``scripts/pull_exchange_fills.py`` (Bybit). Loops every live Alpaca account from
``config/accounts.yaml`` (``alpaca_paper`` / ``alpaca_portfolio`` /
``alpaca_options_paper`` — DISTINCT sub-accounts with distinct creds), reads its
``/v2/account/activities`` FILL records, and upserts into
``runtime_state/exchange_fills.sqlite`` so ``/api/bot/pnl/exchange`` + the
broker-truth cost sweep cover Alpaca too.

Read-only on the broker side, idempotent (``exec_id`` PRIMARY KEY). Fail-soft:
an account whose creds aren't set is skipped with a warning, not a hard abort.

    python3 scripts/pull_alpaca_fills.py --all-alpaca-accounts [--days N] [--fills-db PATH]
    python3 scripts/pull_alpaca_fills.py --account alpaca_paper --days 7   # single
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.exchange_accounts import live_alpaca_fill_accounts  # noqa: E402
from src.runtime.exchange_fills_alpaca import fetch_alpaca_fills  # noqa: E402
from src.runtime.exchange_fills_store import upsert_fills  # noqa: E402

logger = logging.getLogger("pull_alpaca_fills")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=7, help="Pull window in days (default: 7)")
    p.add_argument(
        "--account", default="alpaca",
        help="account_id label for a single-account pull (default: alpaca)",
    )
    p.add_argument("--api-key-env", default="ALPACA_API_KEY_ID")
    p.add_argument("--api-secret-env", default="ALPACA_API_SECRET_KEY")
    p.add_argument("--alpaca-env", default="paper", choices=("paper", "live"))
    p.add_argument(
        "--all-alpaca-accounts",
        action="store_true",
        help=(
            "Ignore --account/--api-*-env/--alpaca-env and loop EVERY live Alpaca "
            "account in config/accounts.yaml, each with its own credentials + host. "
            "Config-driven; fail-soft on a missing-cred account. The daily "
            "broker-truth cost sweep uses this."
        ),
    )
    p.add_argument(
        "--fills-db",
        default=None,
        help=(
            "exchange_fills.sqlite path to write into (default: the store "
            "resolver — DATA_DIR-anchored runtime_state/). Pass the canonical "
            "path explicitly (scripts/ops/_lib.sh::fills_store_path) so the "
            "puller and the offline cost sweep never resolve to different "
            "absolute paths when the wrapper shell lacks DATA_DIR."
        ),
    )
    return p.parse_args(argv)


def _pull_one_account(
    *,
    account_id: str,
    api_key: str,
    api_secret: str,
    env: str,
    days: int,
    fills_path,
) -> int:
    """Pull one Alpaca account's FILL activities into the store; return inserted."""
    from src.units.accounts.alpaca_client import AlpacaClient  # noqa: PLC0415

    client = AlpacaClient(api_key=api_key, api_secret=api_secret, env=env)

    def _fetch_page(*, after, page_token):
        resp = client.account_activities(
            activity_types="FILL", after=after, page_token=page_token, page_size=100,
        )
        if resp.get("retCode") != 0:
            logger.warning(
                "pull_alpaca_fills: account_activities failed for %s: %s",
                account_id, resp.get("retMsg"),
            )
            return []
        result = resp.get("result")
        return result if isinstance(result, list) else []

    rows = fetch_alpaca_fills(_fetch_page, account_id=account_id, days=days)
    inserted = upsert_fills(rows, path=fills_path)
    logger.info(
        "pull_alpaca_fills: account=%s env=%s days=%d candidates=%d inserted=%d store=%s",
        account_id, env, days, len(rows), inserted,
        fills_path if fills_path is not None else "(default resolver)",
    )
    return inserted


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    fills_path = Path(args.fills_db) if args.fills_db else None

    if args.all_alpaca_accounts:
        accounts = live_alpaca_fill_accounts()
        if not accounts:
            logger.error(
                "--all-alpaca-accounts: no live Alpaca accounts in config/accounts.yaml"
            )
            return 2
        ran = 0
        total_inserted = 0
        for acct in accounts:
            api_key = os.environ.get(acct.key_env)
            api_secret = os.environ.get(acct.secret_env)
            if not api_key or not api_secret:
                logger.warning(
                    "pull_alpaca_fills: skip %s — %s / %s not set",
                    acct.account_id, acct.key_env, acct.secret_env,
                )
                continue
            total_inserted += _pull_one_account(
                account_id=acct.account_id,
                api_key=api_key,
                api_secret=api_secret,
                env=acct.env,
                days=args.days,
                fills_path=fills_path,
            )
            ran += 1
        logger.info(
            "pull_alpaca_fills: all-alpaca-accounts done — ran=%d/%d total_inserted=%d",
            ran, len(accounts), total_inserted,
        )
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
        env=args.alpaca_env,
        days=args.days,
        fills_path=fills_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
