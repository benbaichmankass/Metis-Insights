"""Latching broker-account-down alert (operator-requested 2026-06-29).

Problem this closes: when a supposed-to-be-live broker account goes
**unreachable** — the IB Gateway logs out (``ib_paper`` positions read
``None``, MES/MGC/MHG go dark), an exchange API starts 401-ing, creds
rotate out — nothing fires a *loud, standalone* operator alert. The IB
gateway watchdog only Telegrams after it has EXHAUSTED its restart
budget (a terse restart ping, easy to skim past); ``account_open_positions``
logs a WARN but routes no dedicated "account X is down" notification; and
a ``/system-review`` that runs while an account is dark surfaces it in the
report *body*, not as a can't-miss flag. The IB gateway was in fact dark
across one or more reviews and went unflagged — this module exists so that
can't happen again.

This is the **latch** that turns the per-tick reachability state into
exactly two notifications per episode, per account:

  * one ``🔴 [ALERT] Broker account DOWN`` ping the first time an account
    crosses into a *confirmed* unreachable state (``>= threshold``
    consecutive down reads, so a single transient blip never pings), and
  * one ``🟢 [OK] Broker account recovered`` ping when it next reads
    reachable again.

Scope (operator decision 2026-06-29): **all declared-live broker
accounts**. The "declared-live, non-shelved" set is derived from config,
NOT a hardcoded name list — an account is checked only when

  * ``mode == live`` (so the dry/shelved ``ib_live`` 2FA-blocked +
    ``oanda_practice`` are excluded — ``account_open_positions`` returns
    ``None`` for a dry account, which would otherwise look "down"), and
  * its exchange has a reachability primitive (``bybit`` /
    ``interactive_brokers`` / ``alpaca`` / ``oanda``) — which excludes the
    API-less ``breakout`` prop bridge.

An explicit ``ACCOUNT_DOWN_ALERT_SKIP`` CSV is the escape hatch for a live
account that is *intentionally* expected-down for a window.

Reachability is read via the SAME primitive the reverse reconciler already
calls every tick (``account_open_positions``): ``None`` ⇒ could-not-read
(down); a list (even empty ``[]``) ⇒ reachable. No new exchange round-trip
pattern; the reconciler already opens these clients each tick, so the
~10-min-cadenced extra read is negligible.

State lives in a small JSON file under ``runtime_logs`` (deliberately NOT
``trade_journal.db`` — the money DB schema is untouched), mirroring
``daily_cap_alert``. It persists across restarts so the consecutive-down
counter and the latch survive a trader bounce.

Best-effort throughout: any failure (read-only FS, corrupt state, a prober
exception) logs and never raises — this runs once per trader tick and must
never stall the loop. Worst case is a missed or duplicated alert, never a
blocked tick.

This is observability/alerting (not a trade-execution capability), and it
is **on by default** (the cadence knob only *tunes*; setting it ``<= 0``
pauses it) — the same shape as ``PROP_MONITOR_PULSE_SECONDS``, so the
no-default-off-gate Prime Directive is satisfied.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_STATE_FILENAME = "account_reachability_alert_state.json"
_LAST_CHECK_KEY = "__last_check__"

#: Exchanges with a reachability primitive in ``account_open_positions``.
#: ``breakout`` (the prop manual bridge) is deliberately absent — it has no
#: broker API to probe, so it is never an "account down" subject.
_SUPPORTED_EXCHANGES = frozenset(
    {"bybit", "interactive_brokers", "ib", "alpaca", "oanda"}
)


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
        logger.debug("account_reachability_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("account_reachability_alert: state save failed: %s", exc)


def _check_interval_seconds() -> int:
    """Cadence between actual probes (default 600s = 10 min).

    ``<= 0`` pauses the check without a redeploy (tuning/pause knob — the
    capability stays on-by-default; this never strands a trade path).
    """
    try:
        return int(os.environ.get("ACCOUNT_REACHABILITY_CHECK_SECONDS", "600"))
    except (TypeError, ValueError):
        return 600


def _down_threshold() -> int:
    """Consecutive down reads before a DOWN alert fires (default 2).

    Requiring >= 2 means a single transient read failure (a mid-reset
    gateway blip, a one-off network hiccup) never pings — only a sustained
    outage does.
    """
    try:
        n = int(os.environ.get("ACCOUNT_DOWN_ALERT_THRESHOLD", "2"))
        return n if n >= 1 else 1
    except (TypeError, ValueError):
        return 2


def _skip_set() -> frozenset:
    """Account-ids to skip (``ACCOUNT_DOWN_ALERT_SKIP`` CSV).

    Escape hatch for a live account that is *intentionally* expected-down
    for a window, so it doesn't latch a spurious alert.
    """
    raw = os.environ.get("ACCOUNT_DOWN_ALERT_SKIP", "") or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _checkable_accounts(
    cfgs: Dict[str, Dict[str, Any]],
) -> list[tuple[str, Dict[str, Any]]]:
    """Filter to declared-live, non-shelved, probe-able accounts."""
    skip = _skip_set()
    out: list[tuple[str, Dict[str, Any]]] = []
    for aid, cfg in cfgs.items():
        if not isinstance(cfg, dict):
            continue
        if aid in skip:
            continue
        exchange = str(cfg.get("exchange") or "").lower()
        if exchange not in _SUPPORTED_EXCHANGES:
            continue  # excludes breakout (prop manual bridge)
        mode = str(cfg.get("mode") or "live").lower()
        if mode != "live":
            continue  # excludes dry/shelved ib_live, oanda_practice
        out.append((aid, cfg))
    return out


def _remediation_hint(exchange: str) -> str:
    ex = (exchange or "").lower()
    if ex in ("interactive_brokers", "ib"):
        return (
            "Recommended: run the vm-ib-gateway-recover workflow to restart "
            "the IB Gateway, or open a /health-review session to investigate."
        )
    return (
        "Recommended: check the broker API status / credentials, or open a "
        "/health-review session to investigate."
    )


def _approx_window(consecutive: int, interval_s: int) -> str:
    mins = max(1, (interval_s // 60))
    return f"{consecutive} consecutive checks (~{mins}m apart)"


def _send_alert(message: str) -> None:
    """Fire the operator-facing alert: one Telegram + one loud WARNING push.

    ``send_telegram_direct`` would normally mirror to FCM as the generic
    ``telegram`` kind; we suppress that mirror and publish a typed
    ``WARNING`` event instead, so the phone gets a single push on the loud
    Warning channel (the same channel watchdog / health-red / service-down
    alerts use) rather than two pushes.
    """
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "account_reachability_alert: telegram send failed: %s", exc
        )
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "account_reachability_alert: fcm WARNING publish failed: %s", exc
        )


def _alert_down(
    account_id: str, exchange: str, consecutive: int, interval_s: int
) -> None:
    msg = (
        f"\U0001F534 [ALERT] Broker account DOWN: {account_id} ({exchange})\n"
        f"Read unreachable for {_approx_window(consecutive, interval_s)}. "
        "Trades on this account may be unprotected or going dark.\n"
        f"{_remediation_hint(exchange)}"
    )
    _send_alert(msg)


def _alert_recovered(account_id: str, exchange: str) -> None:
    msg = (
        f"\U0001F7E2 [OK] Broker account recovered: {account_id} "
        f"({exchange}) — reachable again."
    )
    _send_alert(msg)


def down_accounts() -> Dict[str, Dict[str, Any]]:
    """Return ``{account_id: state}`` for accounts currently LATCHED down.

    Read-only view of the latch state for the review skills (a down live
    account is a mandatory flags_raised item). Never raises — returns ``{}``
    on any read failure.
    """
    try:
        state = _load_state()
        return {
            aid: st
            for aid, st in state.items()
            if aid != _LAST_CHECK_KEY
            and isinstance(st, dict)
            and bool(st.get("down"))
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("account_reachability_alert: down_accounts failed: %s", exc)
        return {}


def run_account_reachability_check(
    *,
    now: Optional[datetime] = None,
    prober: Optional[Callable[[Dict[str, Any]], Optional[list]]] = None,
    cfgs: Optional[Dict[str, Dict[str, Any]]] = None,
    force: bool = False,
) -> dict:
    """Probe every declared-live account; latch + alert on down/recovery.

    Call once per trader tick — an internal cadence gate
    (``ACCOUNT_REACHABILITY_CHECK_SECONDS``, default 600s) rate-limits the
    actual probing so a 60s tick still only checks every ~10 min.

    Returns a small summary dict (``checked`` / ``newly_down`` /
    ``recovered`` / ``alerted`` / ``skipped``). Best-effort: never raises.
    """
    try:
        interval = _check_interval_seconds()
        if interval <= 0 and not force:
            return {"skipped": "disabled"}

        now = now or datetime.now(timezone.utc)
        state = _load_state()

        if not force:
            last = state.get(_LAST_CHECK_KEY)
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last))
                    if (now - last_dt).total_seconds() < interval:
                        return {"skipped": "cadence"}
                except Exception:  # noqa: BLE001
                    pass  # unparseable timestamp → run now, re-stamp below

        state[_LAST_CHECK_KEY] = now.isoformat()

        if cfgs is None:
            from src.runtime.order_monitor import _load_account_cfgs_for_reconcile
            cfgs = _load_account_cfgs_for_reconcile()
        if prober is None:
            from src.units.accounts.clients import account_open_positions
            prober = account_open_positions

        threshold = _down_threshold()
        checked = newly_down = recovered = alerted = 0

        for aid, cfg in _checkable_accounts(cfgs or {}):
            checked += 1
            exchange = str(cfg.get("exchange") or "?")
            try:
                positions = prober(cfg)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "account_reachability_alert: prober raised for %s: %s",
                    aid, exc,
                )
                positions = None
            reachable = positions is not None

            prev = state.get(aid) or {}
            prev_down = bool(prev.get("down", False))
            prev_consec = int(prev.get("consecutive_down", 0) or 0)

            if reachable:
                if prev_down:
                    recovered += 1
                    _alert_recovered(aid, exchange)
                    alerted += 1
                    state[aid] = {
                        "down": False,
                        "consecutive_down": 0,
                        "last_change": now.isoformat(),
                    }
                else:
                    state[aid] = {
                        "down": False,
                        "consecutive_down": 0,
                        "last_change": prev.get("last_change"),
                    }
            else:
                consec = prev_consec + 1
                cross_into_down = (not prev_down) and consec >= threshold
                state[aid] = {
                    "down": prev_down or consec >= threshold,
                    "consecutive_down": consec,
                    "last_change": (
                        now.isoformat() if cross_into_down
                        else prev.get("last_change")
                    ),
                }
                if cross_into_down:
                    newly_down += 1
                    _alert_down(aid, exchange, consec, interval)
                    alerted += 1

        _save_state(state)
        return {
            "checked": checked,
            "newly_down": newly_down,
            "recovered": recovered,
            "alerted": alerted,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "account_reachability_alert: run_account_reachability_check "
            "failed: %s", exc,
        )
        return {"error": str(exc)}
