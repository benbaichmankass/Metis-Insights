#!/usr/bin/env python3
"""Venue validation for the Bybit broker-naked re-arm sweep
(BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT).

Runs ON the live VM (via the ``validate-bybit-naked-rearm`` operator action)
against the DEMO account ``bybit_1`` only — hard-locked; refuses any other
account. It proves, against the REAL Bybit venue, the two things the new
``order_monitor._check_broker_naked_bybit_positions`` sweep + the Bybit branch
of ``_attempt_naked_autoprotect`` depend on:

  1. **Detection** — a live position with no ``stopLoss`` and no resting SL
     conditional leg reads as NAKED; after a re-arm it reads as PROTECTED. This
     mirrors ``_bybit_position_protection`` (Full-mode position ``stopLoss`` OR
     a resting Partial SL leg → protected) against the venue's real response
     shapes.
  2. **Re-arm** — a Full-mode ``set_trading_stop`` (``tpslMode="Full"``,
     ``positionIdx=0``) is ACCEPTED by the venue and actually attaches a
     position-level stop (the exact call the fix's re-arm makes).

Deliberately SELF-CONTAINED (inlines the detection + re-arm calls) so it
validates the venue contract without depending on the unmerged fix code — it
runs on ``main``.

Uses an isolated symbol no strategy trades (LTCUSDT), with a flat-at-start
guard, and always cleans up (cancel stops, reduce-only close, restore Full
mode). Exit 0 with a PASS/FAIL verdict line per check; nonzero on a structural
failure. Every raw response is printed for the workflow comment.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ACCOUNT_ID = "bybit_1"
# Outside every strategy roster (BTC/ETH/SOL/XRP/ADA/AVAX are live-traded on the
# bybit accounts) so the test never rides a real position.
SYMBOL = "LTCUSDT"
CATEGORY = "linear"
QTY_STEP = 0.1
MIN_NOTIONAL_USD = 6.0  # venue min order VALUE is 5 USDT (ErrCode 110094)

_SL_LEG_TYPES = {"stoploss", "partialstoploss"}


def _load_demo_account():
    from src.config.accounts_loader import load_accounts_dict

    accounts = load_accounts_dict()
    acct = accounts.get(ACCOUNT_ID)
    if acct is None:
        raise SystemExit(f"FAIL: account {ACCOUNT_ID} not found in accounts.yaml")
    acct = dict(acct)
    acct.setdefault("account_id", ACCOUNT_ID)
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


def _position_row(client):
    pos = client.get_positions(category=CATEGORY, symbol=SYMBOL)
    return (((pos or {}).get("result") or {}).get("list") or [{}])[0]


def _protection(client):
    """Inline mirror of order_monitor._bybit_position_protection → (size, protected)."""
    row = _position_row(client)
    try:
        size = abs(float(row.get("size") or 0) or 0.0)
    except (TypeError, ValueError):
        size = 0.0
    if size <= 0:
        return 0.0, True
    pos_sl = str(row.get("stopLoss") or "").strip()
    if pos_sl and pos_sl not in ("0", "0.0", "0.00"):
        return size, True
    has_sl_leg = any(
        str(o.get("stopOrderType") or "").lower() in _SL_LEG_TYPES
        for o in _stop_orders(client)
    )
    return size, has_sl_leg


def _set_tpsl_mode(client, mode: str) -> None:
    try:
        resp = client.set_tp_sl_mode(category=CATEGORY, symbol=SYMBOL, tpSlMode=mode)
        print(f"set_tp_sl_mode({mode}): retCode={((resp or {}).get('retCode'))} "
              f"retMsg={((resp or {}).get('retMsg'))}")
    except Exception as exc:  # noqa: BLE001
        print(f"set_tp_sl_mode({mode}): {exc}")


def main() -> int:
    acct = _load_demo_account()
    from src.units.accounts.clients import bybit_client_for

    client = bybit_client_for(acct)
    if client is None:
        print("FAIL: could not build bybit client for bybit_1 (creds missing?)")
        return 1

    pre = _position_row(client)
    try:
        pre_size = float(pre.get("size") or 0)
    except (TypeError, ValueError):
        pre_size = 0.0
    pre_stops = _stop_orders(client)
    if pre_size > 0 or pre_stops:
        print(f"FAIL: {SYMBOL} is not flat on {ACCOUNT_ID} (size={pre_size}, "
              f"stop_orders={len(pre_stops)}) — refusing on a contaminated symbol.")
        return 1

    tk = client.get_tickers(category=CATEGORY, symbol=SYMBOL)
    last = float((((tk or {}).get("result") or {}).get("list") or [{}])[0]
                 .get("lastPrice") or 0)
    if last <= 0:
        print("FAIL: could not read lastPrice")
        return 1
    qty = round(max(QTY_STEP, math.ceil(MIN_NOTIONAL_USD / last / QTY_STEP) * QTY_STEP), 1)
    print(f"lastPrice={last} qty={qty} (~${qty * last:.2f} notional)")

    verdicts = []
    try:
        # Establish Full mode (the re-arm the fix uses is Full-mode) and open a
        # NAKED long: a plain Market order with NO stopLoss/takeProfit.
        _set_tpsl_mode(client, "Full")
        print("=== open naked position (Market, no SL/TP) ===")
        resp = client.place_order(
            category=CATEGORY, symbol=SYMBOL, side="Buy",
            orderType="Market", qty=str(qty),
        )
        print(f"place_order: retCode={((resp or {}).get('retCode'))} "
              f"retMsg={((resp or {}).get('retMsg'))}")
        time.sleep(3)

        size, protected = _protection(client)
        print(f"=== protection read after naked open: size={size} protected={protected} ===")
        print(f"    position stopLoss={_position_row(client).get('stopLoss')!r} "
              f"stop_legs={len(_stop_orders(client))}")
        verdicts.append(("naked position reads as UNPROTECTED",
                         size > 0 and protected is False, f"size={size} protected={protected}"))

        # Re-arm EXACTLY as _attempt_naked_autoprotect's bybit branch does.
        sl = round(last * (1 - 0.10), 2)
        tp = round(last * (1 + 0.10), 2)
        print(f"=== re-arm Full-mode set_trading_stop (sl={sl} tp={tp}) ===")
        rr = client.set_trading_stop(
            category=CATEGORY, symbol=SYMBOL, positionIdx=0,
            tpslMode="Full", stopLoss=str(sl), takeProfit=str(tp),
        )
        rc = (rr or {}).get("retCode")
        print(f"set_trading_stop: retCode={rc} retMsg={((rr or {}).get('retMsg'))}")
        verdicts.append(("Full-mode set_trading_stop re-arm accepted",
                         rc in (0, "0", None), f"retCode={rc}"))
        time.sleep(3)

        size2, protected2 = _protection(client)
        print(f"=== protection read after re-arm: size={size2} protected={protected2} ===")
        print(f"    position stopLoss={_position_row(client).get('stopLoss')!r}")
        verdicts.append(("re-armed position reads as PROTECTED",
                         bool(protected2), f"protected={protected2}"))
    finally:
        print("=== cleanup: cancel stops + reduce-only close + restore Full mode ===")
        try:
            for s in _stop_orders(client):
                try:
                    client.cancel_order(category=CATEGORY, symbol=SYMBOL,
                                        orderId=s.get("orderId"))
                except Exception as exc:  # noqa: BLE001
                    print(f"  cancel {s.get('orderId')}: {exc}")
            row = _position_row(client)
            try:
                size = float(row.get("size") or 0)
            except (TypeError, ValueError):
                size = 0.0
            if size > 0:
                # Best-effort clear the position stop — Bybit returns
                # ErrCode 34040 "not modified" when there's nothing to clear;
                # that must NOT abort the flatten (the earlier bug left the
                # demo position open). Its own try/except so the reduce-only
                # close ALWAYS runs. A reduceOnly Market close cancels a Full
                # position stop on its own anyway.
                try:
                    client.set_trading_stop(
                        category=CATEGORY, symbol=SYMBOL, positionIdx=0,
                        tpslMode="Full", stopLoss="0", takeProfit="0",
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  clear position stop (non-fatal): {exc}")
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
          f"{'Bybit naked-rearm sweep is venue-validated on demo' if all_ok else 'do NOT merge the real-money re-arm yet'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
