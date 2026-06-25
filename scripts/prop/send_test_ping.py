#!/usr/bin/env python3
"""Fire a single TEST prop-account ticket through the REAL ping path.

This exercises the Breakout prop "trade flow" end-to-end up to (and including)
the notification: it resolves the prop account's ruleset, builds the per-account
leg + sizing, renders the trade-setup ticket, and emits it as a ``prop_signal``
(FCM push + the prop Telegram bot) via the exact production entry point
(``src.prop.breakout_executor.emit_prop_ticket``).

It is a TEST: the order is synthetic and clearly labelled, and it calls the
emitter DIRECTLY (not the full ``execute`` path), so NOTHING is journaled and no
exchange socket is ever opened — it only sends the ping. Safe to run any number
of times.

Must run where the prop bot token + FCM creds live (the live VM, with the
runtime ``.env`` loaded) — that is why it is dispatched through the
``send-prop-test-ping`` system-action, not from a sandbox session.

Usage:
    python3 scripts/prop/send_test_ping.py [--account breakout_1] [--symbol SOLUSDT]
        [--side long] [--entry 150 --sl 145 --tp 175] [--timeframe 1h]
        [--no-push] [--no-telegram]

Exit 0 if at least one leg (push/telegram) was attempted without raising; 1 on a
structural failure (bad account / invalid prices).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the repo root is importable when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _build_order(args: argparse.Namespace) -> dict:
    side = args.side.lower()
    direction = "long" if side in ("long", "buy", "b") else "short"
    # Synthetic but coherent prices (long: sl<entry<tp; short: tp<entry<sl).
    entry = float(args.entry)
    if direction == "long":
        sl = float(args.sl if args.sl is not None else entry * 0.97)
        tp = float(args.tp if args.tp is not None else entry * 1.17)
    else:
        sl = float(args.sl if args.sl is not None else entry * 1.03)
        tp = float(args.tp if args.tp is not None else entry * 0.83)
    return {
        "symbol": args.symbol,
        "direction": direction,
        "side": "buy" if direction == "long" else "sell",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        # The strategy label rides into the rendered ticket so the operator can
        # see at a glance this is a manual test, not a real fired signal.
        "strategy": f"{args.strategy} [TEST PING — ignore, not a real signal]",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Send one TEST prop ticket through the real ping path.")
    ap.add_argument("--account", default="breakout_1", help="prop account id in config/accounts.yaml")
    ap.add_argument("--symbol", default="SOLUSDT")
    ap.add_argument("--side", default="long", choices=["long", "short", "buy", "sell"])
    ap.add_argument("--entry", type=float, default=150.0)
    ap.add_argument("--sl", type=float, default=None)
    ap.add_argument("--tp", type=float, default=None)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--strategy", default="trend_donchian_sol")
    ap.add_argument("--no-push", action="store_true", help="skip the FCM push leg")
    ap.add_argument("--no-telegram", action="store_true", help="skip the Telegram leg")
    args = ap.parse_args()

    from src.config.accounts_loader import load_accounts_dict
    from src.prop.breakout_executor import emit_prop_ticket, is_manual_fill_id
    from src.prop.breakout_notify import emit_prop_signal

    accounts = load_accounts_dict() or {}
    account_cfg = dict(accounts.get(args.account) or {})
    if not account_cfg:
        print(f"ERROR: account '{args.account}' not found in config/accounts.yaml", file=sys.stderr)
        return 1
    account_cfg.setdefault("account_id", args.account)

    order = _build_order(args)

    import os

    # Presence-only (never the values) so the action log shows whether delivery
    # could actually happen — emit_prop_signal's own legs return True even when
    # they no-op on missing creds, so these booleans are the real signal.
    telegram_token_present = bool(
        os.environ.get("TELEGRAM_PROP_BOT_TOKEN")
        or os.environ.get("TELEGRAM_CLAUDE_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    creds = {
        "telegram_token": telegram_token_present,
        "telegram_chat_id": bool(os.environ.get("TELEGRAM_CHAT_ID")),
        "fcm": bool(
            os.environ.get("FCM_SERVICE_ACCOUNT_JSON_PATH")
            or os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        ),
    }
    telegram_deliverable = creds["telegram_token"] and creds["telegram_chat_id"]

    try:
        # Default (no leg-suppression flags): go through emit_prop_ticket's OWN
        # emit path so the ticket carries its generated ticket_id — that's what
        # makes the Yes/No place-decision buttons attach (the injected-emitter
        # seam below is called as `_emitter(ticket)` and has no ticket_id, so it
        # cannot show buttons). Only inject a custom emitter when a leg is being
        # suppressed for the test (a niche debug path; buttons won't show there).
        if args.no_push or args.no_telegram:
            def _emitter(ticket):
                return emit_prop_signal(
                    ticket, push=not args.no_push, telegram=not args.no_telegram)

            trade_id = emit_prop_ticket(
                order, account_cfg, timeframe=args.timeframe, _emitter=_emitter)
        else:
            trade_id = emit_prop_ticket(order, account_cfg, timeframe=args.timeframe)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: emit_prop_ticket raised: {exc}", file=sys.stderr)
        return 1

    result = {
        "account": args.account,
        "symbol": args.symbol,
        "side": order["direction"],
        "entry": order["entry"],
        "sl": order["sl"],
        "tp": order["tp"],
        "trade_id": trade_id,
        "manual_fill_marker": is_manual_fill_id(trade_id),
        "creds_present": creds,
        "telegram_deliverable": telegram_deliverable,
        "note": (
            "TEST ping only — nothing journaled, no exchange socket opened. "
            "If telegram_deliverable is false, the ticket built fine but no "
            "message was sent (missing token/chat-id in the action env)."
        ),
    }
    print(json.dumps(result, indent=2))
    # Surface a non-fatal warning line so the action log flags an undeliverable
    # ping without failing the action (the trade-flow logic still succeeded).
    if not telegram_deliverable:
        print(
            "WARNING: telegram not deliverable (token/chat-id absent) — the "
            "ticket was built but not sent.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
