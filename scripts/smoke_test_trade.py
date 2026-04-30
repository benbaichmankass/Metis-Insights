#!/usr/bin/env python3
"""S-017 — Live trading plumbing smoke test.

Constructs a single signal tagged ``meta.strategy_name="smoke_test"``,
dispatches it through ``safe_place_order`` (the same code path the live
trader uses), captures the response, and — if the order filled —
immediately closes the position with an opposite-side order.

The smoke is *additive*: the strategy multiplexer keeps running on its
normal cadence; this script just injects one tagged signal alongside
real strategy signals. P&L attribution stays clean because
``meta.strategy_name`` is ``smoke_test`` rather than ``vwap`` or
``turtle_soup``.

USAGE
-----
::

    PYTHONPATH=. python3 scripts/smoke_test_trade.py \\
        --account bybit_2 \\
        --qty 0.001 \\
        --side buy

``--dry-run`` swaps in a no-op execution path so the harness can be
exercised without hitting the exchange.

AUTONOMOUS-TRADING RULE
-----------------------
Per ``CLAUDE.md`` § "Autonomous live-trading rule": the trader is
designed to fire trades without per-trade operator confirmation.
This script honours that — it does NOT require an interactive
``--confirm`` flag in LIVE mode. The safety rails are entirely
process-level (``ALLOW_LIVE_TRADING`` interlock + ``RiskManager``
limits in ``safe_place_order`` + the hard ``qty`` cap below).

SAFETY GUARDS
-------------
1. Hard cap: ``qty`` may not exceed ``MAX_SAFE_QTY`` (0.001 BTC).
   The script refuses to start if the cap is exceeded — typo guard,
   not human-in-the-loop.
2. Read-only against the strategy / order code paths: the script does
   NOT import ``src/units/strategies/*`` and does NOT modify
   ``src/runtime/orders.py``. It only constructs a signal dict and
   passes it through the existing ``safe_place_order`` entry point —
   the same entry point a real strategy signal uses.
3. Refuses if ``ALLOW_LIVE_TRADING`` env-var is unset / false in
   live mode. This is the standing process-level interlock; the
   smoke can't accidentally fire if the env file disables live
   trading.
4. Tags every audit-log entry with ``strategy="smoke_test"`` and
   ``meta.is_smoke=True`` so future ``/strategies`` aggregations can
   exclude them.
5. Post-fill, attempts an opposite-side close at the same ``qty``.
   If the close fails, prints a loud warning so the operator can
   manually flatten via ``/closeall``.

EXIT CODES
----------
0 — order accepted (fill or close as expected)
1 — order rejected by the exchange (this is a valid smoke-test
    outcome — proves plumbing-on-rejection works)
2 — script-level error (usage, missing creds, safety guard tripped)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("smoke_test_trade")

# Hard cap. The script will refuse to start if --qty exceeds this.
# 0.001 BTC ≈ ~$70 at current spot — Bybit's typical perp min-lot.
MAX_SAFE_QTY = 0.001

# Audit log location (mirrors src.utils.signal_audit_logger).
AUDIT_PATH = REPO_ROOT / "runtime_logs" / "signal_audit.jsonl"


# ---------------------------------------------------------------------------
# Account-resolution helpers (mirror src/bot/data_loaders.py)
# ---------------------------------------------------------------------------


def _load_accounts_yaml() -> Dict[str, Dict[str, Any]]:
    import yaml
    yaml_path = REPO_ROOT / "config" / "accounts.yaml"
    with yaml_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return raw.get("accounts") or {}


def _account_settings(name: str) -> Dict[str, Any]:
    """Build the settings dict the live trader's pipeline uses for *name*.

    Reads ``config/accounts.yaml`` for the api_key_env contract; resolves
    ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET`` via ``os.environ`` (the
    per-account ``.env.bybit_<name>`` file must already be sourced into
    the process environment, exactly like the systemd unit does)."""
    accounts = _load_accounts_yaml()
    if name not in accounts:
        raise SystemExit(
            f"Unknown account {name!r}. Known: {sorted(accounts)}"
        )
    cfg = accounts[name]

    if cfg.get("enabled") is False:
        raise SystemExit(
            f"Account {name!r} is disabled in accounts.yaml; "
            f"refusing to dispatch a smoke trade against it."
        )

    api_key_env = cfg.get("api_key_env")
    if not api_key_env:
        raise SystemExit(f"Account {name!r} has no api_key_env in accounts.yaml")

    api_key = os.environ.get(api_key_env)
    secret_env = (
        cfg.get("api_secret_env")
        or api_key_env.replace("_API_KEY", "_API_SECRET")
    )
    api_secret = os.environ.get(secret_env)
    if not api_key or not api_secret:
        raise SystemExit(
            f"Bybit credentials not found for {name!r} "
            f"(needs {api_key_env} + {secret_env} in env). "
            f"Source the matching .env.bybit_<name> file before running."
        )

    return {
        "EXCHANGE": cfg.get("exchange", "bybit"),
        "SYMBOL": "BTCUSDT",
        "BYBIT_API_KEY": api_key,
        "BYBIT_API_SECRET": api_secret,
        "MAX_QTY": MAX_SAFE_QTY,
        "ACCOUNT_ID": name,
    }


# ---------------------------------------------------------------------------
# Signal construction
# ---------------------------------------------------------------------------


def _build_smoke_signal(
    side: str, qty: float, account: str, *, note: str,
) -> Dict[str, Any]:
    """Construct the signal dict that goes through ``safe_place_order``.

    ``meta.strategy_name`` is hardcoded to ``"smoke_test"`` so future
    ``/strategies`` aggregations can filter these out.
    """
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "qty": qty,
        "meta": {
            "strategy_name": "smoke_test",
            "is_smoke": True,
            "account_id": account,
            "note": note,
            "smoke_id": uuid.uuid4().hex[:8],
        },
    }


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _log_smoke_event(payload: Dict[str, Any]) -> None:
    payload = {
        **payload,
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "smoke_test",
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")


# ---------------------------------------------------------------------------
# Order dispatch
# ---------------------------------------------------------------------------


def _dispatch(
    signal: Dict[str, Any], settings: Dict[str, Any], dry_run: bool,
) -> Dict[str, Any]:
    """Call the production ``safe_place_order``; return its structured result."""
    from src.runtime.orders import safe_place_order

    if dry_run:
        # Forced dry-run: clone settings with the kill-switch tripped so
        # the production interlock blocks real submission. We do NOT
        # bypass ``safe_place_order`` — the whole point is to exercise
        # that exact code path.
        settings = {**settings, "DRY_RUN": "true", "ALLOW_LIVE_TRADING": "false"}
        client = None
    else:
        from src.exchange.bybit_connector import BybitConnector
        testnet = str(os.environ.get("BYBIT_TESTNET", "false")).strip().lower() == "true"
        client = BybitConnector(
            api_key=settings["BYBIT_API_KEY"],
            api_secret=settings["BYBIT_API_SECRET"],
            testnet=testnet,
        )
    return safe_place_order(signal, settings, client)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--account", required=True,
                   help="Account name from config/accounts.yaml (e.g. bybit_1).")
    p.add_argument("--qty", type=float, required=True,
                   help=f"Order quantity in BTC. Hard-capped at {MAX_SAFE_QTY}.")
    p.add_argument("--side", choices=("buy", "sell"), default="buy",
                   help="Open side. Smoke does this side then closes opposite.")
    p.add_argument("--note", default="S-017 plumbing smoke",
                   help="Free-form note recorded in meta.note.")
    p.add_argument("--dry-run", action="store_true",
                   help="Force DRY_RUN; safe_place_order will refuse the order.")
    p.add_argument("--no-close", action="store_true",
                   help="Skip the post-fill opposite-side close (rare; for "
                        "operator-driven flow control).")
    return p.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.qty > MAX_SAFE_QTY:
        logger.error("qty=%s exceeds MAX_SAFE_QTY=%s — refusing.",
                     args.qty, MAX_SAFE_QTY)
        return 2
    if args.qty <= 0:
        logger.error("qty must be > 0; got %s", args.qty)
        return 2

    if not args.dry_run:
        allow = str(os.environ.get("ALLOW_LIVE_TRADING", "")).strip().lower()
        if allow not in {"true", "1", "yes", "on"}:
            logger.error(
                "ALLOW_LIVE_TRADING is not set in the process env. "
                "Source the per-account .env file (which sets it) before "
                "running the LIVE smoke."
            )
            return 2

    settings = _account_settings(args.account)
    open_signal = _build_smoke_signal(args.side, args.qty, args.account, note=args.note)
    logger.info(
        "smoke OPEN [%s]: account=%s qty=%s side=%s smoke_id=%s",
        "DRY-RUN" if args.dry_run else "LIVE",
        args.account, args.qty, args.side, open_signal["meta"]["smoke_id"],
    )
    _log_smoke_event({"event": "smoke_open_attempt", **open_signal})

    open_result = _dispatch(open_signal, settings, args.dry_run)
    open_status = open_result.get("status", "unknown")
    open_reason = open_result.get("reason")
    logger.info("smoke OPEN result: status=%s reason=%s", open_status, open_reason)
    _log_smoke_event({
        "event": "smoke_open_result",
        "status": open_status,
        "reason": open_reason,
        **{"meta": open_signal["meta"]},
    })

    if open_status not in {"submitted", "ok"}:
        logger.warning(
            "OPEN was not submitted (status=%s). No close needed. "
            "This is a valid smoke outcome — plumbing-on-rejection still "
            "exercised the full path.", open_status,
        )
        return 1

    if args.no_close:
        logger.warning("--no-close set; leaving position open. Use /closeall.")
        return 0

    # Brief wait so the operator can confirm via /trades that the
    # position appeared, then flip it back.
    time.sleep(2)

    close_side = "sell" if args.side == "buy" else "buy"
    close_signal = _build_smoke_signal(
        close_side, args.qty, args.account,
        note=f"{args.note} — close",
    )
    close_signal["meta"]["smoke_id"] = open_signal["meta"]["smoke_id"]
    logger.info(
        "smoke CLOSE [%s]: side=%s qty=%s smoke_id=%s",
        "DRY-RUN" if args.dry_run else "LIVE",
        close_side, args.qty, close_signal["meta"]["smoke_id"],
    )
    _log_smoke_event({"event": "smoke_close_attempt", **close_signal})

    close_result = _dispatch(close_signal, settings, args.dry_run)
    close_status = close_result.get("status", "unknown")
    close_reason = close_result.get("reason")
    logger.info("smoke CLOSE result: status=%s reason=%s", close_status, close_reason)
    _log_smoke_event({
        "event": "smoke_close_result",
        "status": close_status,
        "reason": close_reason,
        "meta": close_signal["meta"],
    })

    if close_status not in {"submitted", "ok"}:
        logger.error(
            "CLOSE FAILED (status=%s reason=%s) — position may still be "
            "open. Operator must flatten manually via /closeall before "
            "the next smoke.", close_status, close_reason,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
