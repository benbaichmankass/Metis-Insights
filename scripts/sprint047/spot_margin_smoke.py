"""S-047 T3 — Bybit V5 Spot Margin testnet smoke test.

Operator-runnable end-to-end check that the D4 + D5 wiring routes a
spot-margin order through the Bybit V5 ``/v5/order/create`` endpoint
with ``isLeverage=1``, opens a borrow line on the account's wallet,
and that closing the position clears the borrow line.

Usage
-----
::

    BYBIT_TESTNET=true \\
    BYBIT_API_KEY_2=<testnet-key> \\
    BYBIT_API_SECRET_2=<testnet-secret> \\
        python scripts/sprint047/spot_margin_smoke.py

The script defaults to the ``bybit_2`` account row (the spot-margin
account declared in T1). It places a tiny SELL via ``execute_pkg`` to
borrow BTC, polls ``get_wallet_balance`` until a non-zero ``borrowAmount``
on BTC appears, then flattens via ``close_open_position`` and verifies
the borrow line clears.

Margin-agnostic: when the operator has not yet flipped Bybit's web-UI
Spot Margin toggle on the testnet account, every ``isLeverage=1`` order
returns retCode 110007 ("MARGIN_TRADING_NOT_ENABLED") server-side. The
script surfaces that retCode plainly so the operator knows the next
step is the toggle, not a code change.

Exit codes
----------
- 0  — success: borrow appeared after open, cleared after close
- 1  — usage / config error (missing env, account not spot-margin)
- 2  — exchange refused open (retCode != 0)
- 3  — borrow line never appeared after open
- 4  — close failed
- 5  — borrow line never cleared after close

This script is **not** invoked from CI; it is a manual operator harness
run against the Bybit testnet to verify T3 end-to-end.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("spot_margin_smoke")

# Repo root on the path so ``import src.…`` resolves regardless of the
# directory the operator runs the script from.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# Smoke trade size — 0.0005 BTC matches the live-smoke target in
# S-047 § 6 and sits comfortably above the testnet min-lot.
_SMOKE_QTY_BTC = 0.0005
_BORROW_POLL_SECONDS = 2.0
_BORROW_POLL_ATTEMPTS = 10


def _load_bybit2_cfg(accounts_path: Optional[str] = None) -> Dict[str, Any]:
    from src.config.accounts_loader import load_accounts_dict

    path = accounts_path or os.path.join(_REPO_ROOT, "config", "accounts.yaml")
    accounts = load_accounts_dict(path)
    bybit_2 = accounts.get("bybit_2")
    if not bybit_2:
        raise SystemExit("config/accounts.yaml has no `bybit_2` row")
    market_type = str(bybit_2.get("market_type") or "").strip().lower()
    if market_type != "spot-margin":
        raise SystemExit(
            f"bybit_2.market_type is {market_type!r} — expected 'spot-margin'. "
            "Run T1 first."
        )
    return {
        "account_id": "bybit_2",
        "exchange": bybit_2.get("exchange", "bybit"),
        "api_key_env": bybit_2.get("api_key_env", "BYBIT_API_KEY_2"),
        "market_type": market_type,
        "min_qty": (bybit_2.get("risk") or {}).get("min_qty", 0.001),
        "qty_precision": (bybit_2.get("risk") or {}).get("qty_precision", 3),
    }


def _btc_borrow_amount(client: Any) -> float:
    """Return the current BTC borrow line on the UNIFIED wallet (USDT-collateral).

    Bybit V5 ``get_wallet_balance`` includes a ``borrowAmount`` field per
    coin row. A spot-margin SHORT on BTCUSDT borrows BTC against USDT
    collateral, so the BTC row's ``borrowAmount`` should be positive
    while the position is open and back to zero after the close.
    """
    resp = client.get_wallet_balance(accountType="UNIFIED") or {}
    coins = (
        ((resp.get("result") or {}).get("list") or [{}])[0].get("coin", [])
    )
    for coin in coins:
        if str(coin.get("coin", "")).upper() == "BTC":
            try:
                return float(coin.get("borrowAmount") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _get_btc_mid_price(client: Any) -> float:
    """Cheap mid-price for BTCUSDT spot — used to set the package's entry/SL/TP."""
    resp = client.get_tickers(category="spot", symbol="BTCUSDT") or {}
    rows = (resp.get("result") or {}).get("list") or []
    if not rows:
        raise SystemExit("get_tickers returned no rows for BTCUSDT spot")
    last = float(rows[0].get("lastPrice") or rows[0].get("indexPrice") or 0)
    if last <= 0:
        raise SystemExit("get_tickers returned a non-positive price")
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accounts-path",
        default=None,
        help="Override path to config/accounts.yaml.",
    )
    parser.add_argument(
        "--qty",
        type=float,
        default=_SMOKE_QTY_BTC,
        help=f"BTC qty to short (default {_SMOKE_QTY_BTC}).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if str(os.environ.get("BYBIT_TESTNET", "")).strip().lower() != "true":
        logger.error(
            "BYBIT_TESTNET must be 'true' for the smoke run — "
            "do not run this against production."
        )
        return 1

    account_cfg = _load_bybit2_cfg(args.accounts_path)
    from src.core.coordinator import OrderPackage
    from src.units.accounts.clients import bybit_client_for
    from src.units.accounts.execute import close_open_position, execute_pkg

    client = bybit_client_for(account_cfg)
    if client is None:
        logger.error(
            "bybit_client_for returned None — set BYBIT_API_KEY_2 + "
            "BYBIT_API_SECRET_2 in env."
        )
        return 1

    # --- 1. Read pre-state --------------------------------------------------
    pre_borrow = _btc_borrow_amount(client)
    logger.info("pre-open BTC borrowAmount: %s", pre_borrow)

    # --- 2. Build a tiny short package + route via execute_pkg --------------
    last_price = _get_btc_mid_price(client)
    sl = last_price * 1.005   # short stop above entry
    tp = last_price * 0.99    # short tp below entry
    pkg = OrderPackage(
        strategy="smoke_test",
        symbol="BTCUSDT",
        direction="short",
        entry=last_price,
        sl=sl,
        tp=tp,
        meta={"smoke_id": f"s047-t3-{int(time.time())}"},
    )

    try:
        trade_id = execute_pkg(
            pkg, account_cfg,
            exchange_client=client,
            balance_usdt=10_000.0,
            dry_run=False,
            qty_override=float(args.qty),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "execute_pkg failed: %s — likely retCode 110007 if Spot "
            "Margin is not yet enabled on the testnet account.",
            exc,
        )
        return 2
    logger.info("opened short — trade_id=%s qty=%s", trade_id, args.qty)

    # --- 3. Poll for the borrow line ---------------------------------------
    open_borrow = pre_borrow
    for attempt in range(_BORROW_POLL_ATTEMPTS):
        time.sleep(_BORROW_POLL_SECONDS)
        open_borrow = _btc_borrow_amount(client)
        logger.info(
            "post-open poll %d/%d: BTC borrowAmount=%s",
            attempt + 1, _BORROW_POLL_ATTEMPTS, open_borrow,
        )
        if open_borrow > pre_borrow:
            break
    if open_borrow <= pre_borrow:
        logger.error(
            "borrow line never appeared (pre=%s post=%s) — Bybit may "
            "have settled the order against existing BTC instead of "
            "borrowing. Check the testnet wallet's BTC balance.",
            pre_borrow, open_borrow,
        )
        return 3

    # --- 4. Close --------------------------------------------------------
    close_result = close_open_position(
        client, account_cfg,
        symbol="BTCUSDT",
        side="short",
        qty=float(args.qty),
    )
    logger.info("close result: %s", close_result)
    if not close_result.get("ok"):
        return 4

    # --- 5. Verify the borrow clears --------------------------------------
    final_borrow = open_borrow
    for attempt in range(_BORROW_POLL_ATTEMPTS):
        time.sleep(_BORROW_POLL_SECONDS)
        final_borrow = _btc_borrow_amount(client)
        logger.info(
            "post-close poll %d/%d: BTC borrowAmount=%s",
            attempt + 1, _BORROW_POLL_ATTEMPTS, final_borrow,
        )
        if final_borrow <= pre_borrow + 1e-9:
            break
    if final_borrow > pre_borrow + 1e-9:
        logger.error(
            "borrow line never cleared (pre=%s peak=%s final=%s) — "
            "manual flatten may be required.",
            pre_borrow, open_borrow, final_borrow,
        )
        return 5

    logger.info(
        "✅ S-047 T3 smoke OK: borrow rose %s → %s on open and cleared "
        "back to %s on close.",
        pre_borrow, open_borrow, final_borrow,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
