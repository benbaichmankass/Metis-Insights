"""Latched alert for an account on a SUSTAINED run of losing days.

WHY THIS EXISTS (Tier-2, 2026-09-11). ``bybit_1`` lost money on **16
consecutive days**, 2026-08-27 → 2026-09-11, a drawdown of **$36,997**, and
**the operator found it, not a monitor** — measured and attributed in
``docs/research/bleed-attribution-2026-09-11.md`` (MI-271), whose § 6 opens
"the detector that is owed regardless" and says the clause is owed *whatever
the attribution turns out to be*. Every existing per-account watcher is
structurally blind to it, and each for its own reason:

  * ``silent_refusal_alert`` grades whether orders REACH the venue. An account
    placing orders that all lose is not refusing anything; it grades
    ``ok``/absent.
  * ``account_reachability_alert`` probes ``positions()``. A bleeding account
    is perfectly reachable.
  * ``daily_cap_alert`` and the RiskManager's daily-loss counters are
    PER-DAY. Sixteen days each individually inside the cap never trip a
    per-day threshold — that is precisely the shape that hid this.
  * ``/health-review``'s strategy-silence check reads ``*_eval`` events. The
    legs were evaluating and signalling throughout.

So "declared live, trading, reachable, and losing every single day for two
weeks" sat in the gap between all of them.

THIS DOES NOT PROBE THE BROKER. It opens one read-only SQLite connection on
its own cadence and no socket at all, so it cannot slow, block or wedge the
loop it observes — the invariant ``account_reachability_alert`` declares and
``silent_refusal_alert`` inherits.

THE FALSE-POSITIVE COUNT IS THE DESIGN EVIDENCE, AND THE SIZE GATE IS THE
LOAD-BEARING TERM — not the day count. MEASURED 2026-09-11 over the journal
tail ``/api/diag/journal?table=trades&limit=1000`` (ids 4701–5700, closed +
non-backtest + ``pnl NOT NULL`` = **562 rows**, **6 accounts**, 26 calendar
days 2026-08-17 → 2026-09-11), sweeping the rule over every maximal losing
run:

    rule                                    fires over the whole window
    N>=4, no size gate                      6  (incl. bybit_2 x2 — NOISE)
    N>=4, cumulative loss >= $100           4
    N>=4, cumulative loss >= $500           4  (0 noise)   <- the shipped default

⚠️ **RAISING ``N`` DOES NOT SEPARATE ``bybit_2``, AND THIS CORRECTS MI-271 § 6
ON ITS OWN NUMBERS.** That memo reports N>=3 misfiring on ``bybit_2`` and
concludes that "at N>=4 the rule fires exactly once per account over the whole
window — on the real event, with zero false positives". Re-measured here,
``bybit_2``'s longest losing run is **10 days totalling −$56.15**, a median
absolute daily PnL of **$5.10**. So its N>=4 fire is not "the real event" — it
is the same dollar-scale noise the memo correctly rejected one threshold
earlier, and it survives N>=5 and N>=6 as well. **Only an absolute size gate
removes it.** The memo's own remedy list says a minimum-size gate is required;
what is corrected is the claim that N>=4 alone is already clean.

At the shipped defaults the surviving fires are ``bybit_1`` (the real event,
16 days / −$36,048 — reproducing MI-271's 16 days / −$36,997 from a different
basis), ``bybit_portfolio`` x2 (the same event on the size-scaled mirror book,
split by its own 4-day silence) and ``alpaca_portfolio`` (a genuine 4-day
−$3,166 run). **Zero are noise.** That is ~1 alert per 6.5 fleet-days.

⚠️ **STATE THE POPULATION.** Those numbers come from a **1000-row TAIL** of
the journal, which reaches back 26 days — not a lifetime. A 90-day
false-positive rate has no denominator here and is deliberately not estimated.
Six accounts is also the whole fleet that CLOSED anything in the window, not
the whole roster.

⚠️ **$500 IS A CHOSEN VALUE WITH A MEASURED BASIS, NOT A TUNED ONE**, and an
absolute fleet-wide floor is a compromise that is stated rather than hidden:
it sits above ``bybit_2``'s worst observed noise run ($56) and far below the
smallest substantive run observed ($3,166), but it necessarily under-serves a
small real-money account for which a sub-$500 bleed would still matter. Filed
rather than papered over.

THE STATES ARE NOT COLLAPSED. Four conditions produce "no alert" and mean
different things:

  * ``insufficient_days`` — fewer than ``min_days`` gradeable days exist for
    this account in the window, so a streak of that length **cannot exist**.
    *We could not look.* Emphatically not "the account is fine": a book with
    no closes is not a book that is winning.
  * ``unreadable`` — the account DID close trades in the window and not one of
    them carried a usable PnL. We looked and could not read it, which is again
    not a clean bill.
  * ``no_streak`` — graded, and the current run is shorter than ``min_days``
    or below the loss floor.
  * ``streak_active`` — the finding.

PROVENANCE IS RESPECTED, SO A STREAK CANNOT BE MANUFACTURED FROM RECONSTRUCTED
ROWS (MI-271 § 6). ``FABRICATED`` PnL is *synthesised with no anchor to the
close* — the class behind the phantom −$6,358 exit leak — so it is excluded
from every daily sum through the one canonical module
(``src.runtime.provenance``), never a bespoke predicate. ``MEASURED`` and
``ESTIMATED`` are summed and **counted separately**, and the resulting
coverage rides in the alert body so the operator can see how much of a streak
is broker truth. Measured on the window above: 129 measured / 372 estimated /
61 unverified / **0 fabricated**, so the exclusion changes nothing today and
exists for the day it does.

⚠️ **A GAP IN OBSERVED DAYS BREAKS THE RUN.** A day on which an account closed
nothing is *we did not look* — it neither extends nor breaks a streak on its
own — but a run that resumes after a long silence is not "consecutive losing
days" in any sense the operator means, so a gap wider than
``LOSING_STREAK_MAX_GAP_DAYS`` ends it. Measured effect on the window above:
it splits ``bybit_portfolio``'s 10-day run in two across its own 4-day
silence, which is the honest reading.

WHAT THIS DELIBERATELY DOES NOT BUILD. MI-271 § 6 proposes "N consecutive
losing days **OR** drawdown-from-peak > X%". Only the first half ships. The
drawdown half needs a per-account equity basis, and ``trades`` carries
realised PnL only — no starting equity — so an equity curve built from it
would be a level inferred from a series of deltas with an invented origin,
which is a fabricated denominator wearing a percentage sign. ``daily_risk_state``
does carry an equity high-water mark and is the right input for it; that is a
separate unit, and saying so is better than shipping a percentage nobody can
source.

LEVEL IS ``WARNING``, NEVER ``CRITICAL``. CRITICAL is reserved for a position
that is UNPROTECTED or REVERSED. A losing streak is a book that is working as
designed and losing, which is a different and quieter fact — and spending the
top channel on it is exactly how the top channel stops being read (202 of 376
rows in one measured ERROR+ window were a single un-latched alarm).

Knobs are cadence/threshold only — no default-off ``*_ENABLED`` gate in front
of a required observability capability (Prime Directive), and an unparseable
value falls back to its DEFAULT rather than to zero, so a typo cannot silently
switch the watch off:

  ``LOSING_STREAK_CHECK_SECONDS``   cadence between reads (default 3600)
  ``LOSING_STREAK_LOOKBACK_DAYS``   how far back to read (default 30)
  ``LOSING_STREAK_MIN_DAYS``        consecutive losing days (default 4)
  ``LOSING_STREAK_MIN_LOSS_USD``    absolute cumulative-loss floor (default 500)
  ``LOSING_STREAK_MAX_GAP_DAYS``    silence that ends a run (default 3)
  ``LOSING_STREAK_SKIP``            CSV escape hatch, mirrors ACCOUNT_DOWN_ALERT_SKIP

THE LATCH IS THE SHARED, DURABLE ONE (``src.runtime.alert_cooldown``), never a
copy and never ``time.monotonic()``. Copying a latch is how the per-PROCESS
defect comes back in the copy: ``target_naked``'s first implementation keyed a
module global on a monotonic clock against a condition that outlives any
process, the trader restarts on every merge to ``main``, and the alert became
202 of 376 rows of the whole operator ERROR+ feed
(BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART). A losing streak
outlives a restart by construction — it is measured in days — so this is
squarely the case that module exists for.

``severity`` IS THE STREAK LENGTH IN DAYS, which is a genuinely monotone
worsening measure: 4 -> 5 -> 6 consecutive losing days is unambiguously worse,
so the one-directional severity rule pages on a lengthening streak and stays
silent on one that merely persists. An account whose streak SHORTENS is
improving and must not page — that asymmetry is the whole reason to use the
shared primitive rather than folding the number into the key.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.runtime import alert_cooldown as _alert_cooldown
from src.runtime.provenance import FABRICATED, classify_pnl, coverage
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

#: The cooldown ``kind``. Resolves to ``runtime_logs/losing_streak_alert_state.json``
#: through :func:`src.runtime.alert_cooldown.state_path`; that file is
#: registered on ``/api/diag/log_file`` IN THE SAME COMMIT as this writer,
#: because a latch that suppresses a page and cannot be inspected is strictly
#: worse than no latch (the ``exit_loop_health`` #8778 lesson).
ALERT_KIND = "losing_streak"

#: Separate from the cooldown latch on purpose, and it MUST NOT share its
#: filename. The cooldown answers "may this key page now?" and stores
#: ``{key: timestamp}``; this answers "what did we last OBSERVE, and when did
#: we last look?" and stores ``{account: {...verdict}}`` — which is what the
#: review skills and the notifications banner read, and what makes a silent
#: detector distinguishable from a healthy fleet.
#:
#: ⚠️ ``alert_cooldown.state_path`` resolves a ``kind`` to
#: ``<kind>_alert_state.json``, so :data:`ALERT_KIND` already owns
#: ``losing_streak_alert_state.json``. Naming this file the same thing — which
#: the sibling-detector convention invites — makes the two writers clobber each
#: other with incompatible shapes: the cooldown's TTL prune drops every value
#: that is not a number, so one pass would silently delete every observation,
#: and the surviving latch would then be parsed as verdicts. The failure is
#: TOTAL and SILENT (both writers swallow their own exceptions), and it
#: disables a detector while leaving it looking installed. A test pins the two
#: names apart.
_STATE_FILENAME = "losing_streak_observed_state.json"
_LAST_CHECK_KEY = "__last_check__"

#: One page per account per day at most. The condition is measured in DAYS, so
#: a tighter window would re-page several times about the same day's data and a
#: looser one would let a lengthening streak go unremarked for longer than the
#: quantity it reports.
COOLDOWN_S: float = 24 * 3600.0

# ── the four states, never collapsed ──────────────────────────────────────
#: The finding: a run of losing days at or past both thresholds.
STREAK_ACTIVE = "streak_active"
#: Graded, and the current run is short or small. The only clean negative.
STREAK_NONE = "no_streak"
#: Fewer than ``min_days`` gradeable days exist — a streak of that length
#: CANNOT exist yet. *We could not look*, never "the account is fine".
STREAK_INSUFFICIENT = "insufficient_days"
#: The account closed trades and none carried a readable, non-fabricated PnL.
#: We looked and could not read it — also not a clean bill.
STREAK_UNREADABLE = "unreadable"

STREAK_STATES: Tuple[str, ...] = (
    STREAK_ACTIVE, STREAK_NONE, STREAK_INSUFFICIENT, STREAK_UNREADABLE,
)


def _int_knob(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an int knob, falling back to the DEFAULT on garbage.

    Never falls back to 0/disabled: a typo in a threshold must not silently
    switch off the only thing watching for this failure class.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(minimum, val)


def _float_knob(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, val)


def _skip_set() -> frozenset:
    raw = os.environ.get("LOSING_STREAK_SKIP", "") or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


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
        logger.debug("losing_streak_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("losing_streak_alert: state save failed: %s", exc)


def _flatten(row: Any) -> Dict[str, Any]:
    """A plain dict carrying the provenance keys alongside the columns.

    ``exit_price_source`` / ``pnl_source`` live inside the ``notes`` JSON blob
    rather than as columns (``provenance`` module docstring), and
    ``classify_pnl`` reads them off the row. Lifting them here keeps the
    canonical classifier as the ONE owner of the vocabulary rather than
    re-deriving a second predicate — which is what
    ``provenance-consumer-guard`` exists to prevent.
    """
    try:
        d = dict(row)
    except (TypeError, ValueError):
        return {}
    notes = d.get("notes")
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except (TypeError, ValueError):
            notes = None
    if isinstance(notes, dict):
        for k in ("pnl_source", "exit_price_source"):
            if k in notes and k not in d:
                d[k] = notes[k]
    return d


def _day_of(row: Dict[str, Any]) -> Optional[str]:
    """The UTC calendar day a close belongs to — CLOSE time, never open time.

    A trade opened on day A and closed on day B realises its PnL on B, which is
    the day whose book moved. Splitting on ``created_at`` would attribute a
    loss to the day the position was entered and smear a streak across days
    nothing happened on.
    """
    for key in ("closed_at", "timestamp", "created_at"):
        v = row.get(key)
        if v:
            text = str(v)[:10]
            try:
                date.fromisoformat(text)
            except ValueError:
                continue
            return text
    return None


def daily_pnl(rows: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """``{account_id: {day: {pnl, n, measured, estimated, unverified, fabricated}}}``.

    PURE — no I/O, no env, no notification — so the policy is arguable in tests
    rather than against a live book.

    FABRICATED rows are counted and EXCLUDED from ``pnl``. A fabricated PnL is
    synthesised with no anchor to the close, and a streak assembled from them
    would be a finding about our own arithmetic rather than about the market.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        row = _flatten(raw)
        if not row:
            continue
        if row.get("status") != "closed":
            continue
        if row.get("is_backtest"):
            continue
        acct = row.get("account_id")
        day = _day_of(row)
        if not acct or not day:
            continue
        bucket = out.setdefault(str(acct), {})
        cell = bucket.setdefault(
            day, {"pnl": 0.0, "n": 0, "measured": 0, "estimated": 0,
                  "unverified": 0, "fabricated": 0, "gradeable": 0},
        )
        cell["n"] += 1
        pnl = row.get("pnl")
        if pnl is None:
            continue
        try:
            value = float(pnl)
        except (TypeError, ValueError):
            continue
        verdict, _ = classify_pnl(row)
        cell[verdict] = cell.get(verdict, 0) + 1
        if verdict == FABRICATED:
            # Counted above, deliberately not summed. See the docstring.
            continue
        cell["pnl"] += value
        cell["gradeable"] += 1
    return out


def current_run(days: Dict[str, Any], *, max_gap_days: int) -> Dict[str, Any]:
    """The losing run ENDING at the most recent gradeable day, if any.

    PURE. Returns ``{"length", "loss", "start", "end", "graded_days"}``.
    ``length`` is 0 when the newest gradeable day was not a loss.

    A day with no gradeable close is *we did not look*: it does not break the
    run by itself, because absence of trading is not a winning day. A silence
    LONGER than ``max_gap_days`` does break it — a run resuming after a week is
    not "consecutive" in any sense an operator means by the word.
    """
    gradeable = sorted(d for d, c in days.items() if c.get("gradeable"))
    if not gradeable:
        return {"length": 0, "loss": 0.0, "start": None, "end": None,
                "graded_days": 0}
    length = 0
    loss = 0.0
    start: Optional[str] = None
    end = gradeable[-1]
    prev: Optional[date] = None
    for day in reversed(gradeable):
        cur = date.fromisoformat(day)
        if prev is not None and (prev - cur).days > max(0, int(max_gap_days)):
            break
        if days[day]["pnl"] >= 0:
            break
        length += 1
        loss += days[day]["pnl"]
        start = day
        prev = cur
    return {"length": length, "loss": loss, "start": start,
            "end": end if length else None, "graded_days": len(gradeable)}


def assess(
    rows: Sequence[Any],
    *,
    min_days: int,
    min_loss_usd: float,
    max_gap_days: int,
) -> Dict[str, Dict[str, Any]]:
    """Grade every account that produced closed rows in the window.

    PURE. An account absent from ``rows`` was NOT OBSERVED and is deliberately
    absent from the result rather than graded healthy — the distinction
    ``silent_refusal_alert`` had to be corrected into keeping.
    """
    graded: Dict[str, Dict[str, Any]] = {}
    for acct, days in daily_pnl(rows).items():
        run = current_run(days, max_gap_days=max_gap_days)
        counts = {
            k: sum(int(c.get(k, 0)) for c in days.values())
            for k in ("measured", "estimated", "unverified", "fabricated")
        }
        # `provenance.coverage` keys its denominator on an explicit `total`
        # and returns None without one. Supplying it here rather than dividing
        # by hand keeps that module the ONE owner of the ratio — a second,
        # local definition of "coverage" is exactly the drift
        # `provenance-consumer-guard` exists to stop.
        counts["total"] = sum(counts.values())
        if run["graded_days"] == 0:
            # Rows exist, none carries a readable non-fabricated PnL.
            state = STREAK_UNREADABLE
        elif run["graded_days"] < min_days:
            state = STREAK_INSUFFICIENT
        elif run["length"] >= min_days and abs(run["loss"]) >= min_loss_usd:
            state = STREAK_ACTIVE
        else:
            state = STREAK_NONE
        graded[acct] = {
            "state": state,
            "streak_days": run["length"],
            "streak_loss_usd": round(run["loss"], 2),
            "streak_start": run["start"],
            "streak_end": run["end"],
            # STATE THE DENOMINATOR beside every count: how many days we could
            # grade at all, not just how many were losses.
            "graded_days": run["graded_days"],
            "observed_days": len(days),
            "rows": sum(int(c.get("n", 0)) for c in days.values()),
            "provenance": counts,
            # None, never 0.0, when nothing is gradeable — zero coverage and
            # "no denominator" are different facts.
            "pnl_coverage": coverage(counts),
            # Why it did NOT alert, when it did not. A bare `alerting: false`
            # collapses "below the day threshold" into "below the size floor"
            # into "we could not look", and those have different remedies.
            "below_min_days": run["length"] < min_days,
            "below_min_loss": abs(run["loss"]) < min_loss_usd,
        }
    return graded


def _send_alert(message: str) -> None:
    """One Telegram + one typed WARNING push — the same shape (and the same
    channel) ``silent_refusal_alert`` and ``account_reachability_alert`` use,
    so this lands beside its siblings rather than inventing a second style.

    WARNING, not CRITICAL: see the module docstring.
    """
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("losing_streak_alert: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("losing_streak_alert: fcm WARNING publish failed: %s", exc)


def describe(account_id: str, a: Dict[str, Any]) -> str:
    """The operator-facing body. States the population, always."""
    cov = a.get("pnl_coverage")
    cov_text = ("coverage unknown (nothing gradeable)" if cov is None
                else f"{cov:.0%} of it broker-measured")
    prov = a.get("provenance") or {}
    fab = int(prov.get("fabricated") or 0)
    fab_text = (f" {fab} fabricated row(s) excluded from the total."
                if fab else "")
    return (
        f"\U0001F7E1 [WARN] {account_id} has lost money on "
        f"{a['streak_days']} consecutive trading days "
        f"({a['streak_start']} → {a['streak_end']}), totalling "
        f"${abs(a['streak_loss_usd']):,.2f} — {cov_text}."
        f"{fab_text} Population: {a['graded_days']} gradeable day(s) of "
        f"activity in the whole window, from {a['rows']} closed row(s). "
        f"This is a WARN, not a position-safety page: the book is trading and "
        f"losing, not unprotected."
    )


def _read_window(db_path: str, days: int) -> List[sqlite3.Row]:
    """Rows in the window. Read-only, SELECT only, no broker call."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT account_id, status, is_backtest, pnl, closed_at,
                   created_at, timestamp, notes
              FROM trades
             WHERE is_backtest = 0
               AND status = 'closed'
               AND COALESCE(closed_at, timestamp, created_at)
                   >= datetime('now', ?)
            """,
            (f"-{int(days)} days",),
        ).fetchall()
    finally:
        conn.close()


def status() -> Dict[str, Dict[str, Any]]:
    """What was last OBSERVED per account — the read surface.

    Returned to the review skills and the ``/api/bot/notifications`` banner.
    Mirrors ``silent_refusal_alert.silent_accounts()`` /
    ``account_reachability_alert.down_accounts()``. Best-effort.
    """
    state = _load_state()
    return {k: v for k, v in state.items()
            if k != _LAST_CHECK_KEY and isinstance(v, dict)}


def streaking_accounts() -> Dict[str, Dict[str, Any]]:
    """Only the accounts currently graded :data:`STREAK_ACTIVE`."""
    return {k: v for k, v in status().items()
            if v.get("state") == STREAK_ACTIVE}


def run_losing_streak_check(
    *,
    now: Optional[datetime] = None,
    rows: Optional[Sequence[Any]] = None,
    force: bool = False,
) -> dict:
    """One tick. Cadence-gated internally; call once per trader tick.

    ``rows`` is a test seam — passing it skips the DB read. Best-effort
    throughout: every path swallows its own exceptions, so the worst case is a
    missed or duplicated alert, never a blocked tick.
    """
    now = now or datetime.now(timezone.utc)
    interval = _int_knob("LOSING_STREAK_CHECK_SECONDS", 3600)
    lookback = _int_knob("LOSING_STREAK_LOOKBACK_DAYS", 30, minimum=1)
    min_days = _int_knob("LOSING_STREAK_MIN_DAYS", 4, minimum=2)
    min_loss = _float_knob("LOSING_STREAK_MIN_LOSS_USD", 500.0)
    max_gap = _int_knob("LOSING_STREAK_MAX_GAP_DAYS", 3, minimum=0)

    state = _load_state()
    if interval <= 0:
        return {"checked": False, "reason": "paused"}
    if not force and rows is None:
        last = state.get(_LAST_CHECK_KEY)
        if last:
            try:
                prev = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if prev.tzinfo is None:
                    prev = prev.replace(tzinfo=timezone.utc)
                if (now - prev).total_seconds() < interval:
                    return {"checked": False, "reason": "cadence"}
            except (TypeError, ValueError):
                pass  # unparseable stamp => check now rather than never

    if rows is None:
        try:
            from src.utils.paths import trade_journal_db_path
            rows = _read_window(str(trade_journal_db_path()), lookback)
        except Exception as exc:  # noqa: BLE001
            # Deliberately NOT treated as "no account is bleeding". We could
            # not look, so nothing latches, nothing clears, next tick retries.
            logger.warning("losing_streak_alert: journal read failed: %s", exc)
            return {"checked": False, "reason": "read_failed"}

    graded = assess(rows, min_days=min_days, min_loss_usd=min_loss,
                    max_gap_days=max_gap)
    state[_LAST_CHECK_KEY] = now.isoformat()
    alerted: List[str] = []
    recovered: List[str] = []
    skip = _skip_set()

    for acct, a in graded.items():
        if acct in skip:
            continue
        prev = state.get(acct) if isinstance(state.get(acct), dict) else {}
        was = str(prev.get("state") or "")
        if a["state"] == STREAK_ACTIVE:
            # SEVERITY IS THE STREAK LENGTH. The shared latch pages when it
            # LENGTHENS and stays silent while it merely persists or shortens.
            # An account digging deeper is a new fact; an account that is still
            # where it was is not, and paging on it is the desensitized-alarm
            # P1 this latch exists to prevent.
            if _alert_cooldown.cooldown_admits(
                ALERT_KIND, acct, COOLDOWN_S,
                severity=int(a["streak_days"]),
            ):
                _send_alert(describe(acct, a))
                alerted.append(acct)
        elif was == STREAK_ACTIVE:
            # ⚠️ THE RECOVERY MESSAGE MUST NAME WHY IT RECOVERED. An account
            # can leave STREAK_ACTIVE by having a winning day (a real
            # recovery), or by going UNREADABLE / INSUFFICIENT (we stopped
            # being able to see it). Reporting the second as "no longer
            # losing" would assert something nobody measured — the exact
            # correction `silent_refusal_alert` needed on 2026-08-24.
            if a["state"] == STREAK_NONE:
                body = (f"its most recent graded day was not a loss "
                        f"({a['graded_days']} day(s) gradeable).")
            elif a["state"] == STREAK_INSUFFICIENT:
                body = (f"it no longer has {min_days} gradeable day(s) in the "
                        f"window — this is NOT a report that it stopped "
                        f"losing, the book has gone quiet.")
            else:
                body = ("its closed rows no longer carry a readable PnL "
                        "— this is NOT a recovery, we cannot measure it.")
            _send_alert(f"\U0001F7E2 [OK] {acct} is no longer on a losing "
                        f"streak: {body}")
            recovered.append(acct)
        state[acct] = dict(a, updated_at=now.isoformat())

    # An account that latched and then stopped producing rows entirely never
    # re-enters the loop above, so its latch would stand for ever — measured on
    # `silent_refusal_alert` at 3.85 days. Release it with its own explicit
    # message: going quiet is NOT a recovery and must not be reported as one.
    for acct, prev in list(state.items()):
        if acct == _LAST_CHECK_KEY or not isinstance(prev, dict):
            continue
        if acct in graded or acct in skip:
            continue
        if str(prev.get("state") or "") != STREAK_ACTIVE:
            state.pop(acct, None)
            continue
        _send_alert(
            f"\U0001F7E2 [OK] {acct} is no longer flagged: it closed NO trades "
            f"at all in the last {lookback} day(s), so there is nothing to "
            f"grade. This is NOT a report that it stopped losing — the "
            f"account has gone quiet, which is its own condition. Last graded "
            f"streak was {prev.get('streak_days')} day(s) / "
            f"${abs(float(prev.get('streak_loss_usd') or 0.0)):,.2f} at "
            f"{prev.get('updated_at')}."
        )
        recovered.append(acct)
        state.pop(acct, None)

    _save_state(state)
    return {"checked": True, "alerted": alerted, "recovered": recovered,
            "assessed": len(graded)}


__all__ = [
    "ALERT_KIND", "STREAK_STATES", "STREAK_ACTIVE", "STREAK_NONE",
    "STREAK_INSUFFICIENT", "STREAK_UNREADABLE",
    "assess", "current_run", "daily_pnl", "describe",
    "run_losing_streak_check", "status", "streaking_accounts",
]
