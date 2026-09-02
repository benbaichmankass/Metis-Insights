#!/usr/bin/env python3
"""Can a backlog row that is WAITING FOR TRADES TO ACCRUE ever actually accrue?

WHY THIS EXISTS — measured, not asserted (2026-09-02, performance-backlog drain).

``docs/claude/performance-review-backlog.json`` carried 31 ``kept_open`` rows
against 13 ``open`` — 2.4x — and the bulk of the ``kept_open`` stock says some
version of *"revisit when >= N closed trades accrue on leg L"*. The existing
``check_backlog_criteria`` guard accepts that as a perfectly good exit
condition, and it is one **only if the clock runs**. Nothing had ever measured
whether it does.

Measured against the live journal at trader sha ``68e73de8`` over the eleven
accrual-gated rows in that file, resolving each row's named legs and counting
``trades`` rows closed since the row was FILED:

* **five rows had long since passed their own threshold** — 375 closed trades
  sat behind rows whose text says "waiting". Nobody had looked.
* **four rows named a leg that cannot produce a trade at all.**
  ``tqqq_trend_long_1d`` and ``splg_trend_long_1d`` have **zero rows in the
  journal, ever** — not zero closes, zero rows. ``eth_pullback_prop_2h`` and
  ``htf_pullback_trend_2h`` are ``execution: shadow``, which is the declared
  no-order gate: they log and place nothing, by construction and on purpose.

So NOT ONE of the eleven was in the state its own text implies ("waiting, on
track"). That is the class, and it is why ``kept_open`` outnumbers ``open``:
an accrual threshold on a leg that does not trade is a **permanent resident
wearing a field** — the exact failure
``BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED``
names, one level down, where the field is present and vacuous rather than absent.

THE HALF THAT IS CHECKABLE OFFLINE, AND IT IS THE HALF THAT MATTERS.
Whether a leg has *already* accrued needs the live journal and cannot run in CI.
But whether a leg **can** accrue is a pure read of two committed config files:
is it declared, is it enabled, is its ``execution`` gate ``live``, and is it
routed to any account that is itself ``mode: live``? A row waiting on a
``shadow`` leg is refutable with no network at all — and three of the four dead
rows above are refutable exactly that way. That is what this module computes and
what the guard in ``check_backlog_criteria.py`` enforces on new rows.

FIVE STATES, NEVER COLLAPSED (this repo's standing rule, and each of the five
was observed in the eleven-row population):

``can_run``            declared, enabled, ``execution: live``, routed to at
                       least one ``mode: live`` account. The clock can tick.
``gated_shadow``       ``execution: shadow`` — the strategy-level gate in
                       ``CLAUDE.md`` § "The two execution gates". It runs and
                       logs and NEVER sends a live order, so an accrual
                       threshold on it is unreachable BY DESIGN, not by
                       misfortune.
``gated_disabled``     ``enabled: false``.
``not_routed``         declared and live, but no ``mode: live`` account lists
                       it, so nothing will ever dispatch it.
``absent_from_config`` no such strategy key. Usually a renamed or removed leg,
                       and a row waiting on it is waiting on nothing.

There is deliberately no ``unknown`` fudge: every one of the five is a
*determination*. What this module does NOT claim is the live half — a
``can_run`` verdict says the gate is open, never that the leg has traded or
will trade often enough. Read it beside a measured count; it is the necessary
condition, not the sufficient one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _backlog import UnsupportedCriteriaShape, criteria_text  # noqa: E402

CAN_RUN = "can_run"
GATED_SHADOW = "gated_shadow"
GATED_DISABLED = "gated_disabled"
NOT_ROUTED = "not_routed"
ABSENT = "absent_from_config"

#: Every state a leg's accrual clock can be in. Exported so a caller cannot
#: hand-roll a fourth spelling of one of them.
CLOCK_STATES = (CAN_RUN, GATED_SHADOW, GATED_DISABLED, NOT_ROUTED, ABSENT)

#: A state in which no trade will ever arrive. ``can_run`` is the only member
#: of the complement, which is the point: the negative set is the interesting one.
DEAD_CLOCK_STATES = frozenset({GATED_SHADOW, GATED_DISABLED, NOT_ROUTED, ABSENT})

#: The field under which a row names the strategy legs whose trades it is
#: waiting for. A LIST of strategy keys as they appear in
#: ``config/strategies.yaml`` — not prose, because the whole point is that a
#: future session can resolve them mechanically instead of re-reading the row.
ACCRUAL_LEGS_FIELD = "accrual_legs"

#: Phrases that mark an exit condition as ACCRUAL-GATED — i.e. the row is
#: waiting for trades to happen rather than for a measurement, a deploy or a
#: decision. Deliberately narrow: a false positive here would demand
#: ``accrual_legs`` from a row that is not waiting on trades at all, and a guard
#: that fires on the wrong rows gets switched off. Each pattern was read off the
#: eleven measured members rather than imagined.
_ACCRUAL_PATTERNS = (
    r"\baccrues?\b",
    r"\baccrual\b",
    r"\bonce trades\b",
    r"\btrades accrue\b",
    r"\bfresh trades\b",
    r"\bclean closed\b",
    r"\bsoak[- ]gated\b",
    r"\bpaper soak\b",
    r"\blive track record\b",
    r"\bwhen live (?:data|n)\b",
)
_ACCRUAL_RE = re.compile("|".join(_ACCRUAL_PATTERNS), re.I)


def is_accrual_shaped(text: str) -> bool:
    """Does this exit-condition prose say "wait for trades to happen"?"""
    return bool(_ACCRUAL_RE.search(text or ""))


def accrual_legs(row: dict[str, Any]) -> list[str]:
    """The strategy keys this row is waiting on, or ``[]`` when it names none.

    ``[]`` is NOT "the row waits on nothing" — it is "the row does not say",
    which is precisely the condition the guard refuses for a new accrual row.
    """
    raw = row.get(ACCRUAL_LEGS_FIELD)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]


def _load_yaml(path: pathlib.Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is a hard dep of the runtime
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, ValueError):
        return {}


def load_config(root: pathlib.Path | None = None) -> tuple[dict, dict]:
    """``(strategies, accounts)`` maps from the committed config."""
    root = root or pathlib.Path(".")
    s = _load_yaml(root / "config" / "strategies.yaml")
    a = _load_yaml(root / "config" / "accounts.yaml")
    return (s.get("strategies", s) or {}), (a.get("accounts", a) or {})


def clock_state(leg: str, strategies: dict, accounts: dict) -> str:
    """Can trades for *leg* ever reach the journal? One of :data:`CLOCK_STATES`.

    ORDER IS LOAD-BEARING. ``absent`` is checked before ``disabled`` before the
    ``execution`` gate before routing, so the reported state is the FIRST reason
    the clock is stopped rather than an arbitrary one — a leg that is both
    ``shadow`` and unrouted should report ``gated_shadow``, because that is the
    thing a reader would have to change first.
    """
    cfg = strategies.get(leg)
    if not isinstance(cfg, dict):
        return ABSENT
    if cfg.get("enabled") is False:
        return GATED_DISABLED
    # `execution` defaults to `live` when omitted — CLAUDE.md § "The two
    # execution gates": both gates default PERMISSIVE, so an omitted key is a
    # live leg, never a demoted one. Reading an absent key as `shadow` would
    # manufacture a dead clock for most of the fleet.
    if str(cfg.get("execution", "live")).strip().lower() == "shadow":
        return GATED_SHADOW
    for acct in accounts.values():
        if not isinstance(acct, dict):
            continue
        # The ACCOUNT-level gate, the other of the two. A leg routed only to
        # `dry_run` accounts is dispatched and refused, so no trade arrives.
        if str(acct.get("mode", "live")).strip().lower() != "live":
            continue
        if leg in (acct.get("strategies") or []):
            return CAN_RUN
    return NOT_ROUTED


#: The four review backlogs. The class was MEASURED on the performance file, but
#: the mechanism is not specific to it — an accrual threshold on a shadow-gated
#: leg is unfalsifiable wherever it is filed, and the health backlog alone
#: carries 187 kept_open rows. The census therefore sweeps all four so the stock
#: stays visible; the GUARD stays diff-scoped, so widening the census cannot
#: fail anybody's PR.
ALL_BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)


def _load_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return d["items"] if isinstance(d, dict) and "items" in d else (d if isinstance(d, list) else [])


def exit_text(row: dict[str, Any]) -> str:
    """This row's exit-condition prose, across every field it may live under."""
    try:
        base = criteria_text(row)
    except UnsupportedCriteriaShape:
        base = ""
    parts = [base]
    for f in ("trigger_condition", "next_action", "next_step", "revisit_conditions"):
        v = row.get(f)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v if isinstance(x, str))
    return "\n".join(p for p in parts if p)


def census(backlog: pathlib.Path, root: pathlib.Path | None = None) -> list[tuple[str, str, str]]:
    """``[(row id, leg, state)]`` for every accrual-shaped carried row."""
    strategies, accounts = load_config(root)
    out: list[tuple[str, str, str]] = []
    for row in _load_rows(backlog):
        rid = str(row.get("id") or "")
        if not rid or str(row.get("status") or "") not in {"open", "kept_open"}:
            continue
        if not is_accrual_shaped(exit_text(row)):
            continue
        legs = accrual_legs(row)
        if not legs:
            out.append((rid, "(none named)", "unstated"))
            continue
        for leg in legs:
            out.append((rid, leg, clock_state(leg, strategies, accounts)))
    return out


def _self_test() -> int:
    """Assert each of the five states is REACHABLE and correctly distinguished.

    A state nothing can produce is a state nobody can rely on, and this module's
    whole claim is that the five are separable.
    """
    strategies = {
        "live_leg": {"enabled": True, "execution": "live"},
        "default_leg": {"enabled": True},                       # execution omitted -> live
        "shadow_leg": {"enabled": True, "execution": "shadow"},
        "off_leg": {"enabled": False, "execution": "live"},
        "orphan_leg": {"enabled": True, "execution": "live"},   # declared, routed nowhere
        "dry_only_leg": {"enabled": True, "execution": "live"},
    }
    accounts = {
        "acct_live": {"mode": "live", "strategies": ["live_leg", "default_leg", "shadow_leg", "off_leg"]},
        "acct_dry": {"mode": "dry_run", "strategies": ["dry_only_leg"]},
    }
    cases = {
        "live_leg": CAN_RUN,
        "default_leg": CAN_RUN,
        "shadow_leg": GATED_SHADOW,
        "off_leg": GATED_DISABLED,
        "orphan_leg": NOT_ROUTED,
        "dry_only_leg": NOT_ROUTED,
        "no_such_leg": ABSENT,
    }
    bad = []
    for leg, want in cases.items():
        got = clock_state(leg, strategies, accounts)
        if got != want:
            bad.append(f"clock_state({leg!r}) = {got!r}, expected {want!r}")
    if set(cases.values()) != set(CLOCK_STATES):
        bad.append(f"self-test does not exercise every state: {sorted(set(CLOCK_STATES) - set(cases.values()))}")

    # The accrual detector: positives read off the real corpus, negatives that
    # must NOT trip it (a measurement, a deploy and a decision are not accrual).
    pos = [
        "Enough real bybit_2 trades accrue to form a meaningful track record",
        "If paper win-rate recovers >45% over >=15 fresh trades",
        ">=20-30 clean closed paper trades per cell",
        "Once trades accrue: review the real PnL",
        "paper soak reviewed; Tier-3 promotion PR proposed or deferred",
    ]
    neg = [
        # A fleet-wide MEASUREMENT row that merely mentions a minimum
        # population. It is not waiting for trades to happen and names no leg,
        # so demanding `accrual_legs` of it would be the guard firing on the
        # wrong row -- measured: this exact text tripped an earlier, looser
        # pattern (`closed trades`) on PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN.
        "Over a 30d window of at least 20 closed trades, no per-strategy "
        "expectancyR exceeds a stated plausible bound without an explicit "
        "outlier annotation, and the real-money totalR sign matches its totalPnl sign",
        "Add a docstring note at src/runtime/exit_plan_realism.py stating the clamp is observe-only",
        "candidate_ev_score's fee_R uses the venue-aware per-symbol roundtrip bps, verified by a test",
        "An operator decision on record for this leg - sized down, re-parameterised, or left as-is",
    ]
    for t in pos:
        if not is_accrual_shaped(t):
            bad.append(f"is_accrual_shaped MISSED an accrual criterion: {t!r}")
    for t in neg:
        if is_accrual_shaped(t):
            bad.append(f"is_accrual_shaped FIRED on a non-accrual criterion: {t!r}")

    if bad:
        print("accrual-clock self-test: FAIL")
        for b in bad:
            print("  " + b)
        return 1
    print(f"accrual-clock self-test: OK — {len(cases)} clock cases over all "
          f"{len(CLOCK_STATES)} states, {len(pos)} accrual positives, {len(neg)} negatives.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backlog", default=None,
                    help="one backlog to census; default is all four")
    ap.add_argument("--all", action="store_true", help="print the census")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    targets = [args.backlog] if args.backlog else list(ALL_BACKLOGS)
    total = dead_n = 0
    for rel in targets:
        path = pathlib.Path(rel)
        if not path.exists():
            # ANNOUNCED, never silent: a census that quietly skips a file
            # under-reports the stock it exists to keep visible.
            print(f"accrual-clock census: SKIPPING {rel} — not present.")
            continue
        rows = census(path)
        dead = [r for r in rows if r[2] in DEAD_CLOCK_STATES or r[2] == "unstated"]
        total += len(rows)
        dead_n += len(dead)
        print(f"\n{rel}: {len(rows)} (row, leg) pair(s), {len(dead)} on a clock "
              f"that cannot tick.")
        for rid, leg, state in rows:
            mark = "  " if state == CAN_RUN else "!!"
            print(f"  {mark} {rid:56s} {leg:26s} {state}")
    if not total:
        print("accrual-clock census: no accrual-shaped carried rows found.")
        return 0
    print(f"\nTOTAL {total} (row, leg) pair(s), {dead_n} on a clock that cannot tick.")
    print("\nA leg outside `can_run` will never deliver the trades its row is "
          "waiting for. `can_run` is the NECESSARY condition only — it says the "
          "gate is open, never that the leg has traded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
