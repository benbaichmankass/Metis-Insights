"""Latching trainer-VM-down alert (operator-requested 2026-07-08).

Problem this closes: the **trainer VM** (`ict-trainer-vm`, `158.178.209.121`)
can go completely down — SSH-unreachable, OOM-killed, hung — and *nothing*
fires a loud operator alert. A `/system-review` that runs while the trainer
is dark surfaces it in the report body, not as a can't-miss notification; the
ML training lifecycle silently stops (no new cycles, no dataset builds, stale
sweeps) and the first anyone hears of it is a review days later. The operator's
directive after the 2026-07-08 review (the trainer was SSH-dead across two
relay attempts and went otherwise unflagged): "the trainer can't be down and
we not know about that — that is absolutely unacceptable. There needs to be a
Telegram ping, a warning on the app, and a banner at the top of the UI."

This module is the **detection + latch** half (the Telegram + FCM WARNING
half); the API banner (`/api/bot/notifications`) reads :func:`status` so the
dashboard + Android surface it too.

**Liveness signal — already on the live VM, no new SSH / cron.** The trainer
runs `scripts/ops/publish_trainer_mirror.sh` on a **2-minute heartbeat timer**
(`ict-trainer-publish.timer`) that rsyncs `trainer_status.json` into
`runtime_logs/trainer_mirror/` on the live VM. So that file's mtime advances
every ~2 min while the trainer is healthy and goes **stale within minutes** the
moment the trainer VM is down/hung — the same signal the dashboard's
`mirror_age_seconds` already uses. We read its age here (no trader→trainer SSH,
no scheduled workflow): a mirror older than `TRAINER_DOWN_STALE_SECONDS`
(default 1200s = 20 min ≈ 10 missed publishes, so a single rsync hiccup can't
false-trip it) is a confirmed DOWN, and the staleness window is itself the
confidence — no separate consecutive-reads counter needed.

Exactly two notifications per episode:

  * one ``🔴 [ALERT] Trainer VM DOWN`` ping the first time the mirror crosses
    into stale (Telegram + one loud WARNING FCM push), and
  * one ``🟢 [OK] Trainer VM recovered`` ping when the mirror is fresh again.

State lives in a small JSON file under ``runtime_logs`` (NOT ``trade_journal.db``
— the money DB schema is untouched), persisting across restarts so the latch
survives a trader bounce. Best-effort throughout: any failure logs and never
raises — this runs once per trader tick and must never stall the loop.

Observability/alerting (not a trade-execution capability), **on by default**
(the cadence knob only *tunes*; `TRAINER_HEARTBEAT_CHECK_SECONDS <= 0` pauses
it) — the same shape as ``account_reachability_alert`` /
``PROP_MONITOR_PULSE_SECONDS``, so the no-default-off-gate Prime Directive is
satisfied.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_STATE_FILENAME = "trainer_reachability_alert_state.json"
_MIRROR_SUBDIR = "trainer_mirror"
_STATUS_FILENAME = "trainer_status.json"


def _state_path():
    return runtime_logs_dir() / _STATE_FILENAME


def _status_path():
    return runtime_logs_dir() / _MIRROR_SUBDIR / _STATUS_FILENAME


def _load_state() -> dict:
    try:
        p = _state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("trainer_reachability_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("trainer_reachability_alert: state save failed: %s", exc)


def _check_interval_seconds() -> int:
    """Cadence between checks (default 300s = 5 min).

    The check is a cheap local-file stat, so a modest gate is plenty. ``<= 0``
    pauses the check without a redeploy (tuning/pause knob — the capability
    stays on-by-default; it never strands a trade path).
    """
    try:
        return int(os.environ.get("TRAINER_HEARTBEAT_CHECK_SECONDS", "300"))
    except (TypeError, ValueError):
        return 300


def _stale_threshold_seconds() -> int:
    """Mirror age (seconds) beyond which the trainer is DOWN (default 1200).

    The publish cadence is ~2 min, so 20 min ≈ 10 missed publishes — a single
    rsync hiccup or a one-off slow cycle can never reach it, only a genuine
    trainer-down does. Clamped to a sane floor so a mis-set tiny value can't
    spam.
    """
    try:
        n = int(os.environ.get("TRAINER_DOWN_STALE_SECONDS", "1200"))
        return n if n >= 300 else 300
    except (TypeError, ValueError):
        return 1200


def _skip() -> bool:
    """``TRAINER_DOWN_ALERT_SKIP`` truthy → don't alert (escape hatch).

    For a window where the trainer is *intentionally* down (a deliberate
    re-provision / migration) so it doesn't latch a spurious alert.
    """
    return str(os.environ.get("TRAINER_DOWN_ALERT_SKIP", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def _mirror_age_seconds() -> Optional[float]:
    """Age (seconds) of the trainer mirror's ``trainer_status.json``.

    ``None`` when the file is absent (the mirror has never been published, or
    the mirror dir is missing) — treated by the caller as DOWN (no fresh
    trainer signal). Mirrors the dashboard router's ``mirror_age_seconds``.
    """
    try:
        p = _status_path()
        if not p.exists():
            return None
        return max(0.0, time.time() - p.stat().st_mtime)
    except Exception as exc:  # noqa: BLE001
        logger.debug("trainer_reachability_alert: mirror age read failed: %s", exc)
        return None


def _last_ts() -> Optional[str]:
    """The trainer's self-reported ``ts`` from the mirror (best-effort)."""
    try:
        p = _status_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("ts") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _send_alert(message: str) -> None:
    """One Telegram + one loud WARNING FCM push (same shape as the account alert)."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trainer_reachability_alert: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("trainer_reachability_alert: fcm WARNING publish failed: %s", exc)


def _alert_down(age_seconds: Optional[float]) -> None:
    if age_seconds is None:
        age_txt = "no trainer_status.json in the mirror (never published, or mirror missing)"
    else:
        age_txt = f"trainer mirror stale for ~{int(age_seconds // 60)}m (no 2-min publish landing)"
    msg = (
        "\U0001F534 [ALERT] Trainer VM DOWN: ict-trainer-vm (158.178.209.121)\n"
        f"{age_txt}. ML training lifecycle has stalled — no new cycles, dataset "
        "builds, or sweeps are landing while it is down. Live shadow/advisory "
        "inference is unaffected.\n"
        "Recommended: trainer-vm-diag relay to probe it; if SSH-dead it likely "
        "needs an OCI-console reboot (see MB-20260705-TRAINER-OOM)."
    )
    _send_alert(msg)


def _alert_recovered() -> None:
    _send_alert(
        "\U0001F7E2 [OK] Trainer VM recovered: ict-trainer-vm — the 2-min mirror "
        "heartbeat is landing again; ML training lifecycle is back."
    )


def status() -> Dict[str, Any]:
    """Read-only view of the trainer-liveness state (for /api/bot/notifications).

    Never raises. ``down`` is the LATCHED state (survives restarts); ``present``
    reflects whether a mirror file exists at all.
    """
    try:
        st = _load_state()
        age = _mirror_age_seconds()
        return {
            "present": _status_path().exists(),
            "down": bool(st.get("down", False)),
            "age_seconds": age,
            "stale_threshold_seconds": _stale_threshold_seconds(),
            "last_ts": _last_ts(),
            "since": st.get("last_change"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("trainer_reachability_alert: status failed: %s", exc)
        return {"present": False, "down": False, "age_seconds": None}


def is_down() -> bool:
    """Quick latched-down boolean; never raises."""
    try:
        return bool(_load_state().get("down", False))
    except Exception:  # noqa: BLE001
        return False


def run_trainer_reachability_check(
    *,
    now: Optional[datetime] = None,
    age_seconds: Optional[float] = None,
    force: bool = False,
) -> dict:
    """Check the trainer mirror freshness; latch + alert on down/recovery.

    Call once per trader tick — an internal cadence gate
    (``TRAINER_HEARTBEAT_CHECK_SECONDS``, default 300s) rate-limits the actual
    check. ``age_seconds`` may be injected for tests (else read from the mirror).
    Returns a small summary dict. Best-effort: never raises.
    """
    try:
        interval = _check_interval_seconds()
        if interval <= 0 and not force:
            return {"skipped": "disabled"}
        if _skip():
            return {"skipped": "TRAINER_DOWN_ALERT_SKIP"}

        now = now or datetime.now(timezone.utc)
        state = _load_state()

        if not force:
            last = state.get("__last_check__")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last))
                    if (now - last_dt).total_seconds() < interval:
                        return {"skipped": "cadence"}
                except Exception:  # noqa: BLE001
                    pass  # unparseable → run now, re-stamp below

        state["__last_check__"] = now.isoformat()

        if age_seconds is None:
            age_seconds = _mirror_age_seconds()

        threshold = _stale_threshold_seconds()
        # ``age_seconds is None`` (no mirror file at all) ⇒ down; else compare.
        stale = age_seconds is None or age_seconds > threshold

        prev_down = bool(state.get("down", False))
        alerted = 0
        newly_down = recovered = 0

        if stale:
            if not prev_down:
                newly_down = 1
                alerted = 1
                _alert_down(age_seconds)
            state["down"] = True
            state["last_change"] = (
                now.isoformat() if not prev_down else state.get("last_change")
            )
        else:
            if prev_down:
                recovered = 1
                alerted = 1
                _alert_recovered()
            state["down"] = False
            state["last_change"] = (
                now.isoformat() if prev_down else state.get("last_change")
            )
        state["age_seconds"] = age_seconds

        _save_state(state)
        return {
            "down": stale,
            "newly_down": newly_down,
            "recovered": recovered,
            "alerted": alerted,
            "age_seconds": age_seconds,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "trainer_reachability_alert: run_trainer_reachability_check failed: %s",
            exc,
        )
        return {"error": str(exc)}
