"""The standing ledger of close failures that NO bot-side lever can clear.

WHY THIS EXISTS
---------------
``order_monitor``'s close-failure pager was built for one failure mode and does
it well: a position that will not flatten must never be retried *silently*
forever. Its comment says so outright — *"It deliberately NEVER goes silent."*

That is right for a failure whose cause is unknown, and wrong for exactly one
narrow class: a failure whose cause has been **established, from broker
evidence, as unclearable by any lever this bot has**. Paging hourly about a
condition the bot provably cannot act on does not inform the operator; it
competes with the pages that can still be acted on. Measured instance, and the
whole reason this module exists: ``alpaca_paper`` GLD (39 shares,
pkg-6a8e3fb325464be3) has failed to close **since 2026-08-27** because OCO
parent ``2e843e04-5487-470c-a702-70e796fbd05e`` sits at ``pending_cancel`` with
``canceled_at`` null. Our DELETE was ACCEPTED — that is what moved it there —
and Alpaca never completed its own cancel, so a re-issued app-level cancel is a
no-op and ``DELETE /v2/positions/GLD?cancel_orders=true`` returns the same
``insufficient qty available`` error. Operator decision 2026-09-02: **downgrade
that one class to the daily digest.**

THE CLASS IS NARROW, AND THAT IS THE POINT
------------------------------------------
⚠️ **THE DOWNGRADE KEYS ON EVIDENCE, NEVER ON REPETITION.** The trigger is
``share_hold == "broker_cancel_wedged"`` — a determination
:func:`~src.units.accounts.alpaca_client.classify_share_hold` makes by reading
the residual order's own ``status`` field from the broker. A close that keeps
failing for an **unknown** reason is precisely what must still page, and it
does: every other reading — including ``not_classified`` (*we did not look*)
and ``residual_unreadable`` (*we looked and could not see*) — routes to the
pager unchanged. There is no retry-count anywhere in this module, deliberately:
"it failed N times" and "we established it cannot succeed" are different claims
and only the second may buy silence.

FIVE TRANSITIONS, NEVER COLLAPSED — AND ONLY ONE IS QUIET
---------------------------------------------------------
``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states". Registered with
``collapsed-state-guard`` as ``close_wedge.transition``.

* ``newly_wedged``          — first observation of this wedge. **PAGES.** The
  operator learns about it once, loudly. Downgrading the *arrival* of a wedge
  would hide the condition, not de-noise it.
* ``still_standing``        — same key, same evidence, still wedged. **THE ONE
  DOWNGRADED STATE**: carried in the digest, floored by a re-page interval (see
  below) so silence is never reachable.
* ``evidence_changed``      — same (account, symbol, side), but wedged on
  DIFFERENT evidence: a different set of wedged order ids/statuses, or a
  different ``share_hold`` state entirely. **PAGES.** This is "newly unclearable
  for a different reason", and reading it as a continuation of the old wedge
  would let a second, unexamined fault inherit the first one's silence.
* ``cleared_confirmed``     — a confirmed close was observed for this key.
  **PAGES**, because a state change the operator was told to expect must be
  reported when it happens, and because the resolution is ATTRIBUTED.
* ``vanished_unattributed`` — the wedge stopped being observed and **no
  confirmed close was ever recorded**. **PAGES**, and says outright that nothing
  explains it.

⚠️ ``vanished_unattributed`` IS NOT ``cleared_confirmed`` AND MUST NEVER BE
FOLDED INTO IT. A position that simply disappears is not evidence that anything
worked. This repo has already been bitten by exactly that inference: the
``PROTECTION_REASSERT_MODE`` row in ``CLAUDE.md`` records a diverged stop that
resolved itself while the gate was at ``annotate`` with an empty allowlist — the
gate could not have done it, and crediting it would have banked a capability
nobody had. ``OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED``
names the same trap for this wedge in its own ``Clears when`` clause. So an
unattributed disappearance is recorded AS an unattributed disappearance.

SILENCE IS STRUCTURALLY UNREACHABLE
-----------------------------------
⚠️ **``still_standing`` IS FLOORED, NOT SILENCED.** It still pages once per
``CLOSE_WEDGE_REPAGE_HOURS`` (default 24h) — down from the close-failure
pager's exponential backoff, which is capped at hourly and so fires ~24x more
often on a wedge that has stood for days.

That floor is deliberate and it is NOT redundancy for its own sake. The digest
(``scripts/ops/work_digest.py``, ``.github/workflows/work-digest.yml``) runs on
a GitHub runner from a fresh checkout, and ``runtime_logs/`` is ``.gitignore``d
and lives on the trader VM — so the digest can only see this ledger by fetching
it from the live diag surface, over a scheduled cadence this repo has measured
as **late and erratic**. ``probes.yml``'s FIRST EVER scheduled run (#34,
conclusion success, 2026-09-01T10:12:17Z) came **~4h50m late** against its
``20 5 * * *`` cron, and fired **once rather than daily**.
(``OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON`` was
CLEARED on that observation 2026-09-02. This paragraph read *"have never once
fired on cron"* until then and **must not be re-quoted** — but the caution
SURVIVES the correction and is better evidenced by it, because a cadence that
slips five hours is a worse carrier for a standing alarm than one that has
simply never been tried.) The digest's own renderer was also dropping queued
bodies until #10747, which landed this morning. Suppressing
the pager *entirely* in favour of that channel would make the operator's stated
risk — the item falling quietly out of BOTH channels — reachable in one step.
The floor is what makes it unreachable regardless of whether the digest works.

**When the floor may be removed:** when a digest run is OBSERVED carrying a
standing wedge through to the operator. Not when the workflow is merged, not
when it is green — CLAUDE.md is emphatic that neither is an observation.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not clear a wedge, cancel anything, or touch an order. It has no
network calls and no DB access: its whole world is one small JSON file and
the classified observation it is handed. It decides how
loudly a condition is reported and keeps a durable record of it. The decision is
a **pure function** (:func:`classify_transition`) so the policy is arguable in
tests rather than against a live wedged position — the lesson of
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from src.units.accounts.alpaca_client import (
    SHARE_HOLD_NOT_CLASSIFIED,
    SHARE_HOLD_STATES,
)
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

#: The durable store. A single small JSON OBJECT (not JSONL) — the
#: ``exit_loop_health`` shape — because this is a set of CURRENT conditions, not
#: an event stream: a reader wants "what is standing right now", and a tail of
#: appends cannot answer that without replaying it.
STANDING_LOG = runtime_logs_dir() / "close_wedge_standing.json"

#: Bumped when the entry shape changes. A reader that finds a schema it does not
#: know must say so rather than parse optimistically — the
#: ``fanout_schema`` lesson, where rows from two shapes were pooled and
#: re-created the defect in the analysis instead of the code.
SCHEMA = 1

#: The ONE hold state that buys the downgrade. The vocabulary is IMPORTED from
#: its owner, never restated here — two copies of a state vocabulary in modules
#: that talk to each other is how they come to disagree.
# collapsed-state: broker_cancel_wedged — this module is the DOWNGRADE, and the
# downgrade is defined by exactly one state. The other three
# (residual_unreadable / no_residual_orders / orders_still_resting) never reach
# here at all: `execution_diagnostics.route_close_failure` returns "page" for
# them BEFORE calling `observe`, and `observe` itself re-checks and refuses. So
# this file legitimately sees one state — and it must, because the whole
# contract is that ONLY a positive, evidenced determination buys quiet. A site
# here that branched on the other three would mean one of them had found a way
# into the quiet path. They are branched on in
# `execution_diagnostics._share_hold_guidance`, which turns each into its own
# operator action.
UNCLEARABLE_HOLD_STATE = "broker_cancel_wedged"
assert UNCLEARABLE_HOLD_STATE in SHARE_HOLD_STATES

#: The five transitions. See the module docstring; only ``still_standing`` is
#: quiet, and even it is floored.
TRANSITIONS: Tuple[str, ...] = (
    "newly_wedged",
    "still_standing",
    "evidence_changed",
    "cleared_confirmed",
    "vanished_unattributed",
)

#: The four that reach the pager. Written as the complement of the ONE quiet
#: state rather than as a hand-listed set, so adding a transition defaults it to
#: LOUD. A new state that silently inherited quietness is the failure direction.
LOUD_TRANSITIONS: frozenset = frozenset(TRANSITIONS) - {"still_standing"}

#: The verdict when the failure is not a confirmed-unclearable wedge at all.
#: ⚠️ **Deliberately NOT a member of :data:`TRANSITIONS`.** No wedge state
#: machine is entered, nothing is stored, and nothing is suppressed — giving
#: this case one of the five real transition names would put ordinary close
#: failures into a vocabulary that exists to describe wedges, and a later reader
#: counting ``newly_wedged`` would be counting every close failure on the fleet.
NOT_A_WEDGE = "not_a_wedge"
assert NOT_A_WEDGE not in TRANSITIONS

_DEFAULT_REPAGE_HOURS = 24.0

#: How long a wedge may go un-observed before it is swept as
#: ``vanished_unattributed``.
#:
#: ⚠️ **72h IS NOT A ROUND NUMBER; IT CLEARS A WEEKEND.** Observations are
#: written by :func:`observe`, which the monitor calls on the close-failure
#: alerting gate — at least hourly while a streak stands, because that gate is
#: capped at ``MONITOR_CLOSE_FAIL_ALERT_MAX_BACKOFF_S`` (3600s) and "never goes
#: silent". But a market-session DEFER **clears the streak**
#: (``order_monitor._clear_close_fail_alert_state``), so a US-equity wedge stops
#: being observed from Friday's close to Monday's open — ~65h during which the
#: wedge is still there and nothing is looking at it. A window shorter than that
#: would page "the position vanished" every Sunday about a position that had not
#: moved. GLD, the instrument this was built for, is a US equity ETF.
_DEFAULT_VANISH_AFTER_HOURS = 72.0

#: How often the sweep re-stamps an EMPTY ledger to prove it is still looking.
#:
#: ⚠️ **THIS IS WHAT MAKES ``wedges: {}`` MEAN ANYTHING.** Before it, the store
#: was written only when a wedge was actually recorded, so a fleet that had
#: never wedged left NO FILE AT ALL — and a missing file cannot distinguish
#: *"the trader looked and nothing is wedged"* from *"the trader is not running,
#: or crashed, or was never deployed"*. Those are opposite findings and the
#: second is the dangerous one, because this ledger is the ONLY channel a
#: downgraded wedge appears in (MI-34). Measured 2026-09-03: the file had never
#: existed on the live VM, `/api/diag/log_file?name=close_wedge_standing`
#: answered `present: false`, and the digest rendered that as a clean fleet.
#:
#: 15 minutes is chosen against the READER's cadence, not the monitor's: the
#: work digest runs hourly, so a floor well inside that window means every
#: digest reads a ledger stamped since the previous one. It is a floor, not a
#: timer — the sweep still only writes when it runs.
_DEFAULT_HEARTBEAT_MINUTES = 15.0

#: How many heartbeat intervals may pass before a reader must call the ledger
#: STALE. Three, so a single missed sweep (a restart, one slow pass) is not
#: reported as a dead writer, while a genuinely stopped writer surfaces inside
#: an hour. Published INTO the artifact by :func:`_save` so the reader grades
#: against the writer's own declared cadence instead of a second copy of this
#: number that can drift away from it.
STALE_AFTER_INTERVALS = 3.0


def _env_float(name: str, default: float, floor: float = 0.0) -> float:
    """Read *name* as a float at call time; fall back to *default*, clamp >= *floor*.

    Falls back to the DEFAULT on an unparseable value, never to zero — a
    fat-fingered knob must not silently become "page every time" or "sweep
    immediately". The ``PROTECTION_REASSERT_*`` discipline.
    """
    raw = os.environ.get(name)  # allow-silent: alarm-cadence tuning knob, not a capability gate
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(floor, float(raw))
    except (TypeError, ValueError):
        return default


def repage_hours() -> float:
    return _env_float("CLOSE_WEDGE_REPAGE_HOURS", _DEFAULT_REPAGE_HOURS)


def vanish_after_hours() -> float:
    return _env_float("CLOSE_WEDGE_VANISH_AFTER_HOURS", _DEFAULT_VANISH_AFTER_HOURS)


def heartbeat_minutes() -> float:
    """Minutes between empty-ledger re-stamps. Floored at 1 to bound disk churn."""
    return _env_float(
        "CLOSE_WEDGE_HEARTBEAT_MINUTES", _DEFAULT_HEARTBEAT_MINUTES, floor=1.0,
    )


def wedge_key(account: object, symbol: object, side: object) -> str:
    """The identity of a wedge: (account, symbol, direction), lowercased side.

    The SAME triple ``order_monitor`` keys its close-failure streak and cooldown
    on, deliberately — a wedge and the streak it suppresses must be the same
    thing or the suppression would not land on the failure that earned it.
    """
    return "|".join((
        str(account or ""), str(symbol or ""), str(side or "").lower(),
    ))


#: Order ids + statuses as ``classify_share_hold`` renders them into its detail
#: string: ``"<id> is <status>"``. Parsed back out so "wedged on the SAME order"
#: and "wedged on a DIFFERENT one" are distinguishable — which is what makes
#: ``evidence_changed`` a real state rather than a hopeful one.
_EVIDENCE_RE = re.compile(r"([0-9a-fA-F][0-9a-fA-F-]{7,})\s+is\s+([a-z_]+)")


def evidence_fingerprint(detail: object) -> str:
    """A stable fingerprint of WHICH orders are wedged and in what status.

    ⚠️ **An empty fingerprint means "no order ids were recoverable from the
    detail", NOT "no orders are wedged".** It is returned as the literal
    ``"unfingerprinted"`` rather than ``""`` so a consumer cannot read absence
    as emptiness; two ``unfingerprinted`` observations compare EQUAL, which is
    the conservative direction here (it does not manufacture an
    ``evidence_changed`` page out of a detail string we could not parse).
    """
    pairs = sorted({f"{oid}:{st.lower()}" for oid, st in _EVIDENCE_RE.findall(str(detail or ""))})
    return ",".join(pairs) if pairs else "unfingerprinted"


class Observation(NamedTuple):
    """One close-failure observation, already classified. The input to the policy."""

    account: str
    symbol: str
    side: str
    share_hold: str
    detail: str


class Decision(NamedTuple):
    """What the policy concluded. ``transition`` is one of :data:`TRANSITIONS`."""

    transition: str
    should_page: bool
    #: Why paging was or was not suppressed, in operator-readable words. Always
    #: populated — a suppression with no stated reason is unauditable.
    reason: str
    entry: Optional[Dict[str, Any]]


def classify_transition(
    prior: Optional[Dict[str, Any]],
    obs: Observation,
    now: datetime,
) -> Tuple[str, str]:
    """Pure: given the stored entry (or None) and a fresh observation, name the
    transition and say why. Never raises, never touches disk.

    ⚠️ Callers must only reach here with ``obs.share_hold ==
    UNCLEARABLE_HOLD_STATE``; :func:`observe` enforces that. Every other reading
    — ``not_classified`` most of all — is a page, and is not this function's
    business to weaken.
    """
    fp = evidence_fingerprint(obs.detail)
    if prior is None:
        return ("newly_wedged", "first observation of this wedge")
    if prior.get("share_hold") != obs.share_hold:
        return (
            "evidence_changed",
            f"hold state changed {prior.get('share_hold')!r} -> {obs.share_hold!r}",
        )
    prior_fp = str(prior.get("evidence") or "unfingerprinted")
    if prior_fp != fp:
        return (
            "evidence_changed",
            f"wedged on different orders now ({prior_fp} -> {fp})",
        )
    return ("still_standing", "same wedge, same evidence")


def _should_page_standing(entry: Dict[str, Any], now: datetime) -> Tuple[bool, str]:
    """The FLOOR under ``still_standing``: page once per :func:`repage_hours`.

    Returns ``(page?, reason)``. An unreadable or absent ``last_paged_at``
    **PAGES** — the same direction ``work_digest._already_sent_today`` chose for
    its own latch: on a notification path a broken suppressor must announce
    itself as a duplicate, never as silence.
    """
    last = _parse_ts(entry.get("last_paged_at"))
    if last is None:
        return (True, "no readable last-paged time — failing loud")
    hours = repage_hours()
    due = last + timedelta(hours=hours)
    if now >= due:
        return (True, f"standing wedge re-page ({hours:.0f}h floor reached)")
    remaining = (due - now).total_seconds() / 3600.0
    return (
        False,
        f"downgraded to the digest — confirmed unclearable; next floor page in "
        f"{remaining:.1f}h ({hours:.0f}h floor)",
    )


def _parse_ts(value: object) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the store. A missing or unreadable file yields an EMPTY store marked
    with its read state — never a silently-empty one."""
    p = path or STANDING_LOG
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("store is not an object")
        wedges = raw.get("wedges")
        return {
            "schema": raw.get("schema"),
            "wedges": wedges if isinstance(wedges, dict) else {},
            "last_sweep_at": raw.get("last_sweep_at"),
            "updated_at": raw.get("updated_at"),
            "read_state": "read",
        }
    except FileNotFoundError:
        # ⚠️ NO FILE IS *NOT* "THE TRADER HAS NEVER RECORDED A WEDGE" — that is
        # what this comment used to claim, and the claim was wrong in the
        # direction that loses signal. Since the heartbeat below, a running
        # trader ALWAYS leaves a file; so an absent one now means the writer is
        # not running, has never deployed, or cannot write its data dir. The
        # `read_state` is kept as `absent` (distinct from `read` with no wedges,
        # and from `unreadable`) precisely so no caller can collapse them.
        return {"schema": SCHEMA, "wedges": {}, "read_state": "absent"}
    except (OSError, ValueError) as exc:
        logger.warning("close_wedge_standing: store unreadable (%s)", exc)
        return {"schema": None, "wedges": {}, "read_state": "unreadable"}


def _save(
    store: Dict[str, Any],
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Atomically persist the store. Returns False on any failure.

    ⚠️ **The return value is load-bearing and must not be discarded.** The page
    is suppressed only because the condition is being CARRIED here; a failed
    write means it is not carried, and :func:`observe` pages instead. A
    best-effort write whose failure is swallowed would convert the downgrade
    into exactly the silence this whole module exists to prevent.

    ``now`` stamps ``updated_at``. It is a parameter rather than a bare
    ``datetime.now()`` because that stamp is now GRADED downstream — the digest
    calls a ledger stale from it — and a clock a test cannot set is a freshness
    rule nobody can write a failing test for. Callers pass the same ``now`` they
    reason with, so the file's own timestamp cannot disagree with the decision
    that produced it.
    """
    p = path or STANDING_LOG
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        payload = {
            "schema": SCHEMA,
            "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
            # THE ARTIFACT DECLARES ITS OWN FRESHNESS CONTRACT. A reader grading
            # `updated_at` needs to know how often this file is SUPPOSED to move,
            # and the only way for reader and writer to be unable to disagree
            # about that is for the writer to state it here. A second copy of the
            # cadence living in the reader is the drift this repo keeps paying
            # for; a reader that finds these absent must fall back and SAY it is
            # falling back, never assume fresh.
            "heartbeat_interval_s": int(heartbeat_minutes() * 60.0),
            "stale_after_intervals": STALE_AFTER_INTERVALS,
            # Carried, not recomputed: the sweep's own liveness is what lets it
            # tell "nothing vanished" from "nobody was watching".
            "last_sweep_at": store.get("last_sweep_at"),
            "wedges": store.get("wedges") or {},
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError as exc:
        logger.warning("close_wedge_standing: store write FAILED (%s) — will page", exc)
        return False


def _heartbeat(
    store: Dict[str, Any],
    now: datetime,
    path: Optional[Path] = None,
) -> bool:
    """Re-stamp an EMPTY ledger so ``wedges: {}`` means WE LOOKED. True if written.

    ⚠️ **THIS IS THE HALF THAT MAKES THE OTHER HALF READABLE.** The digest can
    only report "nothing is wedged" honestly if a *present* ledger is evidence
    that something looked. Without this the only evidence available was the
    file's ABSENCE, which is equally consistent with the trader being dead.

    ⚠️ **``last_sweep_at`` IS DELIBERATELY NOT ADVANCED HERE.** That field means
    *"a sweep ran while wedges were being carried"* and it gates the re-arm in
    :func:`sweep_vanished` that refuses to retire a wedge across a period nobody
    watched. A heartbeat proves the trader is alive; it does NOT prove any wedge
    was under observation, because there were none. Advancing it would let the
    first sweep after a wedge appears believe it had continuous observation it
    never had, and retire that wedge as ``vanished_unattributed`` — inventing a
    disappearance, which is precisely the failure the re-arm exists to prevent.
    Liveness and observation-continuity are different facts and this function
    only ever asserts the first.

    Never raises: a heartbeat failure must not break the sweep. It is reported
    by the file simply not moving, which is exactly what a reader grades.
    """
    try:
        last = _parse_ts(store.get("updated_at"))
        if last is not None:
            due_after = timedelta(minutes=heartbeat_minutes())
            if now - last < due_after:
                return False
        # A store that was `absent` has no `updated_at`, so it falls through and
        # writes immediately — the first heartbeat creates the file.
        return _save(store, path, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_wedge_standing: heartbeat failed (%s)", exc)
        return False


def observe(
    obs: Observation,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Decision:
    """Record a classified close failure and decide whether it pages.

    Returns a :class:`Decision` whose ``should_page`` the caller MUST honour.

    ⚠️ **Any reading other than ``broker_cancel_wedged`` pages, immediately and
    without touching the ledger.** That includes ``not_classified`` — a failure
    nobody classified is *"we did not look"*, and this module's whole contract is
    that only a positive, evidenced determination buys quiet.
    """
    now = now or datetime.now(timezone.utc)
    if obs.share_hold != UNCLEARABLE_HOLD_STATE:
        return Decision(
            transition=NOT_A_WEDGE,
            should_page=True,
            reason=(
                f"share_hold={obs.share_hold or SHARE_HOLD_NOT_CLASSIFIED!r} is not a "
                f"confirmed-unclearable determination — pages unchanged"
            ),
            entry=None,
        )

    store = _load(path)
    if store["read_state"] == "unreadable":
        # We cannot tell a new wedge from a standing one, so we cannot claim the
        # downgrade's precondition. Page.
        return Decision(
            "newly_wedged", True,
            "standing-wedge store is UNREADABLE — cannot establish this wedge is "
            "already carried, so it pages",
            None,
        )

    key = wedge_key(obs.account, obs.symbol, obs.side)
    wedges: Dict[str, Any] = store["wedges"]
    prior = wedges.get(key) if isinstance(wedges.get(key), dict) else None
    transition, why = classify_transition(prior, obs, now)

    entry: Dict[str, Any] = dict(prior or {})
    entry.update({
        "account": obs.account,
        "symbol": obs.symbol,
        "side": obs.side,
        "share_hold": obs.share_hold,
        "detail": str(obs.detail or "")[:512],
        "evidence": evidence_fingerprint(obs.detail),
        "last_seen": now.isoformat(),
    })
    entry.setdefault("first_seen", now.isoformat())
    entry.setdefault("pages_suppressed", 0)
    if transition == "evidence_changed":
        # A different fault. It does not inherit the old one's standing —
        # including its suppression budget.
        entry["first_seen"] = now.isoformat()
        entry["pages_suppressed"] = 0

    if transition == "still_standing":
        page, reason = _should_page_standing(entry, now)
    else:
        page, reason = True, why

    if page:
        entry["last_paged_at"] = now.isoformat()
    else:
        entry["pages_suppressed"] = int(entry.get("pages_suppressed") or 0) + 1
        reason = f"{reason}; {entry['pages_suppressed']} page(s) suppressed so far"

    wedges[key] = entry
    if not _save(store, path, now=now):
        # The carry failed. The condition is NOT being held anywhere, so the
        # only honest routing left is the pager.
        return Decision(
            transition, True,
            "standing-wedge store could not be written — the digest carry is NOT "
            "in place, so this pages rather than going quiet",
            entry,
        )
    return Decision(transition, page, reason, entry)


def resolve_confirmed(
    account: object, symbol: object, side: object,
    attribution: str = "monitor observed a confirmed close",
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Optional[Decision]:
    """A confirmed close landed for this key — the wedge is over, ATTRIBUTED.

    Returns the ``cleared_confirmed`` decision (which always pages: the operator
    was told this condition would be carried until it changed, and this is the
    change), or ``None`` when no wedge was standing for the key — the ordinary
    case on every healthy close, and deliberately NOT an event.
    """
    now = now or datetime.now(timezone.utc)
    store = _load(path)
    wedges: Dict[str, Any] = store["wedges"]
    key = wedge_key(account, symbol, side)
    entry = wedges.pop(key, None)
    if not isinstance(entry, dict):
        return None
    entry["resolved_at"] = now.isoformat()
    entry["resolution"] = "cleared_confirmed"
    entry["attribution"] = attribution
    _save(store, path, now=now)
    return Decision("cleared_confirmed", True, attribution, entry)


def sweep_vanished(
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> List[Decision]:
    """Retire wedges that stopped being observed WITHOUT a confirmed close.

    ⚠️ **This is the state the ``PROTECTION_REASSERT_MODE`` row exists to warn
    about, and it is recorded AS ITSELF.** The position is gone from the close
    path and nothing told us why: an operator console action, a venue-side
    completion of Alpaca's own cancel, a package cascade, or the trader simply
    not having run. Each is possible; none is established. Reporting this as a
    clean clear would bank a repair nobody can name — so it pages, and the body
    says the cause is unestablished.

    Returns one decision per swept wedge (usually none).
    """
    now = now or datetime.now(timezone.utc)
    store = _load(path)
    # ⚠️ `absent` REACHES THE HEARTBEAT, everything else still returns. A missing
    # file is the exact state the heartbeat exists to end, so it must not be
    # filtered out here — that ordering bug would leave the first write forever
    # waiting on a file that only the first write can create. `unreadable` still
    # returns untouched: we could not see the store, and overwriting a store we
    # failed to parse would destroy standing wedges to make a tidier file.
    if store["read_state"] == "unreadable":
        return []
    wedges: Dict[str, Any] = store["wedges"]
    window = vanish_after_hours()

    # Nothing carried → nothing to retire, and no clock worth advancing.
    #
    # ⚠️ BUT THE FILE STILL GETS STAMPED, and that reversal is the point of
    # MI-101. This branch used to `return []` before touching disk, so the
    # overwhelmingly common case — a fleet with no standing wedge — wrote
    # NOTHING, EVER. The file therefore did not exist on the live VM at all, and
    # `present: false` was read downstream as a clean fleet. Since a wedge here
    # has been DOWNGRADED OUT OF THE PAGER, "no file" and "no wedges" rendering
    # alike means a real wedge would appear in NEITHER channel.
    #
    # The original comment's concern is still honoured: the write is FLOORED at
    # `heartbeat_minutes()`, so this costs one small atomic write per interval,
    # not one per monitor pass.
    if not any(isinstance(v, dict) for v in wedges.values()):
        _heartbeat(store, now, path)
        return []

    # ⚠️ WERE *WE* LOOKING? A gap in OUR observation is not a disappearance.
    #
    # The sweep infers "this wedge stopped being reported" from the absence of
    # fresh observations. That inference is only available if observation was
    # actually running. If the trader was down (or this sweep simply was not
    # called) for longer than the window, EVERY standing wedge looks vanished at
    # once — and the first thing a restarted trader would do is page a flood of
    # "the position disappeared and nothing explains it" for positions that
    # never moved. That is the exact collapse this module is built against, one
    # level up: *we did not look* rendering as *we looked and it was gone*.
    #
    # So a sweep that finds its own last run older than the window RE-ARMS the
    # clock and retires nothing. The next pass, having genuinely watched for a
    # full window, can conclude something.
    last_sweep = _parse_ts(store.get("last_sweep_at"))
    store["last_sweep_at"] = now.isoformat()
    if last_sweep is None or (now - last_sweep) > timedelta(hours=window):
        for v in wedges.values():
            if isinstance(v, dict):
                v["observation_gap_at"] = now.isoformat()
        _save(store, path, now=now)
        logger.info(
            "close_wedge_standing: sweep re-armed (no continuous observation for "
            "the last %.0fh) — retiring nothing this pass", window,
        )
        return []

    cutoff = now - timedelta(hours=window)
    out: List[Decision] = []
    for key in [k for k in list(wedges) if isinstance(wedges.get(k), dict)]:
        entry = wedges[key]
        seen = _parse_ts(entry.get("last_seen"))
        if seen is None or seen >= cutoff:
            # An unparseable `last_seen` is NOT a stale one. We cannot date the
            # observation, so we cannot claim it stopped — leave it standing and
            # let the digest report it rather than inventing a disappearance.
            continue
        entry["resolved_at"] = now.isoformat()
        entry["resolution"] = "vanished_unattributed"
        entry["attribution"] = (
            "NOT ESTABLISHED — the wedge stopped being observed and no confirmed "
            "close was recorded. This is not evidence anything was repaired."
        )
        wedges.pop(key, None)
        out.append(Decision("vanished_unattributed", True, entry["attribution"], entry))
    # ⚠️ PERSIST THE CLOCK ON EVERY PASS, NOT ONLY WHEN SOMETHING WAS RETIRED.
    # Saving only on `if out:` was a real defect caught by
    # tests/test_close_wedge_downgrade.py: a quiet pass advanced `last_sweep_at`
    # in memory and threw it away, so the stored clock stayed pinned at the last
    # re-arm. Every later pass then measured its gap from that ancient value,
    # found it older than the window, and re-armed again — making
    # `vanished_unattributed` UNREACHABLE in production while every unit test of
    # the retirement logic still passed. The clock IS the evidence that we were
    # watching; discarding it on the passes where we saw nothing is discarding
    # exactly the observation it is supposed to record.
    _save(store, path, now=now)
    return out


def load_standing(path: Optional[Path] = None) -> Dict[str, Any]:
    """The digest's read surface. Carries its OWN read state, never a bare list.

    Three states, never collapsed — this is the same distinction
    ``work_digest.build_digest`` already makes for its window, applied to the
    ledger:

      * ``unreadable`` — the store exists and could not be parsed. **We looked
        and could not see.**
      * ``absent``     — no store file. On the trader this means "no wedge has
        ever been recorded"; **on a GitHub runner it means the file was never
        fetched**, which is *we did not look*. The two are told apart by
        ``fetched_from``, which only the workflow sets — a reader must not
        conclude "no wedges" from an ``absent`` store it never fetched.
      * ``read``       — the store was parsed; ``wedges`` is the real set, and
        an EMPTY set is a real observation.
    """
    store = _load(path)
    wedges = store["wedges"]
    return {
        "readState": store["read_state"],
        "schema": store.get("schema"),
        "schemaKnown": store.get("schema") == SCHEMA,
        "wedges": [
            dict(v, key=k) for k, v in sorted(wedges.items())
            if isinstance(v, dict)
        ],
        "count": sum(1 for v in wedges.values() if isinstance(v, dict)),
    }
