"""Is every OPEN leg of an order package still reachable by the exit path?

**The gap this watches.** ``order_monitor``'s strategy loop drives exits per
ORDER PACKAGE — it selects ``get_order_packages_by_strategy(strategy,
status="open")`` — and both effectuation branches of ``_apply_update`` then
resolve ONE trade row from ``open_pkg["linked_trade_id"]``. But
``Coordinator.multi_account_execute`` fans a single package out across N
accounts, producing N trade rows that share one ``order_package_id``. So:

* a trail/stale-stop/giveback MODIFY amends one account's bracket and syncs one
  ``trades`` row; every sibling keeps its entry-time bracket forever, and
* a monitor CLOSE closes the linked leg and flips the PARENT package to
  ``closed`` — after which the loop's ``status="open"`` filter drops the whole
  package, and the surviving legs can never be revisited.

``_cascade_close_netted_siblings`` does not cover this: its query is scoped
``AND account_id=?``, i.e. intra-account netting only.

**Why a detector and not just a fix.** The repair is Tier-3 order-path work
(``BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG``). This lands first so the
repair is verifiable and a regression is visible — the same order
``BL-20260816-COVERAGE-IS-ONE-SIDED`` was handled in. It is also the only
surface that can say the condition exists at all: a stranded leg renders as a
perfectly normal open position on ``/api/bot/positions`` and its package renders
as a perfectly normal closed package, so nothing in either view is wrong-looking.

**Observe-only.** One read-only SQLite connection on its own cadence. No socket,
no broker round-trip (preserving the invariant ``account_reachability_alert``
documents), no order path. It cannot refuse or close a trade.

**One assessor, two consumers.** ``assess()`` is the single source of the
verdict; the live alert here and the offline ``scripts/ops/exit_mechanics_audit``
both call it, so the alarm and the report can never disagree about a package —
the discipline ``src/runtime/dead_leg.py`` already applies to refusals.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.utils.paths import runtime_logs_dir, trade_journal_db_path

logger = logging.getLogger(__name__)

_STATE_FILENAME = "package_leg_coverage_state.json"
_LAST_CHECK_KEY = "__last_check__"

_CADENCE_ENV = "PACKAGE_LEG_CHECK_SECONDS"
_DEFAULT_CADENCE_S = 3600  # hourly: this is a standing condition, not an event

#: Per-package verdicts. Never collapsed into a single "bad" — each is a
#: DIFFERENT operator action. Registered with `collapsed-state-guard` as
#: `package_leg_coverage.verdict`.
VERDICTS: Tuple[str, ...] = (
    "managed",              # every open leg is reachable by the loop
    "divergent",            # open multi-leg package, sibling stops disagree
    "stranded",             # package CLOSED, open legs remain -> out of the loop
    "linked_unresolvable",  # open package whose managed leg cannot be identified
)

#: Verdicts that represent a real gap. `managed` is the healthy state;
#: `linked_unresolvable` IS included, because "we could not identify the managed
#: leg" is not evidence the legs are managed.
_GAP_VERDICTS = frozenset({"divergent", "stranded", "linked_unresolvable"})


# --------------------------------------------------------------------------
# knobs / state


def _int_knob(name: str, default: int, *, minimum: int = 0) -> int:
    """Read an int knob, falling back to the DEFAULT on garbage.

    Never to 0/disabled: a typo in a cadence must not silently switch off the
    only thing watching this class (the `silent_refusal_alert` rule).
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(float(raw)))
    except (TypeError, ValueError):
        return default


def _skip_set() -> frozenset:
    raw = os.environ.get("PACKAGE_LEG_CHECK_SKIP", "") or ""
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
        logger.debug("package_leg_coverage: state load failed: %s", exc)
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        logger.debug("package_leg_coverage: state save failed: %s", exc)


# --------------------------------------------------------------------------
# reads


def _read_journal(db_path: str) -> Tuple[List[dict], Dict[str, dict]]:
    """Open non-backtest trades + every package they belong to. READ-ONLY."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        trades = [dict(r) for r in conn.execute(
            "SELECT id, account_id, symbol, direction, position_size, "
            "       stop_loss, take_profit_1, order_package_id, strategy_name "
            "  FROM trades "
            " WHERE status='open' AND COALESCE(is_backtest,0)=0"
        )]
        pkg_ids = {t["order_package_id"] for t in trades if t.get("order_package_id")}
        packages: Dict[str, dict] = {}
        if pkg_ids:
            # Chunked IN(): sqlite's variable limit is 999 and the open book is
            # far smaller, but a fleet growth spurt must not turn this into a
            # silent partial read.
            ids = list(pkg_ids)
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ("SELECT order_package_id, strategy_name, symbol, status, sl, tp, "
                     "       linked_trade_id, close_reason "
                     "  FROM order_packages WHERE order_package_id IN (%s)"
                     % ",".join("?" * len(chunk)))
                for r in conn.execute(q, chunk):
                    packages[r["order_package_id"]] = dict(r)
        return trades, packages
    finally:
        conn.close()


# --------------------------------------------------------------------------
# the assessment — pure, and the ONLY place a verdict is decided


def assess(trades: Iterable[dict], packages: Dict[str, dict]) -> Dict[str, dict]:
    """Verdict per order package. Pure; no I/O.

    Keyed by ``order_package_id``. A trade carrying no package id is reported
    under the sentinel key ``"(no package)"`` with verdict
    ``linked_unresolvable`` — it is unreachable by a loop that iterates
    packages, which is the same operator problem by a different route.
    """
    by_pkg: Dict[Optional[str], List[dict]] = defaultdict(list)
    for t in trades:
        by_pkg[t.get("order_package_id")].append(t)

    out: Dict[str, dict] = {}
    for pkg_id, legs in by_pkg.items():
        legs = sorted(legs, key=lambda r: int(r["id"]))
        leg_view = [
            {"trade_id": t.get("id"), "account": t.get("account_id"),
             "qty": t.get("position_size"), "stop_loss": t.get("stop_loss")}
            for t in legs
        ]
        if not pkg_id:
            out["(no package)"] = {
                "verdict": "linked_unresolvable",
                "reason": "open trade carries no order_package_id",
                "legs": leg_view, "leg_count": len(legs),
            }
            continue

        pkg = packages.get(pkg_id)
        if pkg is None:
            out[pkg_id] = {
                "verdict": "linked_unresolvable",
                "reason": "order_packages row not found",
                "legs": leg_view, "leg_count": len(legs),
            }
            continue

        linked = pkg.get("linked_trade_id")
        base = {
            "strategy": pkg.get("strategy_name"), "symbol": pkg.get("symbol"),
            "package_status": pkg.get("status"), "package_sl": pkg.get("sl"),
            "linked_trade_id": linked, "close_reason": pkg.get("close_reason"),
            "legs": leg_view, "leg_count": len(legs),
        }
        for lv, t in zip(leg_view, legs):
            lv["is_linked"] = str(t.get("id")) == str(linked)

        if str(pkg.get("status")) == "closed":
            # Every open leg here is outside the loop, linked or not: the
            # package will never be selected again.
            out[pkg_id] = {**base, "verdict": "stranded",
                           "reason": f"package closed ({pkg.get('close_reason')}) "
                                     f"with {len(legs)} open leg(s)"}
            continue

        if linked is not None and not any(
                str(t.get("id")) == str(linked) for t in legs):
            # The managed leg is not among the open rows. We cannot say the
            # survivors are managed — that is not the same as saying they are.
            out[pkg_id] = {**base, "verdict": "linked_unresolvable",
                           "reason": "linked_trade_id names no OPEN trade row"}
            continue

        stops = {round(float(t["stop_loss"]), 10)
                 for t in legs if t.get("stop_loss") is not None}
        unmeasured = any(t.get("stop_loss") is None for t in legs)
        if len(legs) > 1 and len(stops) > 1:
            out[pkg_id] = {**base, "verdict": "divergent",
                           "stop_values": sorted(stops),
                           "stop_unmeasured_legs": unmeasured,
                           "reason": f"{len(stops)} distinct sibling stops "
                                     f"across {len(legs)} legs"}
            continue

        out[pkg_id] = {**base, "verdict": "managed",
                       "stop_unmeasured_legs": unmeasured}
    return out


def summarize(verdicts: Dict[str, dict]) -> Dict[str, Any]:
    """Counts by verdict, plus the leg totals a reader needs as a denominator."""
    counts = {v: 0 for v in VERDICTS}
    legs_by_verdict = {v: 0 for v in VERDICTS}
    for row in verdicts.values():
        v = row.get("verdict", "managed")
        counts[v] = counts.get(v, 0) + 1
        legs_by_verdict[v] = legs_by_verdict.get(v, 0) + int(row.get("leg_count") or 0)
    total_legs = sum(legs_by_verdict.values())
    return {
        "packages": len(verdicts),
        "open_legs": total_legs,
        "by_verdict": counts,
        "legs_by_verdict": legs_by_verdict,
        # The two headline numbers, named so a reader never has to add them up.
        "stranded_legs": legs_by_verdict.get("stranded", 0),
        "divergent_packages": counts.get("divergent", 0),
    }


# --------------------------------------------------------------------------
# alerting


def _send_alert(message: str) -> None:
    """One Telegram + one typed WARNING push — the same loud channel
    `account_reachability_alert` / `silent_refusal_alert` use, so this lands
    beside its siblings rather than inventing a third alert style."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("package_leg_coverage: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("package_leg_coverage: fcm WARNING publish failed: %s", exc)


_VERDICT_HINT = {
    "stranded": ("its package is already closed, so order_monitor will never "
                 "select it again — the leg exits only on its own resting "
                 "bracket or the reconciler"),
    "divergent": ("a trail moved the linked leg only; the sibling still holds "
                  "its entry-time bracket at the venue"),
    "linked_unresolvable": ("the managed leg could not be identified — this is "
                            "'we could not look', not 'the legs are fine'"),
}


def _describe(pkg_id: str, row: dict) -> str:
    legs = ", ".join(
        f"#{leg['trade_id']}/{leg['account']}"
        + (" (linked)" if leg.get("is_linked") else "")
        + (f" SL={leg['stop_loss']}" if leg.get("stop_loss") is not None else " SL=—")
        for leg in row.get("legs", [])
    )
    return (f"{row.get('verdict','?').upper()} {pkg_id} "
            f"[{row.get('strategy')}/{row.get('symbol')}]: {row.get('reason')}. "
            f"Legs: {legs}. Why it matters: {_VERDICT_HINT.get(row.get('verdict'), '')}")


def package_leg_gaps() -> Dict[str, dict]:
    """Currently-latched gaps, for the review skills.

    Mirrors ``account_reachability_alert.down_accounts()`` /
    ``silent_refusal_alert.silent_accounts()``.
    """
    return {k: v for k, v in _load_state().items() if k != _LAST_CHECK_KEY}


def run_package_leg_check(*, force: bool = False) -> Dict[str, Any]:
    """Cadenced check. Returns the envelope; never raises into the tick.

    A journal-read failure returns ``checked: False`` and latches NOTHING —
    it must never read as "no package has a gap".
    """
    cadence = _int_knob(_CADENCE_ENV, _DEFAULT_CADENCE_S)
    state = _load_state()
    now = time.time()
    if not force and cadence > 0:
        last = state.get(_LAST_CHECK_KEY)
        if isinstance(last, (int, float)) and (now - last) < cadence:
            return {"checked": False, "reason": "cadence"}

    try:
        trades, packages = _read_journal(str(trade_journal_db_path()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("package_leg_coverage: journal read failed: %s", exc)
        return {"checked": False, "reason": "read_failed", "error": str(exc)}

    verdicts = assess(trades, packages)
    summary = summarize(verdicts)
    skip = _skip_set()

    state[_LAST_CHECK_KEY] = now
    fired: List[str] = []
    for pkg_id, row in verdicts.items():
        if pkg_id in skip:
            continue
        v = row.get("verdict")
        if v not in _GAP_VERDICTS:
            state.pop(pkg_id, None)  # recovered (or repaired) — clear the latch
            continue
        prev = state.get(pkg_id)
        # Latched per (package, verdict): a package that moves from divergent to
        # stranded is a NEW condition and must alert again, but an unchanged one
        # must not re-ping every hour (the desensitized-alarm P1).
        if isinstance(prev, dict) and prev.get("verdict") == v:
            continue
        state[pkg_id] = {"verdict": v, "reason": row.get("reason"),
                         "strategy": row.get("strategy"), "symbol": row.get("symbol"),
                         "legs": row.get("legs"),
                         "since": datetime.now(timezone.utc).isoformat()}
        fired.append(_describe(pkg_id, row))

    _save_state(state)

    if fired:
        head = (f"⚠️ EXIT-PATH GAP — {len(fired)} order package(s) hold open legs "
                f"the monitor cannot manage "
                f"({summary['stranded_legs']} stranded leg(s), "
                f"{summary['divergent_packages']} divergent package(s) "
                f"over {summary['open_legs']} open leg(s))")
        _send_alert(head + "\n\n" + "\n\n".join(fired[:5]))

    return {"checked": True, "summary": summary, "alerts_fired": len(fired),
            "verdicts": verdicts}


def write_state_snapshot() -> None:
    """No-op placeholder kept out of the tick: the latch file IS the snapshot."""


__all__ = ["assess", "summarize", "run_package_leg_check", "package_leg_gaps",
           "VERDICTS"]
