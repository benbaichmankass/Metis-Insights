#!/usr/bin/env python3
"""Pull Bybit's own wallet ledger (``/v5/account/transaction-log``) into the
venue-truth store, continuously — the LIVE replacement for a pasted CSV export.

WHY (operator directive, 2026-08-31). The authoritative realized figure for
``bybit_2`` came from an operator's UM CSV export and therefore froze on
2026-07-13 while the account kept trading — 59 closed real-money trades with no
wallet-truth counterpart (``BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-
CLOSES-UNRECONCILED``). We hold live API credentials for every Bybit account and
the venue serves the same quantity the export shows, so there is no reason for a
human to be in this loop:

    *"You cannot manage a trade system based on the fact that I'm gonna
    occasionally give you a CSV of trade data."*

WHAT IT DOES NOT DO. It does not compute P&L and it does not decide anything.
It stores rows. The definition of wallet truth — which ``type`` values count,
which currencies, what ``change`` means — lives in
``src/runtime/bybit_wallet_truth.py`` as pure functions, so the money question
is arguable in tests rather than embedded in a puller. It never touches
``trade_journal.db`` and never reaches the order path.

RUNTIME SHAPE. Rides the EXISTING hourly ``ict-exchange-fills-pull`` service as a
third ExecStart rather than adding a timer — a smaller deploy surface on a path
that already works. Overlapping windows are the normal case for an hourly puller
with a lookback, so the store is keyed on the venue's own row id and a re-pull
inserts nothing (asserted in tests: a double-count here would MOVE an
account-level P&L figure, which is worse than a duplicated fill).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.exchange_accounts import live_bybit_fill_accounts  # noqa: E402
from src.runtime.exchange_fills_store import upsert_transaction_log  # noqa: E402

logger = logging.getLogger("pull_bybit_transaction_log")

#: Hard bound on cursor pagination per account per run. The venue decides how
#: many pages exist; an unbounded `while cursor` is how a puller becomes an
#: infinite loop against a misbehaving cursor. 7 days at 200/page is far inside
#: this, so hitting the cap is itself worth logging as a WARNING.
MAX_PAGES = 50
PAGE_LIMIT = 100


def _default_client(api_key: str, api_secret: str, demo: bool):
    from pybit.unified_trading import HTTP  # imported lazily: VM-only dep

    if demo:
        return HTTP(demo=True, api_key=api_key, api_secret=api_secret)
    return HTTP(testnet=False, api_key=api_key, api_secret=api_secret)


def fetch_transaction_log(
    client: Any,
    *,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """Every transaction-log row in the window, following the venue's cursor.

    Raises on a transport/auth failure — the CALLER turns that into the
    ``unreadable`` state. Returning ``[]`` on an exception here would collapse
    "we could not look" into "the account was flat", which is the single
    reading ``bybit_wallet_truth`` exists to keep apart.
    """
    rows: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    for page in range(max_pages):
        params: dict[str, Any] = {"accountType": "UNIFIED", "limit": PAGE_LIMIT}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        if cursor:
            params["cursor"] = cursor
        resp = client.get_transaction_log(**params)
        result = (resp or {}).get("result") or {}
        batch = result.get("list") or []
        rows.extend(batch)
        cursor = result.get("nextPageCursor") or None
        if not cursor or not batch:
            break
    else:
        logger.warning(
            "transaction-log pagination hit MAX_PAGES=%d — the window may be "
            "TRUNCATED, so treat the resulting figure as a lower bound",
            max_pages,
        )
    return rows


def pull_one_account(
    account_id: str,
    api_key: str,
    api_secret: str,
    *,
    demo: bool,
    days: int,
    store_path: Optional[Path] = None,
    client_factory: Callable[..., Any] = _default_client,
    now_ms: Optional[int] = None,
) -> int:
    import time

    end = int(now_ms if now_ms is not None else time.time() * 1000)
    start = end - int(days) * 86_400_000
    client = client_factory(api_key, api_secret, demo)
    rows = fetch_transaction_log(client, start_ms=start, end_ms=end)
    inserted = upsert_transaction_log(rows, account_id, path=store_path)
    logger.info(
        "transaction-log: account=%s demo=%s days=%d fetched=%d inserted=%d",
        account_id, demo, days, len(rows), inserted,
    )
    return inserted


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all-bybit-accounts", action="store_true")
    ap.add_argument("--account")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--store-db")
    ap.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    store = Path(args.store_db) if args.store_db else None
    accounts = live_bybit_fill_accounts()
    if args.account:
        accounts = [a for a in accounts if a.account_id == args.account]
    if not accounts:
        logger.error("no matching live Bybit accounts in config/accounts.yaml")
        return 2

    ok, failed, skipped, total = 0, [], [], 0
    for acct in accounts:
        key = os.environ.get(acct.key_env)
        secret = os.environ.get(acct.secret_env)
        if not key or not secret:
            logger.warning(
                "skip %s — %s / %s not set", acct.account_id, acct.key_env, acct.secret_env
            )
            skipped.append(acct.account_id)
            continue
        try:
            total += pull_one_account(
                acct.account_id, key, secret,
                demo=acct.demo, days=args.days, store_path=store,
            )
        except Exception as exc:  # noqa: BLE001
            # One unreachable account must not abort the others, but it IS a
            # failure and propagates to the exit code — the unit goes red rather
            # than reporting success over an account it never covered. Same
            # hard-won shape as pull_exchange_fills (BL-20260807).
            logger.error("account=%s FAILED (demo=%s): %s", acct.account_id, acct.demo, exc)
            failed.append(acct.account_id)
            continue
        ok += 1

    summary = {
        "ok": ok, "failed": failed, "skipped": skipped,
        "accounts": len(accounts), "inserted": total,
    }
    logger.info("transaction-log done — %s", summary)
    if args.json:
        print(json.dumps(summary))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
