from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

# Writer path resolved through runtime_logs_dir() so DATA_DIR /
# RUNTIME_LOGS_DIR overrides apply consistently with the heartbeat +
# runtime_status writers. Pre-2026-05-11 this was hardcoded to
# ``Path(__file__).resolve().parents[2] / "runtime_logs"``, which
# diverged from runtime_logs_dir() the moment the OCI block-storage
# drop-in went in: liveness_watchdog + hourly_report read from the
# DATA_DIR-resolved path and found nothing while audit was being
# written at the repo path.
BASE = runtime_logs_dir()
SIGNAL_FILE = BASE / "signal_audit.jsonl"
SUMMARY_FILE = BASE / "summary_markers.json"


def signal_dual_write_enabled() -> bool:
    """Whether the SQL dual-write to ``trade_journal.db::signals`` is active.

    Single source of the ``SIGNAL_DUAL_WRITE_DISABLED`` gate — consulted by the
    writer here AND by the ``/api/bot/signals`` reader (WC-5 cutover). The
    coupling matters: the reader is DB-canonical ONLY while the dual-write runs;
    when the operator disables it (the rollback), the reader must fall back to
    the JSONL audit, never serve a frozen DB. Read at call time (next-event).
    """
    return os.environ.get("SIGNAL_DUAL_WRITE_DISABLED", "").strip().lower() not in {
        "true", "1", "yes", "on",
    }


# Fail-loud dedup state: escalate the FIRST failure of an episode to an
# ERROR outcome (surfaces on /api/bot/logs + alerts), then stay quiet until a
# write succeeds again — so a persistently-broken DB doesn't flood per signal,
# but a freshly-diverging dual-write (now that reads come from the DB) can't
# fail silently the way the old best-effort `logger.warning` did.
_dual_write_failing = False


def _dual_write_to_db(payload: Dict[str, Any]) -> None:
    """Also write *payload* to ``trade_journal.db::signals`` (fail-loud).

    S-034 → WC-5 cutover: the SQL signals log is now the **canonical** read
    source for ``/api/bot/signals``; the JSONL file is the append-only audit.
    This dual-write keeps the SQL log current. The opt-out env flag
    ``SIGNAL_DUAL_WRITE_DISABLED=true`` is the single rollback — it stops the
    SQL write here AND flips the reader back to the JSONL audit.

    **Never raises** (the JSONL write upstream is unconditional). But unlike the
    pre-cutover best-effort version, a failure now escalates ONCE per failure
    episode to an ERROR outcome — a silently-diverging DB would otherwise leave
    the dashboard stale, since reads come from the DB.
    """
    global _dual_write_failing
    if not signal_dual_write_enabled():
        return
    try:
        from src.units.db.database import Database
        db = Database()  # canonical resolver — never the bare-CWD fallback
        db.insert_signal(payload)
    except Exception as exc:  # noqa: BLE001
        if not _dual_write_failing:
            _dual_write_failing = True
            logger.error("signal_audit_logger: SQL dual-write FAILED: %s", exc)
            try:
                from src.runtime import outcomes

                outcomes.report(
                    "signal_dual_write",
                    "failed",
                    level=outcomes.Level.ERROR,
                    reason=str(exc),
                    note="/api/bot/signals reads the DB — a stalled dual-write "
                    "leaves the Signals panel stale until this recovers.",
                )
            except Exception:  # noqa: BLE001  # allow-silent: alerting is best-effort; the JSONL audit + logger.error above already record the failure, and the writer must never raise into the pipeline.
                pass
        else:
            logger.warning("signal_audit_logger: SQL dual-write still failing: %s", exc)
        return
    if _dual_write_failing:
        _dual_write_failing = False
        logger.info("signal_audit_logger: SQL dual-write recovered")


def log_signal(event: Dict[str, Any]) -> None:
    payload = dict(event or {})
    payload.setdefault("logged_at_utc", datetime.now(timezone.utc).isoformat())
    with SIGNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")
    # Dual-write to trade_journal.db::signals (S-034 transition).
    _dual_write_to_db(payload)
    # M12 S5 — mobile-push observer for buy/sell rows. Matches the
    # /api/bot/signals dashboard filter, so we only push on actual ICT
    # detections (skipping pipeline tick "candle observed" / "no signal"
    # events that would flood the operator's phone). The publish itself
    # is best-effort + feature-flagged + subscription-filtered by the
    # notifier — never propagates into the audit-writer path.
    try:
        _fire_signal_emitted_event(payload)
    except Exception:  # noqa: BLE001  # allow-silent: M12 S5 observer hook — notifier failure must never propagate into audit writer
        pass


def _fire_signal_emitted_event(payload: Dict[str, Any]) -> None:
    """Mirror a buy/sell signal to subscribed Android devices via FCM.

    Gating mirrors ``/api/bot/signals``'s server-side filter so the same
    rows the dashboard surfaces are the same rows that wake the phone —
    no surprise volume that the dashboard wouldn't have shown.

    Lazy import — mobile_push is a sibling module and a startup-ordering
    quirk (or a stripped env without google-auth) must never crash
    log_signal's main path.
    """
    side = str(payload.get("side", "")).lower()
    if side not in ("buy", "sell", "long", "short"):
        return
    from src.runtime.mobile_push import publish_event
    from src.runtime.mobile_push.event_kinds import SIGNAL_EMITTED

    # Subset of the audit row that's useful on a phone notification —
    # mirrors the Signals tab's SignalCard composition. Drop zones (too
    # big for an FCM data payload) and any field that's None.
    out = {
        "symbol": payload.get("symbol"),
        "side": payload.get("side"),
        "strategy": payload.get("strategy"),
        "pattern": payload.get("pattern"),
        "confidence": payload.get("confidence"),
        "price": payload.get("price"),
    }
    out = {k: v for k, v in out.items() if v is not None}
    publish_event(SIGNAL_EMITTED, out)


def _load_state() -> Dict[str, str]:
    if SUMMARY_FILE.exists():
        try:
            return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: Dict[str, str]) -> None:
    SUMMARY_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_send_summary(now_utc: datetime) -> bool:
    """Return True at most once per UTC hour.

    S-022 PR2: cadence flipped from twice-a-day (07:00 / 19:00) to once
    every hour. The slot key is now ``{YYYY-MM-DD}-{HH}`` so the existing
    dedupe machinery (last_slot in summary_markers.json) still applies —
    a tick loop that calls this multiple times within the same hour gets
    True only on the first call.
    """
    now_utc = now_utc.astimezone(timezone.utc)
    slot = f"{now_utc.date()}-{now_utc.hour:02d}"
    state = _load_state()
    if state.get("last_slot") == slot:
        return False
    state["last_slot"] = slot
    _save_state(state)
    return True
