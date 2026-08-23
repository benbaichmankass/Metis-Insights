"""Prop FILLS-staleness alert — the book demonstrably traded and nothing was
reported back.

WHY THIS EXISTS (P1 of the 2026-08-23 prop refinement,
``docs/audits/full-system-audit-2026-08-23.md`` § 11.3). The account-status
request (:mod:`src.prop.prop_status_request`) escalates when the **balance**
goes stale. There was **no equivalent for FILLS** — so when the report-back
path itself broke (the screenshot reader was returning HTTP 404 on a wrong
model id), three days of terminal prop trades went unrecorded and were found
only because the operator happened to send another screenshot
(``BL-20260823-PROP-JOURNAL-MISSING-THREE-DAYS-OF-TERMINAL-TRADES``). The
journal said the book was flat-ish and quiet; the venue disagreed by
**$111.86**.

⚠️ **THIS MUST NEVER KEY ON UNACTED TICKETS, AND DOES NOT** (operator
directive, 2026-08-23). On a manual bridge the operator is not always at the
terminal when a ticket is live, so an unanswered ticket is the **expected**
shape, not a defect — the operator has said so twice, and said explicitly that
ticket answer-rate "shouldn't be a metric of success here". An alert keyed on
unacted tickets would fire constantly on correct behaviour, which is the
desensitized-alarm P1 this repo has already paid for once
(``MB-20260719-DATASET-AUDIT-NOISE``). Both detectors below therefore require
**positive evidence that the book traded**, and neither can fire because a
human was busy.

TWO DETECTORS, BOTH PROOF-ANCHORED, DELIBERATELY NOT ONE.

* **A — ``crossed_unreported`` (the leading indicator).** An open prop
  position whose SL or TP was *already announced as crossed* by
  :mod:`src.prop.prop_sl_tp_alert` at least ``..._CROSSED_GRACE_HOURS`` ago,
  and which is *still* open in the journal. The crossing is a price
  observation, not an assumption: the bot fetched the bar and compared it to
  the level the ticket declared. A position whose stop has been through is
  almost certainly flat at the venue, so the journal row is stale. This fires
  DURING the blind window.
* **B — ``balance_moved_unreported`` (the backstop).** Two consecutive
  operator-reported ``prop_account_status`` snapshots whose ``balance`` differs
  by at least ``..._BALANCE_DELTA_USD``, with **zero** fills reported in the
  interval between them. A moved balance is realized PnL, so closes happened;
  no fill row means they were not recorded. This is what actually caught the
  incident, and it can only fire once the operator HAS reported — it is
  impossible for it to punish unavailability.

**B does not require the fills to RECONCILE with the delta, deliberately.**
Measured on the live journal: the 2026-08-19 snapshot pair moved **+$245.00**
while the two fills reported in that window summed to **+$235.97**, and the
2026-07-20→08-18 pair moved **−$78.61** against **−$68.61** of reported fills.
Both are correct-and-explained; a reconciliation test would have called both a
finding. The question is "was ANYTHING reported", not "does it add up" — fees,
funding and partial reports all break the arithmetic without breaking the
record.

THRESHOLD BASIS (state the population). Over the **10 consecutive snapshot
pairs** in the live ``prop_account_status`` table on 2026-08-23, the balance
deltas were −27.27, −72.00, −186.72, −1.28, −102.39, −78.61, −9.00, +245.00,
−0.14, −111.86. Seven had fills reported in the interval and are explained.
Three did not: −9.00, −0.14 and **−111.86** — the last being the real incident.
The default **$25** threshold therefore fires **exactly once on this history,
on the true positive**: it sits above the observed unexplained noise floor
(0.14, 9.00) and far below the $150 daily-loss limit, so nothing that could
matter to the account hides underneath it. Lowering it to $5 adds one alert;
lowering it to $0.01 adds two. This is a chosen value with a measured basis
behind it — not a tuned one, and n=10 is a small denominator.

STATES ARE NOT COLLAPSED. Per account the assessment carries an explicit
``balance_state`` and each open position a ``crossing_state``, because several
conditions look identical in a "no findings" count and mean opposite things:

  ``balance_state``   ``insufficient_snapshots`` (fewer than 2 rows — no delta
                      EXISTS, which is NOT "clean") · ``balance_unreadable``
                      (a row carries no numeric balance) · ``within_noise``
                      (we looked; the move is under the threshold) ·
                      ``explained`` (fills were reported in the interval) ·
                      ``unreported`` (the finding).
  ``crossing_state``  ``unknown`` (``prop_sl_tp_alert`` has no entry for this
                      position — *we did not look*, never "did not cross") ·
                      ``not_crossed`` · ``crossed_within_grace`` ·
                      ``crossed_unreported`` (the finding).

A journal read FAILURE grades nothing and latches nothing (``checked: False``,
``reason: "read_failed"``) — it is never "no account is missing fills".

**Baseline, not gated.** Cadence/threshold knobs only, no default-off
``*_ENABLED`` flag in front of a required observability capability (Prime
Directive), and an unparseable value falls back to its DEFAULT rather than to
zero — a typo must not silently switch off the only thing watching this class:

  ``PROP_FILLS_STALENESS_CHECK_SECONDS``          cadence (default 3600; ``<=0`` pauses)
  ``PROP_FILLS_STALENESS_BALANCE_DELTA_USD``      detector B threshold (default 25.0)
  ``PROP_FILLS_STALENESS_CROSSED_GRACE_HOURS``    detector A grace (default 6.0)
  ``PROP_FILLS_STALENESS_SKIP``                   CSV of account ids to skip

Latched per ``(account, finding-key)`` — mirroring
:mod:`src.runtime.silent_refusal_alert`, where a NEW cause on an already-
latched account is a NEW problem and must not be swallowed by the old latch.
A ``crossed_unreported`` latch clears with a ``[OK]`` ping when the position
leaves the open set (the close WAS reported); a ``balance_moved_unreported``
latch is pruned **silently** when a newer snapshot supersedes the pair,
because superseding the evidence does not repair the journal and an "[OK]"
there would be a false statement.

Read-only + best-effort: one SQLite read per cadence window, no socket, no
order path. Never raises into the trader tick.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATE_FILENAME = "prop_fills_staleness_state.json"
_LAST_CHECK_KEY = "__last_check__"

_DEFAULT_CHECK_SECONDS = 3600
_DEFAULT_BALANCE_DELTA_USD = 25.0
_DEFAULT_CROSSED_GRACE_HOURS = 6.0


# ── small helpers ─────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _int_knob(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an int knob, falling back to the DEFAULT on garbage — never to 0."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(float(raw)))
    except (TypeError, ValueError):
        return default


def _float_knob(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Read a float knob, falling back to the DEFAULT on garbage — never to 0."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val or val in (float("inf"), float("-inf")):
        return default
    return max(minimum, val)


def _skip_set() -> frozenset:
    raw = os.environ.get("PROP_FILLS_STALENESS_SKIP", "") or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _state_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / _STATE_FILENAME


def _load_state() -> dict:
    try:
        p = _state_path()
        if not p.exists():
            return {}
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("prop_fills_staleness: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        import json
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("prop_fills_staleness: state save failed: %s", exc)


def _sl_tp_state() -> Dict[str, Dict[str, Any]]:
    """The crossing timestamps ``prop_sl_tp_alert`` already recorded.

    Read through that module's own loader rather than re-opening its file, so
    the two can never disagree about the state's shape or location.
    """
    from src.prop.prop_sl_tp_alert import _load_state as load_sl_tp_state
    data = load_sl_tp_state()
    return data if isinstance(data, dict) else {}


# ── detector A: a crossed bracket that never got a close report ───────

def assess_crossings(
    positions: List[Dict[str, Any]],
    sl_tp_state: Dict[str, Dict[str, Any]],
    *,
    now: datetime,
    grace_hours: float,
) -> List[Dict[str, Any]]:
    """Grade each open position's crossing state. Pure; no I/O.

    An entry ABSENT from ``sl_tp_state`` grades ``unknown`` — ``prop_sl_tp_alert``
    has not looked at this position yet, which is emphatically not the same as
    "the level was not crossed" and must never be reported as clean.
    """
    out: List[Dict[str, Any]] = []
    cutoff = now - timedelta(hours=grace_hours)
    for pos in positions:
        key = str(pos.get("key") or "")
        entry = sl_tp_state.get(key)
        row: Dict[str, Any] = {
            "key": key,
            "account_id": pos.get("account_id"),
            "symbol": pos.get("symbol"),
            "direction": pos.get("direction"),
            "opened_at": pos.get("opened_at"),
            "crossing_state": "unknown",
            "level": None,
            "crossed_at": None,
            "hours_since_crossing": None,
        }
        if isinstance(entry, dict):
            stamps = [
                ("sl", _parse_iso(entry.get("sl_alerted_at"))),
                ("tp", _parse_iso(entry.get("tp_alerted_at"))),
            ]
            crossed = [(lvl, dt) for lvl, dt in stamps if dt is not None]
            if not crossed:
                row["crossing_state"] = "not_crossed"
            else:
                level, dt = min(crossed, key=lambda p: p[1])
                row["level"] = level
                row["crossed_at"] = dt.isoformat()
                row["hours_since_crossing"] = round(
                    (now - dt).total_seconds() / 3600.0, 2
                )
                row["crossing_state"] = (
                    "crossed_unreported" if dt <= cutoff else "crossed_within_grace"
                )
        out.append(row)
    return out


# ── detector B: a moved balance no fill explains ──────────────────────

def assess_balance_move(
    snapshots: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    *,
    delta_threshold: float,
) -> Dict[str, Any]:
    """Compare the two newest snapshots against the fills reported between them.

    ``snapshots`` newest-first. Returns a graded dict; never raises.

    The interval is half-open ``(prev.reported_at, latest.reported_at]`` so a
    fill reported in the same second as the snapshot that reflects it counts as
    explaining it — the 2026-07-17 pair in the live table has exactly that
    shape (fill 22:14:08, snapshot 22:14:19).
    """
    out: Dict[str, Any] = {
        "balance_state": "insufficient_snapshots",
        "delta": None,
        "prev_balance": None,
        "latest_balance": None,
        "prev_id": None,
        "latest_id": None,
        "window_start": None,
        "window_end": None,
        "fills_in_window": None,
    }
    if len(snapshots) < 2:
        return out

    latest, prev = snapshots[0], snapshots[1]
    out["prev_id"] = prev.get("id")
    out["latest_id"] = latest.get("id")
    lb, pb = _num(latest.get("balance")), _num(prev.get("balance"))
    out["prev_balance"], out["latest_balance"] = pb, lb
    if lb is None or pb is None:
        out["balance_state"] = "balance_unreadable"
        return out

    start, end = _parse_iso(prev.get("reported_at")), _parse_iso(latest.get("reported_at"))
    out["window_start"] = start.isoformat() if start else None
    out["window_end"] = end.isoformat() if end else None
    if start is None or end is None:
        # An undateable snapshot cannot bound a window, so we cannot say what
        # was reported inside it. That is "could not look", not "clean".
        out["balance_state"] = "balance_unreadable"
        return out

    delta = lb - pb
    out["delta"] = round(delta, 2)
    if abs(delta) < delta_threshold:
        out["balance_state"] = "within_noise"
        return out

    in_window = 0
    for f in fills:
        ts = _parse_iso(f.get("reported_at"))
        if ts is not None and start < ts <= end:
            in_window += 1
    out["fills_in_window"] = in_window
    out["balance_state"] = "explained" if in_window else "unreported"
    return out


# ── messages ──────────────────────────────────────────────────────────

def describe_crossing(row: Dict[str, Any], grace_hours: float) -> str:
    return (
        "\U0001F534 [ALERT] Prop trade may have closed unrecorded — "
        f"{row.get('symbol')} {row.get('direction')} on {row.get('account_id')}.\n"
        f"Its {str(row.get('level') or '').upper()} was crossed "
        f"{row.get('hours_since_crossing')}h ago "
        f"(grace {grace_hours:g}h) and the journal still has it OPEN, so no "
        "close has been reported back.\n"
        "If it is closed on the terminal, report the fill (a screenshot works). "
        "If it is genuinely still open, nothing to do — this fires once."
    )


def describe_balance(account_id: str, a: Dict[str, Any]) -> str:
    delta = a.get("delta")
    sign = "+" if (delta or 0) >= 0 else "-"
    return (
        f"\U0001F534 [ALERT] {account_id}: balance moved with NO fills reported.\n"
        f"{sign}${abs(delta):,.2f} between your last two reports "
        f"({a.get('window_start')} → {a.get('window_end')}), and zero fills "
        "were reported in that interval — so trades closed that the journal has "
        "no record of, and every per-trade number for that window is missing "
        "them.\n"
        "Report the missing fills if you have them. If the move was NOT trading "
        "(a deposit, withdrawal or broker adjustment), ignore this."
    )


def _send_alert(message: str) -> None:
    """One Telegram (prop bot) + one typed WARNING push — the loud channel, the
    same shape ``silent_refusal_alert`` uses, so this lands beside its siblings
    rather than inventing a second alert style for one class."""
    try:
        from src.prop.breakout_notify import _prop_bot_token
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False,
                             bot_token=_prop_bot_token())
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_fills_staleness: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("prop_fills_staleness: fcm WARNING publish failed: %s", exc)


# ── read-only view for the review skills ──────────────────────────────

def stale_fill_accounts() -> Dict[str, Dict[str, Any]]:
    """``{account_id: {finding_key: latch}}`` for accounts currently latched.

    Mirrors ``silent_refusal_alert.silent_accounts()`` /
    ``account_reachability_alert.down_accounts()`` so the review skills read
    this class the same way they read its siblings. Never raises.
    """
    try:
        state = _load_state()
        return {
            aid: st.get("findings", {})
            for aid, st in state.items()
            if aid != _LAST_CHECK_KEY
            and isinstance(st, dict)
            and st.get("findings")
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("prop_fills_staleness: stale_fill_accounts failed: %s", exc)
        return {}


# ── the tick entry point ──────────────────────────────────────────────

def run_prop_fills_staleness(
    *,
    now: Optional[datetime] = None,
    force: bool = False,
    alerter: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """One tick. Cadence-gated internally; call once per trader tick.

    Returns a small summary dict. `alerter` is a test seam. Never raises.
    """
    now = now or _now()
    send = alerter or _send_alert
    interval = _int_knob("PROP_FILLS_STALENESS_CHECK_SECONDS", _DEFAULT_CHECK_SECONDS)
    if interval <= 0:
        return {"checked": False, "reason": "paused"}

    state = _load_state()
    if not force:
        prev = _parse_iso(state.get(_LAST_CHECK_KEY))
        # An unparseable stamp means check NOW rather than never.
        if prev is not None and (now - prev).total_seconds() < interval:
            return {"checked": False, "reason": "cadence"}

    delta_threshold = _float_knob(
        "PROP_FILLS_STALENESS_BALANCE_DELTA_USD", _DEFAULT_BALANCE_DELTA_USD
    )
    grace_hours = _float_knob(
        "PROP_FILLS_STALENESS_CROSSED_GRACE_HOURS", _DEFAULT_CROSSED_GRACE_HOURS
    )

    from src.prop import prop_journal

    try:
        if not prop_journal.tables_present():
            # No prop journal at all — we cannot look, and that is not "clean".
            return {"checked": False, "reason": "tables_absent"}
        fills = prop_journal.list_fills(limit=2000)
    except Exception as exc:  # noqa: BLE001 — a read failure is not a finding
        logger.warning("prop_fills_staleness: fills read failed: %s", exc)
        return {"checked": False, "reason": "read_failed"}

    try:
        from src.prop.prop_monitor_pulse import find_open_prop_positions
        positions = find_open_prop_positions(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_fills_staleness: open-position scan failed: %s", exc)
        positions = []

    try:
        from src.prop.prop_identity import declared_prop_account_ids
        declared = declared_prop_account_ids(live_only=True) or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("prop_fills_staleness: declared ids unavailable: %s", exc)
        declared = []

    account_ids = sorted(
        {str(a) for a in declared}
        | {str(p.get("account_id")) for p in positions if p.get("account_id")}
    )
    skip = _skip_set()
    crossings = assess_crossings(
        positions, _sl_tp_state(), now=now, grace_hours=grace_hours
    )

    alerted: List[str] = []
    recovered: List[str] = []
    assessed: Dict[str, Dict[str, Any]] = {}

    for aid in account_ids:
        if aid in skip:
            continue
        prior = state.get(aid) if isinstance(state.get(aid), dict) else {}
        prior_findings = prior.get("findings") or {}
        findings: Dict[str, Dict[str, Any]] = {}

        # --- detector B -------------------------------------------------
        try:
            snapshots = prop_journal.list_account_status(aid, limit=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "prop_fills_staleness: status read failed for %s: %s", aid, exc
            )
            snapshots = []
            balance = {"balance_state": "read_failed"}
        else:
            balance = assess_balance_move(
                snapshots,
                [f for f in fills if str(f.get("account_id")) == aid],
                delta_threshold=delta_threshold,
            )
        if balance.get("balance_state") == "unreported":
            key = f"balance:{balance.get('prev_id')}->{balance.get('latest_id')}"
            findings[key] = {"kind": "balance_moved_unreported", **balance}

        # --- detector A -------------------------------------------------
        acct_crossings = [c for c in crossings if str(c.get("account_id")) == aid]
        for c in acct_crossings:
            if c["crossing_state"] == "crossed_unreported":
                findings[f"crossed:{c['key']}"] = {
                    "kind": "crossed_unreported", **c
                }

        # --- latch --------------------------------------------------------
        for key, f in findings.items():
            if key in prior_findings:
                continue
            try:
                send(
                    describe_balance(aid, f)
                    if f["kind"] == "balance_moved_unreported"
                    else describe_crossing(f, grace_hours)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("prop_fills_staleness: alert send failed: %s", exc)
            alerted.append(f"{aid}/{key}")

        # A crossed-unreported latch clears with an [OK] when the position
        # leaves the open set — that IS the close being reported. A balance
        # latch is pruned silently: a newer snapshot supersedes the evidence
        # without repairing the journal, so "[OK]" there would be false.
        open_keys = {f"crossed:{c['key']}" for c in acct_crossings}
        for key, f in prior_findings.items():
            if key in findings:
                continue
            if str(f.get("kind")) == "crossed_unreported" and key not in open_keys:
                try:
                    send(
                        f"\U0001F7E2 [OK] {aid}: {f.get('symbol')} "
                        f"{f.get('direction')} is closed in the journal — the "
                        "close was reported."
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "prop_fills_staleness: recovery send failed: %s", exc
                    )
                recovered.append(f"{aid}/{key}")

        assessed[aid] = {
            "balance_state": balance.get("balance_state"),
            "delta": balance.get("delta"),
            "fills_in_window": balance.get("fills_in_window"),
            "crossings": {
                c["key"]: c["crossing_state"] for c in acct_crossings
            },
            "findings": len(findings),
        }
        state[aid] = {"findings": findings, "updated_at": now.isoformat()}

    for aid in [k for k in state if k != _LAST_CHECK_KEY and k not in assessed]:
        state.pop(aid, None)
    state[_LAST_CHECK_KEY] = now.isoformat()
    _save_state(state)

    return {
        "checked": True,
        "accounts": len(assessed),
        "alerted": alerted,
        "recovered": recovered,
        "assessed": assessed,
    }


__all__ = [
    "run_prop_fills_staleness",
    "stale_fill_accounts",
    "assess_balance_move",
    "assess_crossings",
    "describe_balance",
    "describe_crossing",
]
