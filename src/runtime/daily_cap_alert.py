"""Latching daily-loss-cap notification (operator-approved 2026-05-28).

Problem this closes: when an account exhausts its daily-loss cap, the
refusal surfaces as ``sized_qty=0`` at the coordinator's zero-qty gate —
a path that emits **no** Telegram ping (unlike the RiskBreach /
exchange_rejected paths). So a capped account went silent: the operator
got no clear "account X hit its daily cap" notification, and once the cap
reset at 00:00 UTC there was no "resumed" signal either.

This module is the **latch** that turns the per-tick cap state into
exactly two notifications per episode:

  * one ``exhausted`` ping the first time an account crosses into
    "daily cap met" for the day, and
  * one ``resumed`` ping when it next crosses back out (a new UTC day
    resets ``daily_pnl`` to ~0, or a recovering PnL lifts it above the
    cap).

State lives in a small JSON file under ``runtime_logs`` —
deliberately NOT in ``trade_journal.db`` so the money DB schema is
untouched. Accounts are reloaded fresh every dispatch tick (so an
in-memory latch on the per-account ``RiskManager`` would not persist);
the file is the cross-tick memory.

Best-effort throughout: any failure (read-only FS, corrupt state) logs
at DEBUG/WARN and never blocks dispatch. Worst case is a missed or
duplicated ping — never a stalled order path.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_STATE_FILENAME = "daily_cap_alert_state.json"


def _state_path():
    return runtime_logs_dir() / _STATE_FILENAME


def _load_state() -> dict:
    try:
        p = _state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("daily_cap_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        import os
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("daily_cap_alert: state save failed: %s", exc)


def note_account_cap_state(
    account_id: str,
    *,
    exhausted: bool,
    daily_pnl: Optional[float] = None,
    cap_usd: Optional[float] = None,
    demo: bool = False,
) -> Optional[str]:
    """Record the current cap state for *account_id*; ping on transition.

    Returns the kind of ping enqueued (``"exhausted"`` / ``"resumed"``)
    or ``None`` when the state was unchanged (no ping). Call once per
    account per dispatch round with the freshly-evaluated ``exhausted``
    flag (``RiskManager.is_daily_cap_exhausted(equity)``).
    """
    if not account_id:
        return None
    try:
        state = _load_state()
        prev = state.get(account_id) or {}
        prev_exhausted = bool(prev.get("exhausted", False))

        if exhausted == prev_exhausted:
            return None  # no transition → no ping (this is the latch)

        kind = "exhausted" if exhausted else "resumed"
        # Suppress a spurious "resumed" on the very first observation
        # (no prior record) — we only announce a resume if we previously
        # announced an exhaustion.
        announce = not (kind == "resumed" and not prev)
        state[account_id] = {"exhausted": exhausted}
        _save_state(state)

        if not announce:
            return None

        try:
            from src.runtime.execution_diagnostics import enqueue_daily_cap_alert
            enqueue_daily_cap_alert(
                account=account_id,
                kind=kind,
                daily_pnl=daily_pnl,
                cap_usd=cap_usd,
                demo=demo,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_cap_alert: enqueue failed for account=%s kind=%s: %s",
                account_id, kind, exc,
            )
        return kind
    except Exception as exc:  # noqa: BLE001
        logger.debug("daily_cap_alert: note_account_cap_state failed: %s", exc)
        return None
