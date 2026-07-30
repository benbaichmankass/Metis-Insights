#!/usr/bin/env python3
"""Pull broker-truth executions from IBKR into the local fills store.

The IB sibling of ``scripts/pull_exchange_fills.py`` (Bybit) — read-only on the
broker side, idempotent (``exec_id`` PRIMARY KEY), safe to re-run on overlapping
windows.

WHY (2026-07-30 provenance audit)
---------------------------------
**State the population.** Measured on 2026-07-30: ``ib_paper`` carries
**+$284,084.92** of fabricated PnL in the *all-status* population (845 rows) —
but that is **4 ``orphaned`` rows**. In the *closed, non-backtest* decision
population (829 rows, −$36,018.60 fabricated) ``ib_paper`` is **3 of 24** rows
and the concentration is ``bybit_1`` / ``bybit_portfolio``.

The forward-looking reason this puller matters: the companion Tier-2 change
stopped ``_sweep_local_pnl_for_unpriced`` substituting a live mark, and IBKR
historical-candle coverage is 0% — so without a broker-truth read every future
IB close is a *declared unmeasured* gap rather than a number. Cause of both:
``interactive_brokers`` is absent from
``clients.BROKER_PNL_READER_EXCHANGES``, no IB fills reader existed, so every IB
close fell through to ``order_monitor._sweep_local_pnl_for_unpriced``, which
prices a CONFIRMED CLOSE off ``last_mark_price()`` hours later and then
multiplies the error by the futures contract multiplier.

IBKR serves the truth via ``reqExecutions``: each fill carries a
``CommissionReport`` with the broker's own ``commission`` and ``realizedPNL``. A
row sourced here is ``provenance.MEASURED``.

WHAT THIS IS *NOT*
------------------
**This is a forward-accruing pull, not a backfill.** IBKR's execution history is
short-lived (the API serves roughly the current trading day; the Gateway
discards on its nightly reset), so this CANNOT retroactively measure the 226
historical rows — those remain a relabelling problem, and the operator decision
of 2026-07-30 is that the historical pass is RELABEL ONLY, never re-price.

This script does not assert that window. Every run reports what the venue
ACTUALLY served (``oldest_exec_time`` .. ``newest_exec_time``, mapped/dropped
counts, how many fills carried realised PnL) so the true reach is measured on
first contact rather than assumed — see
``src.runtime.exchange_fills_ib.coverage_summary``.

**Tier-1.** Read-only broker call + a write to the standalone
``exchange_fills.sqlite`` sidecar. It never touches ``trade_journal.db``, never
places an order, and nothing reads it back into PnL resolution yet — wiring it
into the journal's PnL path is the separate **Tier-2** step that needs an
operator OK.

Usage:
    python3 scripts/pull_ib_executions.py [--days N] [--account ID]
                                          [--all-ib-accounts] [--fills-db PATH]
                                          [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the ``src`` package importable when run as
# ``python3 scripts/pull_ib_executions.py`` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.exchange_accounts import live_ib_fill_accounts  # noqa: E402
from src.runtime.exchange_fills_ib import (  # noqa: E402
    coverage_summary,
    fetch_ib_executions,
)
from src.runtime.exchange_fills_store import upsert_fills  # noqa: E402

logger = logging.getLogger("pull_ib_executions")


class IBReadError(RuntimeError):
    """The broker read could not be confirmed — NOT the same as 'no fills'."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--days",
        type=int,
        default=2,
        help=(
            "Pull window in days (default: 2 — over-samples the daily cadence "
            "so a missed run is picked up next time, to whatever extent IBKR "
            "still holds the history)"
        ),
    )
    p.add_argument(
        "--account",
        default=None,
        help="Single account_id to pull (default: --all-ib-accounts behaviour)",
    )
    p.add_argument(
        "--all-ib-accounts",
        action="store_true",
        help=(
            "Loop every live IBKR account declared in config/accounts.yaml. "
            "Config-driven so a roster change needs no edit here. Fail-soft: an "
            "account whose Gateway is unreachable is reported and skipped, not "
            "a hard abort."
        ),
    )
    p.add_argument(
        "--fills-db",
        default=None,
        help=(
            "exchange_fills.sqlite path to write into (default: the store "
            "resolver). Pass the canonical path explicitly "
            "(scripts/ops/_lib.sh::fills_store_path) so the puller and the "
            "offline cost sweep never resolve to different absolute paths "
            "(BL-20260717-FILLS-STORE-PATH-SPLIT)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + map + report coverage, but write nothing to the store.",
    )
    return p.parse_args(argv)


def _pull_one_account(
    *,
    account_id: str,
    account_cfg: dict,
    days: int,
    fills_path: Path | None,
    dry_run: bool,
) -> dict:
    """Pull one IB account's executions into the store.

    Returns a per-account result dict (also the coverage record logged as JSON).
    Raises :class:`IBReadError` when the broker read could not be confirmed —
    the caller must NOT record that as "no fills" (a could-not-read is not an
    absence; conflating the two is the exact error class the provenance work
    exists to stop).
    """
    # Local import: pulls in ib_insync, which is heavy and absent on hosts that
    # only run the mapping tests. Keeps ``--help`` snappy too.
    from src.units.accounts.clients import ib_read_client_for  # noqa: PLC0415

    client = ib_read_client_for(account_cfg)
    if client is None:
        raise IBReadError(f"{account_id}: not an IB account / ib_port unset")

    raw_count = 0

    def _fetch(since: str):
        nonlocal raw_count
        fills = client.executions(since)
        if fills is None:
            # None = breaker open / gateway wedged / read failed.
            raise IBReadError(
                f"{account_id}: executions read could not be confirmed "
                f"(gateway unreachable or circuit breaker open)"
            )
        raw_count = len(fills)
        return fills

    try:
        rows = fetch_ib_executions(_fetch, account_id=account_id, days=days)
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 — teardown must never mask the result
            pass

    cov = coverage_summary(rows, raw_fill_count=raw_count)
    inserted = 0 if dry_run else upsert_fills(rows, path=fills_path)
    result = {
        "account_id": account_id,
        "days": days,
        "inserted": inserted,
        "dry_run": bool(dry_run),
        **cov,
    }
    logger.info("pull_ib_executions: %s", json.dumps(result, sort_keys=True))
    if cov["dropped"]:
        # A dropped fill is a real signal (unmappable side/price/time), not noise.
        logger.warning(
            "pull_ib_executions: %s — %d of %d fills could not be mapped and "
            "were DROPPED (never coerced into a fabricated row)",
            account_id, cov["dropped"], cov["raw_fills"],
        )
    return result


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = _parse_args(argv)
    fills_path = Path(args.fills_db) if args.fills_db else None

    accounts = live_ib_fill_accounts()
    if args.account:
        accounts = [a for a in accounts if a.account_id == args.account]
        if not accounts:
            logger.error(
                "pull_ib_executions: no live IB account %r in config/accounts.yaml",
                args.account,
            )
            return 2
    if not accounts:
        logger.error(
            "pull_ib_executions: no live IBKR accounts in config/accounts.yaml"
        )
        return 2

    ran = 0
    failed = 0
    total_inserted = 0
    for acct in accounts:
        try:
            res = _pull_one_account(
                account_id=acct.account_id,
                account_cfg=acct.config,
                days=args.days,
                fills_path=fills_path,
                dry_run=args.dry_run,
            )
        except IBReadError as exc:
            # Fail-soft per account, but LOUD — an unreadable gateway means this
            # account accrued no broker truth this run, which must not look like
            # a clean zero.
            logger.warning("pull_ib_executions: skip %s — %s", acct.account_id, exc)
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pull_ib_executions: skip %s — unexpected %s: %s",
                acct.account_id, type(exc).__name__, exc,
            )
            failed += 1
            continue
        total_inserted += int(res["inserted"])
        ran += 1

    logger.info(
        "pull_ib_executions: done — ran=%d failed=%d of %d total_inserted=%d",
        ran, failed, len(accounts), total_inserted,
    )
    # A run that read NO account at all is a failure worth surfacing (gateway
    # down); one that read >= 1 is a success even if a sibling was skipped.
    return 0 if ran > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
