"""Latched alert for an account the arbitration fan-out keeps STARVING.

WHY THIS EXISTS (Tier-2, 2026-09-11). ``breakout_1`` — a ``mode: live`` prop
account — took no trade for **twelve days** while its legs evaluated ~1000
times each and signalled at a rate **identical to their ``bybit_1`` siblings,
to the second**. The cause was upstream of execution: ``aggregate_intents``
elects ONE winner per SYMBOL globally, the per-account fan-out correctly
re-elected ``trend_donchian_sol_prop`` for ``breakout_1``, and the election was
then discarded because the account is not in ``ARBITRATION_FANOUT_ACCOUNTS``.
Full chain: ``docs/research/prop-account-silence-2026-09-11.md`` (MI-274).

**THE INSTRUMENT WAS ALREADY RIGHT. NOTHING READ IT.**
``arbitration_fanout_soak`` recorded ``starved_accounts: ["breakout_1"]``
correctly on every single occurrence, for days, and it is consumed by no
reader at all. That is this repo's signature failure and it is worse than a
missing signal: a reviewer sees the field and assumes something acts on it —
the precise case ``provenance-consumer-guard`` exists to catch in the sibling
domain. **This module is that reader.**

Three per-account detectors were measured blind to this, each for its own
structural reason (MI-274's table, each with a positive control):

  * ``silent_refusal_alert`` — ``breakout_1`` has **no key at all** in its
    latch. It grades journal rows; a starved account produces none, and zero
    rows is its *"we observed nothing"* bucket, never a finding.
  * ``prop_fills_staleness`` — ``{"findings": {}}``. Detector A needs an open
    position and detector B needs two balance snapshots; the account is flat
    and no new snapshot exists, so **both** are structurally unarmed.
  * ``/health-review``'s strategy-silence check — healthy, correctly: the legs
    ARE evaluating, 997/995/1000 times.

The starvation produces no journal row, no ticket, no refusal and no alarm,
so it is invisible to every detector keyed on the journal. It is visible in
exactly one place, and this reads that place.

THIS DOES NOT PROBE THE BROKER AND DOES NOT READ THE JOURNAL. It tails one
local JSONL file the trader has already written — no socket, no SQLite, no
order path.

THE FALSE-POSITIVE COUNT IS THE DESIGN EVIDENCE. MEASURED 2026-09-11 over the
**COMPLETE** ``arbitration_fanout_soak.jsonl`` — 510 rows, 543,967 bytes
accounted exactly against the file's own reported ``size_bytes``, so this is
the LIFETIME and not a tail (MI-274 had only the newest 100 rows) — spanning
2026-08-30T14:25Z → 2026-09-11T17:10Z. Per-account graded state, restricted to
the gradeable rows (see the schema warning below):

    account            routed   starved   no_winner
    bybit_1               391         1          94
    bybit_2                66         0          11
    bybit_portfolio        66         0          11
    breakout_1              1        42           0
    alpaca_paper            0         0          11
    alpaca_portfolio        0         0          11

Sweeping the shipped rule (``starved >= N`` AND ``routed == 0``) hourly across
the whole file, counting distinct alert episodes:

    window   N>=2   N>=3   N>=4   N>=6   N>=8
      24h       2      2      5      5       1     <- breakout_1 ONLY, at every N
      48h       2      3      2      2       3     <- breakout_1 ONLY, at every N

**At every threshold tested and both window lengths, the only account that
ever fires is ``breakout_1``. Zero false positives.** The separation is not
marginal — 42 starvations against 1 for the next-highest account — so the
choice of ``N`` is not load-bearing here and 4 is taken to match the sibling
``losing_streak_alert`` rather than because the data forces it.

⚠️ **``routed == 0`` IS THE DISCRIMINATOR, AND ``elected`` IS A TRAP.** The
obvious rule — "elected by the planner but absent from ``rounds_applied``" —
fires on ``bybit_2`` and ``bybit_portfolio``, which were each elected **66
times** with ``rounds_applied`` empty. They are not starved: ``rounds_applied``
only ever contains ALLOWLISTED accounts, so a non-allowlisted account that
holds the global winner still gets its order through the ordinary global
dispatch and is graded ``routed``. Keying on ``rounds_applied`` would have
reported two healthy accounts as starved and buried the one real finding.

⚠️ **ONLY ``fanout_schema``-BEARING ROWS ARE GRADEABLE, AND THIS IS NOT
PEDANTRY.** ``arbitration_fanout`` states that a row without that key predates
2026-08-30 and its ``starved_accounts`` **conflates starvation with no-winner
ticks** — the overstatement it measured at 6.5x. Restricting to v2 rows
removes **13 of ``bybit_1``'s 14** starved gradings, i.e. almost the entire
apparent false-positive population is that conflation. Pre-v2 rows are counted
and reported as ``ungradeable_rows``, never as starvation and never silently
dropped.

⚠️ **``starved`` AND ``no_winner`` ARE NEVER POOLED.** A no-winner tick has no
other account to have lost to; its cause is upstream and a fan-out is not its
remedy. ``no_winner`` counts ride in the detail as context and can never
contribute to the threshold.

THE STATES ARE NOT COLLAPSED. Four conditions produce "no alert":

  * ``not_observed`` — the account appears in no gradeable row in the window.
    *We could not look.* Emphatically NOT "it is routing fine": an account
    whose legs stopped signalling entirely vanishes from this file, and that
    is a different problem, not a healthy one.
  * ``soak_unreadable`` — the soak file is absent, empty, or unparseable. We
    tried and failed. **An absent soak never means nothing is starved** — it
    most likely means ``ARBITRATION_FANOUT_MODE=off``, under which the
    measurement itself is switched off.
  * ``routing`` — observed, and it got orders out.
  * ``starved_persistent`` — the finding.

LEVEL IS ``WARNING``, NEVER ``CRITICAL``. CRITICAL is reserved for a position
UNPROTECTED or REVERSED. An account taking no trades is losing opportunity,
not control of a position.

⚠️ **NO SEVERITY IS PASSED TO THE LATCH, DELIBERATELY, AND THE ASYMMETRY WITH
``losing_streak_alert`` IS THE POINT.** There, severity is the streak length —
a genuinely monotone measure of a worsening fault. Here the only candidate is
the starvation COUNT in the window, which tracks how many SIGNALS the legs
happened to produce, not how much worse the fault is: a busy market day raises
it while the defect is unchanged. Passing it would page on market activity
and call it deterioration, which is the desensitized-alarm direction. The
condition is persistent rather than escalating, so a plain per-key latch —
one page per account per window — is the honest rate limit.

Knobs are cadence/threshold only — no default-off ``*_ENABLED`` gate in front
of a required observability capability (Prime Directive), and an unparseable
value falls back to its DEFAULT rather than to zero:

  ``STARVED_ACCOUNT_CHECK_SECONDS``  cadence between reads (default 3600)
  ``STARVED_ACCOUNT_WINDOW_HOURS``   lookback (default 24)
  ``STARVED_ACCOUNT_MIN_ROWS``       starvations before it is a pattern (default 4)
  ``STARVED_ACCOUNT_TAIL_BYTES``     bound on the tail read (default 2 MiB)
  ``STARVED_ACCOUNT_SKIP``           CSV escape hatch, mirrors ACCOUNT_DOWN_ALERT_SKIP

THE LATCH IS THE SHARED, DURABLE ONE (``src.runtime.alert_cooldown``), never a
copy and never ``time.monotonic()`` — the condition here ran for twelve days
across many trader restarts, so a per-process latch would have re-armed on
every merge to ``main``
(BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART, measured at 202 of
376 rows of the whole operator ERROR+ feed).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.runtime import alert_cooldown as _alert_cooldown
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

#: Resolves to ``runtime_logs/starved_account_alert_state.json`` through
#: :func:`src.runtime.alert_cooldown.state_path`. Registered on
#: ``/api/diag/log_file`` IN THE SAME COMMIT as this writer — a latch that
#: suppresses a page and cannot be inspected is strictly worse than no latch.
ALERT_KIND = "starved_account"

_SOAK_FILENAME = "arbitration_fanout_soak.jsonl"
#: ⚠️ DELIBERATELY NOT ``starved_account_alert_state.json`` — that name is
#: already owned by :data:`ALERT_KIND` through
#: ``alert_cooldown.state_path``. Two writers with incompatible shapes on one
#: path clobber silently and disable the detector while leaving it looking
#: installed; see the sibling note in ``losing_streak_alert``. Pinned by a test.
_STATE_FILENAME = "starved_account_observed_state.json"
_LAST_CHECK_KEY = "__last_check__"

#: One page per account per day at most. The condition persists for days, so a
#: tighter window would re-page about the same standing fault.
COOLDOWN_S: float = 24 * 3600.0

# ── the four states, never collapsed ──────────────────────────────────────
#: The finding: repeatedly elected, repeatedly lost the symbol, never routed.
STARVED_PERSISTENT = "starved_persistent"
#: Observed, and it got at least one order out. The only clean negative.
STARVED_ROUTING = "routing"
#: The account appears in no gradeable row. *We could not look* — NOT healthy.
STARVED_NOT_OBSERVED = "not_observed"
#: The soak is absent/empty/unparseable. We tried and failed.
STARVED_SOAK_UNREADABLE = "soak_unreadable"

STARVED_STATES: Tuple[str, ...] = (
    STARVED_PERSISTENT, STARVED_ROUTING, STARVED_NOT_OBSERVED,
    STARVED_SOAK_UNREADABLE,
)


def _int_knob(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an int knob, falling back to the DEFAULT on garbage — never to 0."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(minimum, val)


def _skip_set() -> frozenset:
    raw = os.environ.get("STARVED_ACCOUNT_SKIP", "") or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _state_path():
    return runtime_logs_dir() / _STATE_FILENAME


def _soak_path() -> pathlib.Path:
    return pathlib.Path(runtime_logs_dir()) / _SOAK_FILENAME


def _load_state() -> dict:
    try:
        p = _state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("starved_account_alert: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("starved_account_alert: state save failed: %s", exc)


def read_soak_tail(path: Any = None, *, max_bytes: Optional[int] = None
                   ) -> Optional[List[Dict[str, Any]]]:
    """The tail of the soak as parsed rows, or ``None`` if we could not look.

    ``None`` is the *we did not look* value and is never an empty list: the
    soak was measured at 543,967 bytes and growing, and an unreadable file must
    not render as a quiet fleet.

    The read is BOUNDED because this runs on the trader tick. The first
    (possibly partial) line after seeking is discarded rather than guessed at.
    """
    p = pathlib.Path(path) if path is not None else _soak_path()
    limit = max_bytes if max_bytes is not None else _int_knob(
        "STARVED_ACCOUNT_TAIL_BYTES", 2 * 1024 * 1024, minimum=4096)
    try:
        if not p.exists():
            return None
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()  # drop the partial line we landed inside
            blob = fh.read()
    except Exception as exc:  # noqa: BLE001
        logger.debug("starved_account_alert: soak read failed: %s", exc)
        return None
    rows: List[Dict[str, Any]] = []
    for line in blob.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue  # one bad line is not an unreadable file
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _row_time(row: Dict[str, Any]) -> Optional[datetime]:
    raw = row.get("logged_at_utc")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def assess(
    rows: Optional[Sequence[Dict[str, Any]]],
    *,
    min_rows: int,
    now: Optional[datetime] = None,
    window_hours: int = 24,
) -> Dict[str, Dict[str, Any]]:
    """Grade every account appearing in the soak window.

    PURE — no I/O, no env, no notification — so the policy is arguable in tests
    rather than against a live routing decision, which is the lesson of
    ``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.

    ``rows is None`` means the soak could not be read and yields the single
    ``soak_unreadable`` verdict rather than an empty (clean-looking) result.
    """
    if rows is None:
        return {"__soak__": {"state": STARVED_SOAK_UNREADABLE,
                             "starved": 0, "routed": 0, "no_winner": 0,
                             "gradeable_rows": 0, "ungradeable_rows": 0,
                             "window_rows": 0}}
    now = now or datetime.now(timezone.utc)
    lo = now - timedelta(hours=max(1, int(window_hours)))
    gradeable = 0
    ungradeable = 0
    per: Dict[str, Dict[str, int]] = {}
    for row in rows:
        ts = _row_time(row)
        if ts is None or not (lo < ts <= now):
            continue
        # ⚠️ A row with no `fanout_schema` predates 2026-08-30 and CONFLATES
        # starvation with no-winner ticks (`arbitration_fanout.FANOUT_STATES`,
        # measured at a 6.5x overstatement). It is counted so the denominator
        # is visible and is never graded.
        if row.get("fanout_schema") is None:
            ungradeable += 1
            continue
        gradeable += 1
        for acct, cell in (row.get("per_account") or {}).items():
            if not isinstance(cell, dict):
                continue
            bucket = per.setdefault(
                str(acct),
                {"starved": 0, "routed": 0, "no_winner": 0,
                 "winner_unattributed": 0, "no_candidates": 0, "unknown": 0},
            )
            state = str(cell.get("state") or "unknown")
            if state in bucket:
                bucket[state] += 1
            else:
                bucket["unknown"] += 1

    graded: Dict[str, Dict[str, Any]] = {}
    for acct, counts in per.items():
        observed = sum(counts.values())
        if observed == 0:
            state = STARVED_NOT_OBSERVED
        elif counts["starved"] >= min_rows and counts["routed"] == 0:
            state = STARVED_PERSISTENT
        elif counts["routed"] > 0:
            state = STARVED_ROUTING
        else:
            # Seen, never routed, but below the threshold. NOT `routing` — we
            # have no positive evidence it can route — and not the finding
            # either. `not_observed` is the honest reading: nothing here yet
            # supports a verdict about this account.
            state = STARVED_NOT_OBSERVED
        graded[acct] = {
            "state": state,
            "starved": counts["starved"],
            "routed": counts["routed"],
            # Context only. NEVER pooled into the threshold — a no-winner tick
            # has no other account to have lost to.
            "no_winner": counts["no_winner"],
            "no_candidates": counts["no_candidates"],
            "unknown": counts["unknown"],
            # STATE THE DENOMINATOR beside every count.
            "gradeable_rows": gradeable,
            "ungradeable_rows": ungradeable,
            "window_rows": gradeable + ungradeable,
            "below_min_rows": counts["starved"] < min_rows,
        }
    return graded


def describe(account_id: str, a: Dict[str, Any], *, window_hours: int) -> str:
    """The operator-facing body. States the population, always."""
    return (
        f"\U0001F7E1 [WARN] {account_id} is being STARVED by symbol "
        f"arbitration: it held a candidate and lost the symbol "
        f"{a['starved']} time(s) in the last {window_hours}h and routed "
        f"NOTHING. Population: {a['gradeable_rows']} gradeable soak row(s) "
        f"({a['ungradeable_rows']} pre-schema row(s) not graded). Its legs are "
        f"signalling and producing no orders, so no journal row, ticket or "
        f"refusal exists for this — it is invisible to every account detector "
        f"keyed on the journal. Check ARBITRATION_FANOUT_ACCOUNTS. "
        f"({a['no_winner']} separate no-winner tick(s) are NOT counted here.)"
    )


def _send_alert(message: str) -> None:
    """One Telegram + one typed WARNING push — the shape
    ``silent_refusal_alert`` and ``account_reachability_alert`` share."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("starved_account_alert: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("starved_account_alert: fcm WARNING publish failed: %s", exc)


def status() -> Dict[str, Dict[str, Any]]:
    """What was last OBSERVED per account — the read surface.

    Mirrors ``silent_refusal_alert.silent_accounts()`` and
    ``account_reachability_alert.down_accounts()``. Best-effort.
    """
    state = _load_state()
    return {k: v for k, v in state.items()
            if k != _LAST_CHECK_KEY and isinstance(v, dict)}


def starved_accounts() -> Dict[str, Dict[str, Any]]:
    """Only the accounts currently graded :data:`STARVED_PERSISTENT`."""
    return {k: v for k, v in status().items()
            if v.get("state") == STARVED_PERSISTENT}


def run_starved_account_check(
    *,
    now: Optional[datetime] = None,
    rows: Optional[Sequence[Dict[str, Any]]] = None,
    force: bool = False,
) -> dict:
    """One tick. Cadence-gated internally; call once per trader tick.

    ``rows`` is a test seam — passing it skips the file read. Best-effort
    throughout: the worst case is a missed or duplicated alert, never a
    blocked tick.
    """
    now = now or datetime.now(timezone.utc)
    interval = _int_knob("STARVED_ACCOUNT_CHECK_SECONDS", 3600)
    window_hours = _int_knob("STARVED_ACCOUNT_WINDOW_HOURS", 24, minimum=1)
    min_rows = _int_knob("STARVED_ACCOUNT_MIN_ROWS", 4, minimum=1)

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
        rows = read_soak_tail()

    graded = assess(rows, min_rows=min_rows, now=now,
                    window_hours=window_hours)
    state[_LAST_CHECK_KEY] = now.isoformat()

    # An unreadable soak is NOT a clean fleet. Nothing latches and nothing
    # clears, so a standing alert survives a blind window rather than being
    # silently retracted by our own inability to look.
    if list(graded) == ["__soak__"]:
        state["__soak__"] = dict(graded["__soak__"], updated_at=now.isoformat())
        _save_state(state)
        logger.warning(
            "starved_account_alert: %s unreadable — not grading. An absent "
            "soak most likely means ARBITRATION_FANOUT_MODE=off, in which "
            "case the measurement itself is switched off.", _SOAK_FILENAME)
        return {"checked": False, "reason": "soak_unreadable"}
    state.pop("__soak__", None)

    alerted: List[str] = []
    recovered: List[str] = []
    skip = _skip_set()

    for acct, a in graded.items():
        if acct in skip:
            continue
        prev = state.get(acct) if isinstance(state.get(acct), dict) else {}
        was = str(prev.get("state") or "")
        if a["state"] == STARVED_PERSISTENT:
            # NO `severity` — see the module docstring. The starvation count
            # tracks signal volume, not how much worse the fault is.
            if _alert_cooldown.cooldown_admits(ALERT_KIND, acct, COOLDOWN_S):
                _send_alert(describe(acct, a, window_hours=window_hours))
                alerted.append(acct)
        elif was == STARVED_PERSISTENT:
            # ⚠️ NAME WHY IT RECOVERED. Leaving STARVED_PERSISTENT by ROUTING
            # is a real recovery; leaving it by dropping out of the window is
            # the legs going quiet, which is a different and unmeasured fact.
            if a["state"] == STARVED_ROUTING:
                body = (f"{a['routed']} of its elections routed in the last "
                        f"{window_hours}h.")
            else:
                body = ("it no longer appears in the soak often enough to "
                        "grade — this is NOT a report that it started "
                        "routing; its legs may simply have stopped "
                        "signalling, which is its own condition.")
            _send_alert(f"\U0001F7E2 [OK] {acct} is no longer starved: {body}")
            recovered.append(acct)
        state[acct] = dict(a, updated_at=now.isoformat())

    # A latched account that drops out of the soak entirely would otherwise
    # hold its latch for ever — the shape measured on `silent_refusal_alert` at
    # 3.85 days. Release it with a message that does not claim a recovery.
    for acct, prev in list(state.items()):
        if acct == _LAST_CHECK_KEY or not isinstance(prev, dict):
            continue
        if acct in graded or acct in skip:
            continue
        if str(prev.get("state") or "") != STARVED_PERSISTENT:
            state.pop(acct, None)
            continue
        _send_alert(
            f"\U0001F7E2 [OK] {acct} is no longer flagged as starved: it "
            f"appears in NO gradeable soak row in the last {window_hours}h, so "
            f"there is nothing to grade. This is NOT a report that it started "
            f"routing — it has dropped out of arbitration entirely, which is "
            f"its own condition. It was last graded starved "
            f"{prev.get('starved')} time(s) at {prev.get('updated_at')}."
        )
        recovered.append(acct)
        state.pop(acct, None)

    _save_state(state)
    return {"checked": True, "alerted": alerted, "recovered": recovered,
            "assessed": len(graded)}


__all__ = [
    "ALERT_KIND", "STARVED_STATES", "STARVED_PERSISTENT", "STARVED_ROUTING",
    "STARVED_NOT_OBSERVED", "STARVED_SOAK_UNREADABLE",
    "assess", "describe", "read_soak_tail", "run_starved_account_check",
    "starved_accounts", "status",
]
