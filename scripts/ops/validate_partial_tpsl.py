#!/usr/bin/env python3
"""Venue validation for BYBIT_TPSL_MODE=partial (Fix 2 of
BL-20260720-ICTSCALP-PASTSTOP-EXITS).

Runs ON the live VM (via the ``validate-partial-tpsl`` operator action)
against the DEMO account ``bybit_1`` only — hard-locked; refuses any other
account. Places two tiny netted BTCUSDT orders with qty-scoped Partial
tpsl, verifies both bracket pairs coexist on the venue (the whole point:
under Full mode the second order would REPLACE the first's bracket),
amends one SL qty-scoped, then cleans up (cancels stop orders, reduce-only
closes the position).

Exit 0 with a PASS/FAIL verdict line per check; nonzero on a structural
failure. Every raw response is printed for the workflow comment.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ACCOUNT_ID = "bybit_1"
SYMBOL = "BTCUSDT"
CATEGORY = "linear"


def _load_demo_account():
    from src.config.accounts_loader import load_accounts_dict

    accounts = load_accounts_dict()
    acct = accounts.get(ACCOUNT_ID)
    if acct is None:
        raise SystemExit(f"FAIL: account {ACCOUNT_ID} not found in accounts.yaml")
    acct = dict(acct)
    acct.setdefault("account_id", ACCOUNT_ID)
    # Hard demo lock: this script places real venue orders; the demo/paper
    # account is the only permissible target.
    is_demo = bool(acct.get("is_demo")) or (
        str(acct.get("account_class") or "").lower() == "paper"
    ) or bool(acct.get("demo"))
    if not is_demo:
        raise SystemExit(
            f"FAIL: account {ACCOUNT_ID} does not read as demo/paper — refusing"
        )
    return acct


def _stop_orders(client):
    resp = client.get_open_orders(
        category=CATEGORY, symbol=SYMBOL, orderFilter="StopOrder",
    )
    return ((resp or {}).get("result") or {}).get("list") or []


def main() -> int:
    acct = _load_demo_account()
    from src.units.accounts.clients import bybit_client_for
    from src.units.accounts.execute import _submit_order

    client = bybit_client_for(acct)
    if client is None:
        print("FAIL: could not build bybit client for bybit_1 (creds missing?)")
        return 1

    os.environ["BYBIT_TPSL_MODE"] = "partial"
    cfg = {"account_id": ACCOUNT_ID, "exchange": "bybit", "market_type": "linear"}

    # Current price for wide, non-triggering brackets.
    tk = client.get_tickers(category=CATEGORY, symbol=SYMBOL)
    last = float((((tk or {}).get("result") or {}).get("list") or [{}])[0]
                 .get("lastPrice") or 0)
    if last <= 0:
        print("FAIL: could not read lastPrice")
        return 1
    print(f"lastPrice={last}")

    qty = 0.001
    verdicts = []

    def order(sl_off, tp_off):
        return {
            "account_id": ACCOUNT_ID, "symbol": SYMBOL, "side": "Buy",
            "qty": qty,
            "sl": round(last * (1 - sl_off), 1),
            "tp": round(last * (1 + tp_off), 1),
        }

    try:
        print("=== place order A (partial tpsl) ===")
        a = order(0.10, 0.10)
        _submit_order(client, a, cfg)
        print(f"A placed: sl={a['sl']} tp={a['tp']}")
        time.sleep(2)
        print("=== place order B (netted add, partial tpsl) ===")
        b = order(0.12, 0.12)
        _submit_order(client, b, cfg)
        print(f"B placed: sl={b['sl']} tp={b['tp']}")
        time.sleep(3)

        stops = _stop_orders(client)
        print(f"=== conditional (stop) orders after both placements: {len(stops)} ===")
        for s in stops:
            print(f"  qty={s.get('qty')} triggerPrice={s.get('triggerPrice')} "
                  f"stopOrderType={s.get('stopOrderType')} "
                  f"orderStatus={s.get('orderStatus')} orderId={s.get('orderId')}")
        # THE core check: two independent SL orders + two TP orders coexist.
        n_sl = sum(1 for s in stops
                   if str(s.get("stopOrderType", "")).lower()
                   in ("stoploss", "partialstoploss"))
        n_tp = sum(1 for s in stops
                   if str(s.get("stopOrderType", "")).lower()
                   in ("takeprofit", "partialtakeprofit"))
        ok_coexist = n_sl >= 2 and n_tp >= 2
        verdicts.append(("both bracket pairs coexist (2 SL + 2 TP)", ok_coexist,
                         f"n_sl={n_sl} n_tp={n_tp}"))

        pos = client.get_positions(category=CATEGORY, symbol=SYMBOL)
        plist = (((pos or {}).get("result") or {}).get("list") or [{}])
        print(f"=== position: size={plist[0].get('size')} "
              f"tpslMode={plist[0].get('tpslMode')} ===")
        verdicts.append(("position tpslMode is Partial",
                         str(plist[0].get("tpslMode", "")).lower() == "partial",
                         plist[0].get("tpslMode")))

        print("=== amend order A's SL qty-scoped ===")
        from src.units.accounts.execute import modify_open_order
        amended_sl = round(last * (1 - 0.11), 1)
        out = modify_open_order(client, cfg, symbol=SYMBOL, sl=amended_sl, qty=qty)
        print(f"amend result: {out}")
        verdicts.append(("qty-scoped SL amend accepted", bool(out.get("ok")),
                         out.get("error")))
        time.sleep(2)
        stops2 = _stop_orders(client)
        n_sl2 = sum(1 for s in stops2
                    if str(s.get("stopOrderType", "")).lower()
                    in ("stoploss", "partialstoploss"))
        print(f"stop orders after amend: {len(stops2)} (SL legs: {n_sl2})")
        verdicts.append(
            ("amend did not destroy the sibling bracket", n_sl2 >= 2, f"n_sl={n_sl2}"))
    finally:
        print("=== cleanup: cancel stop orders + reduce-only close ===")
        try:
            for s in _stop_orders(client):
                try:
                    client.cancel_order(category=CATEGORY, symbol=SYMBOL,
                                        orderId=s.get("orderId"))
                except Exception as exc:  # noqa: BLE001
                    print(f"  cancel {s.get('orderId')}: {exc}")
            pos = client.get_positions(category=CATEGORY, symbol=SYMBOL)
            size = float(((((pos or {}).get("result") or {}).get("list") or [{}])[0]
                          ).get("size") or 0)
            if size > 0:
                client.place_order(
                    category=CATEGORY, symbol=SYMBOL, side="Sell",
                    orderType="Market", qty=str(size), reduceOnly=True,
                )
                print(f"  reduce-only closed size={size}")
        except Exception as exc:  # noqa: BLE001
            print(f"  cleanup error (manual demo cleanup may be needed): {exc}")

    print("\n=== VERDICTS ===")
    all_ok = True
    for name, ok, detail in verdicts:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} ({detail})")
        all_ok = all_ok and ok
    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'} — "
          f"{'BYBIT_TPSL_MODE=partial is venue-validated on demo' if all_ok else 'do NOT flip BYBIT_TPSL_MODE=partial yet'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
