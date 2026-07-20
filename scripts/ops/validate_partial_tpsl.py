#!/usr/bin/env python3
"""Venue validation for BYBIT_TPSL_MODE=partial (Fix 2 of
BL-20260720-ICTSCALP-PASTSTOP-EXITS).

Runs ON the live VM (via the ``validate-partial-tpsl`` operator action)
against the DEMO account ``bybit_1`` only — hard-locked; refuses any other
account. Places two tiny netted orders with qty-scoped Partial tpsl,
verifies both bracket pairs coexist on the venue (the whole point: under
Full mode the second order would REPLACE the first's bracket), amends one
SL qty-scoped, then cleans up (cancels the stop orders, reduce-only closes
the test position, restores the symbol's tpsl mode to Full).

The test runs on an ISOLATED symbol no strategy trades (first run, issue
#7145, used BTCUSDT: the demo account's live strategies held a 0.016
position with a Full-mode bracket, which (a) pinned the position-level
tpslMode read at "Full" regardless of our Partial legs and (b) made the
cleanup's flatten close THEIR position, not just the test's). A flat-at-
start guard aborts rather than run contaminated.

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
# Deliberately OUTSIDE every strategy roster (BTC/ETH/SOL/XRP/ADA/AVAX are
# live-traded on the bybit accounts) so the test never rides a real position.
SYMBOL = "LTCUSDT"
CATEGORY = "linear"
QTY_STEP = 0.1  # LTCUSDT linear qty step (= min order qty)
MIN_NOTIONAL_USD = 6.0  # venue min order VALUE is 5 USDT (ErrCode 110094); small buffer


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


def _position_size(client) -> float:
    pos = client.get_positions(category=CATEGORY, symbol=SYMBOL)
    row = (((pos or {}).get("result") or {}).get("list") or [{}])[0]
    try:
        return float(row.get("size") or 0)
    except (TypeError, ValueError):
        return 0.0


def _set_tpsl_mode(client, mode: str) -> None:
    """Best-effort position tpsl-mode flip; 'already this mode' is fine."""
    try:
        resp = client.set_tp_sl_mode(
            category=CATEGORY, symbol=SYMBOL, tpSlMode=mode,
        )
        print(f"set_tp_sl_mode({mode}): retCode={((resp or {}).get('retCode'))} "
              f"retMsg={((resp or {}).get('retMsg'))}")
    except Exception as exc:  # noqa: BLE001 — mode may already match
        print(f"set_tp_sl_mode({mode}): {exc}")


def main() -> int:
    acct = _load_demo_account()
    from src.units.accounts.clients import bybit_client_for
    from src.units.accounts.execute import _submit_order

    client = bybit_client_for(acct)
    if client is None:
        print("FAIL: could not build bybit client for bybit_1 (creds missing?)")
        return 1

    # Flat-at-start guard: a pre-existing position or working stop orders on
    # the test symbol would contaminate every check AND put the cleanup at
    # risk of touching state that is not ours (the #7145 BTCUSDT lesson).
    pre_size = _position_size(client)
    pre_stops = _stop_orders(client)
    if pre_size > 0 or pre_stops:
        print(f"FAIL: {SYMBOL} is not flat on {ACCOUNT_ID} "
              f"(size={pre_size}, stop_orders={len(pre_stops)}) — refusing to "
              "run on a contaminated symbol. Retry when flat.")
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

    # Venue enforces a minimum order VALUE (5 USDT, ErrCode 110094 — the
    # third dispatch, #7152, failed here at 0.1 LTC ~= $4.75), so size from
    # the live price rather than the min qty.
    import math
    qty = round(max(QTY_STEP, math.ceil(MIN_NOTIONAL_USD / last / QTY_STEP) * QTY_STEP), 1)
    print(f"qty={qty} (~${qty * last:.2f} notional)")

    verdicts = []

    def order(sl_off, tp_off):
        return {
            "account_id": ACCOUNT_ID, "symbol": SYMBOL, "side": "Buy",
            "qty": qty,
            "sl": round(last * (1 - sl_off), 2),
            "tp": round(last * (1 + tp_off), 2),
        }

    try:
        # Establish Partial mode explicitly while the symbol is flat (the
        # per-order tpslMode=Partial attribute should do this implicitly on
        # a clean symbol, but the explicit set is deterministic).
        _set_tpsl_mode(client, "Partial")

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
        amended_sl = round(last * (1 - 0.11), 2)
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
        # The flat-at-start guard proved everything on this symbol is ours,
        # so cancel-all + flatten-the-symbol is exactly test-scoped.
        print("=== cleanup: cancel stop orders + reduce-only close + restore Full mode ===")
        try:
            for s in _stop_orders(client):
                try:
                    client.cancel_order(category=CATEGORY, symbol=SYMBOL,
                                        orderId=s.get("orderId"))
                except Exception as exc:  # noqa: BLE001
                    print(f"  cancel {s.get('orderId')}: {exc}")
            size = _position_size(client)
            if size > 0:
                client.place_order(
                    category=CATEGORY, symbol=SYMBOL, side="Sell",
                    orderType="Market", qty=str(size), reduceOnly=True,
                )
                print(f"  reduce-only closed size={size}")
            # Leave the venue as we found it (live default is Full mode).
            _set_tpsl_mode(client, "Full")
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
