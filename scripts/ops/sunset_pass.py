#!/usr/bin/env python3
# wiring: .github/workflows/sunset-pass.yml (weekly cron + workflow_dispatch)
"""E3 — the SUNSET PASS. The pass that asks *should this come out?*

Phase G of the operating-layer build
([`docs/design/operating-layer-build-plan-DESIGN.md`](../../docs/design/operating-layer-build-plan-DESIGN.md)).

WHY THIS EXISTS, MEASURED
=========================
*"6 strategy retirements ever, none in five weeks against 45 live legs, and
nothing has ever retired a skill, register, workflow or guard. Complexity is
monotonic by construction until something removes."*

⚠️ **THE BUILD PLAN NAMES THE WRONG CAUSE, AND THIS PASS IS SHAPED BY THE RIGHT
ONE.** The plan says the M7 kill packet cannot fire because *"Override 5
converts every kill to `tune` pending artifacts that do not exist."* Measured
against the first committed packet run — population: **all 52 enabled
strategies**, `comms/strategy_reviews/2026-09-01/`, window 2026-08-25 →
2026-09-01:

* **Override 5 fired on 0 of 52.** It is never reached.
* **52 of 52 graded `hold`, 0 actionable.** 35 had **zero closed trades in the
  window**; the other 17 sat below the floor.
* **The highest `n_closed` across all 52 was 8, against a KILL/DEMOTE floor of
  20.** The gate is 2.5x short of its own floor at its single best leg.

The matrix short-circuits on insufficient evidence long before it can reach
`demote_shadow`/`kill`, so Override 5 grades nothing. The real blocker is that
**the gate's evidence window (7 days) is far shorter than the sample its own
floor demands**, and no leg can close 20 trades in a week.

AND UNDERNEATH THAT, THE COLLAPSED STATE THIS PASS EXISTS FOR
------------------------------------------------------------
The gate files these two under one verdict:

* a leg that is **young** and has not yet closed 20 trades, and
* a leg that has **never closed a single trade in its life**

Both read `hold — insufficient evidence`. Measured over the same 52, joined to
lifetime closes: **12 legs have never closed a trade, ever** — and the one
mechanism that could retire them files them identically to a healthy young leg,
every day, forever. *We have not judged this leg yet* and *this leg has nothing
to judge and never will at this rate* are opposite facts. That is why nothing
gets removed, and it is the distinction this pass is built to make.

WHAT THIS DOES **NOT** DO — the anti-duplication contract
=========================================================
Building a mechanism that already exists is `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`.
This pass **re-implements no detector**. It consumes the ones that exist:

* ``comms/strategy_reviews/*/INDEX.json`` — the M7 gate's OWN output, across
  every date it has produced. This pass never re-grades a strategy and never
  restates the gate's thresholds (see ``_floor_from_reason``).
* ``scripts/ci/check_unwired_artifacts.py`` — **imported**, never copied, for
  the "is this tool wired to anything that runs it" question. That guard is
  correct and already registered; what it does not do is force an answer.

What none of them does is ask *should this come out* and require a written
answer. Every register in this repo grows; `check_unwired_artifacts` reports
**115 findings and exits 0**. A detector that reports and passes is a list.
The disposition register plus ``scripts/ci/check_sunset_dispositions.py`` are
the half that makes a candidate cost something to ignore.

⚠️ **THIS PASS PROPOSES. IT NEVER ENACTS.** Retiring a live strategy leg is
**Tier-3** (`config/strategies.yaml`). Nothing here writes config, flips an
`execution:` field, or deletes a file. The output is an artifact and a
proposal.

STATES, AND WHY NONE OF THEM IS COLLAPSED
=========================================
Strategy legs (``strategy_verdict``):

``governed_elsewhere``
    The M7 gate reached its matrix for this leg on at least one date — it can
    grade it on merit. E3 **stands off**; re-grading it here would be the
    duplication above. This is a real branch, not decoration.
``retire_candidate``
    The gate has never once been able to grade it AND the evidence says it is
    not accumulating: see ``basis`` for which evidence carried it.
``watch``
    Short-circuited, but the evidence does not (yet) support a candidacy —
    too few passes, or it is still closing trades.
``not_assessed``
    **We could not look.** Absent from every index, unreadable, or routed to a
    venue this pass structurally cannot measure (see the prop caveat below).
    Never to be read as "fine".

⚠️ **THE PROP CAVEAT IS NOT A DETAIL — IT IS 25% OF THE DAY-ONE CANDIDATE
SET.** Lifetime closes come from ``/api/bot/performance``, which reads the
``trades`` table. Prop fills are **deliberately isolated** in ``prop_fills`` so
prop never leaks into the real-money/paper KPIs, so a prop-routed leg reads
**zero lifetime closes while trading normally**. Of the 12 legs that read
"never closed a trade", **3 are the `breakout_1` prop legs** — false positives
that a naive pass would propose for retirement. Any leg routed to an account
with ``account_class: prop`` is therefore ``not_assessed``, with the reason
recorded on the row. A leg routed to **no account at all** is the opposite
case and is recorded as such: it cannot trade by construction.

Machinery (``machinery_verdict``) mirrors `check_unwired_artifacts`'s own
buckets rather than inventing a second vocabulary: ``unwired`` ·
``doc_only`` · ``skill_invoked`` · ``not_assessed``.

THE LIFETIME READ IS THREE-STATE
================================
``lifetime_state`` ∈ ``read`` · ``not_read`` (**no capture was supplied — we
did not look**) · ``unreadable`` (a capture was supplied and could not be
parsed). That describes THE CAPTURE. It does not describe an individual leg,
and conflating the two is how this pass invented a corpse.

**So each leg carries its own ``leg_lifetime_state``** ∈ ``observed`` ·
``not_observed`` · ``not_read`` / ``unreadable``. ⚠️ **A leg missing from a
``read`` capture has NOT been measured at zero.** ``/api/bot/performance``
filters ``AND t.pnl IS NOT NULL``, so it lists every strategy with a
**pnl-bearing** close — not with *any* close. A leg whose every close landed
``pnl NULL`` is simply absent. *"We did not observe a pnl-bearing close"* and
*"the leg never closed a trade"* are different facts; this pass used to default
the first to ``0`` and report the second, which manufactured
``retire_candidate`` verdicts (11 of 52 enabled legs absent, 2026-09-05 —
``docs/claude/diagnoses/MI-124-never-firing-legs-diagnosis.md``). ``not_observed``
never proposes a retirement on its own. To settle whether such a leg ever
closed, count ``trade_journal.db::trades`` directly — the performance route is
a performance view, not an "ever closed" oracle.

USAGE
-----
    python3 scripts/ops/sunset_pass.py --write
    python3 scripts/ops/sunset_pass.py --lifetime-json perf.json --write
    python3 scripts/ops/sunset_pass.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
REVIEWS = REPO / "comms" / "strategy_reviews"
OUT_ROOT = REPO / "comms" / "sunset"
STRATEGIES_YAML = REPO / "config" / "strategies.yaml"
ACCOUNTS_YAML = REPO / "config" / "accounts.yaml"

SCHEMA_VERSION = 1

#: How many DISTINCT packet dates must agree before a leg the gate cannot grade
#: is proposed for retirement on index evidence alone. CHOSEN, not measured —
#: there is one packet date in existence as this ships, so a value of 1 would
#: make the pass name candidates off a single day's window, which for a 1d leg
#: is no evidence at all. A leg with a `read` lifetime of zero closes bypasses
#: this, because that evidence does not depend on how many passes have run.
MIN_PASSES_FOR_INDEX_BASIS = 3

#: The reason text the M7 gate emits when it never reached its matrix. Matched
#: rather than re-derived: this pass must not hold a second opinion about what
#: "the gate could not grade it" means.
_ZERO_CLOSED = "no closed trades in window"
_INSUFFICIENT = "insufficient evidence"
_FLOOR_RE = re.compile(r"n_closed=(\d+)\s*<\s*(\d+)")


# ---------------------------------------------------------------------------
# reading what already exists
# ---------------------------------------------------------------------------
def _load_json(p: Path) -> Optional[Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- we could not look
        return None


def _floor_from_reason(reason: str) -> Optional[int]:
    """Read the gate's KILL/DEMOTE floor out of the gate's OWN words.

    The floor lives as a literal inside `strategy_review_packet.py`'s reason
    string (`"insufficient evidence (n_closed=1 < 20) ..."`). Parsing it here
    rather than restating `20` is deliberate: two files holding the same
    threshold is how they drift, and this pass has no business having an
    opinion about the gate's floor. If the gate's floor moves, this follows it.
    Unparseable -> ``None``, and the caller degrades rather than guessing.
    """
    m = _FLOOR_RE.search(reason or "")
    return int(m.group(2)) if m else None


def read_review_indices(root: Path = REVIEWS) -> Tuple[List[Tuple[str, dict]], List[str]]:
    """Every committed M7 packet index, oldest first, plus unreadable dates."""
    out: List[Tuple[str, dict]] = []
    bad: List[str] = []
    if not root.is_dir():
        return out, bad
    for day in sorted(p for p in root.iterdir() if p.is_dir()):
        idx = _load_json(day / "INDEX.json")
        if isinstance(idx, dict) and isinstance(idx.get("rows"), list):
            out.append((day.name, idx))
        else:
            bad.append(day.name)
    return out, bad


def _yaml(p: Path) -> Optional[dict]:
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def account_routing(accounts_yaml: Path = ACCOUNTS_YAML) -> Optional[Dict[str, List[Tuple[str, str]]]]:
    """strategy -> [(account_id, account_class)]. ``None`` when unreadable.

    Returned as ``None`` rather than ``{}`` on a read failure: an empty map
    would make *every* leg look unrouted, which is this pass's strongest
    retirement signal. A failed read must never manufacture 52 candidates.
    """
    doc = _yaml(accounts_yaml)
    if not isinstance(doc, dict):
        return None
    accounts = doc.get("accounts")
    if not isinstance(accounts, dict):
        return None
    routing: Dict[str, List[Tuple[str, str]]] = {}
    for aid, cfg in accounts.items():
        if not isinstance(cfg, dict):
            continue
        klass = str(cfg.get("account_class") or "unknown")
        for s in (cfg.get("strategies") or []):
            routing.setdefault(str(s), []).append((str(aid), klass))
    return routing


def lifetime_closes(capture: Optional[Any]) -> Tuple[str, Dict[str, int]]:
    """(lifetime_state, {strategy: lifetime_closed_trades}).

    ``capture`` is a ``/api/bot/performance?window=all`` document. Real-money
    and paper are summed because a leg's liveness is not an account question —
    a paper leg that closes trades is producing evidence.
    """
    if capture is None:
        return "not_read", {}
    if not isinstance(capture, dict) or not isinstance(capture.get("perStrategy"), list):
        return "unreadable", {}
    tot: Dict[str, int] = {}
    for block in (capture, capture.get("paper") or {}):
        if not isinstance(block, dict):
            continue
        for row in (block.get("perStrategy") or []):
            if isinstance(row, dict) and row.get("name") is not None:
                tot[str(row["name"])] = tot.get(str(row["name"]), 0) + int(row.get("trades") or 0)
    return "read", tot


# ---------------------------------------------------------------------------
# the strategy half
# ---------------------------------------------------------------------------
def grade_strategies(
    indices: List[Tuple[str, dict]],
    *,
    lifetime_state: str,
    lifetime: Dict[str, int],
    routing: Optional[Dict[str, List[Tuple[str, str]]]],
) -> List[dict]:
    """One row per leg the M7 gate has ever graded. See the module docstring."""
    seen: Dict[str, dict] = {}
    for day, idx in indices:
        for row in idx.get("rows") or []:
            if not isinstance(row, dict) or not row.get("strategy"):
                continue
            name = str(row["strategy"])
            reasons = " | ".join(str(r) for r in (row.get("reasons") or []))
            n_closed = row.get("n_closed")
            st = seen.setdefault(name, {
                "strategy": name, "first_seen": day, "last_seen": day,
                "indices_seen": 0, "max_n_closed_seen": None,
                "reached_matrix_on": [], "closes_latest": None, "floor": None,
            })
            st["last_seen"] = day
            st["indices_seen"] += 1
            if isinstance(n_closed, int):
                st["closes_latest"] = n_closed
                st["max_n_closed_seen"] = max(st["max_n_closed_seen"] or 0, n_closed)
            floor = _floor_from_reason(reasons)
            if floor is not None:
                st["floor"] = floor
            # The gate SHORT-CIRCUITED when it says so in its own words. Anything
            # else means it reached the matrix and graded on merit.
            if not (_ZERO_CLOSED in reasons or _INSUFFICIENT in reasons):
                st["reached_matrix_on"].append(day)

    rows: List[dict] = []
    for name, st in sorted(seen.items()):
        routed = None if routing is None else routing.get(name, [])
        classes = {k for _, k in (routed or [])}
        # ⚠️ THE PER-LEG LIFETIME READ IS ITSELF THREE-STATE, AND IT USED TO BE
        # COLLAPSED HERE. The comment that stood here claimed
        # `/api/bot/performance` "lists every strategy with any closed trade, so
        # under `read` an absent leg genuinely closed ZERO — a real
        # measurement." **That claim was false**, and it was the load-bearing
        # part: it is what persuaded a reader the default was an observation.
        # `src/web/api/routers/performance.py` filters `AND t.pnl IS NOT NULL`,
        # so the capture lists every strategy with a **pnl-bearing** close, not
        # with *any* close. A leg whose every close landed `pnl NULL` is simply
        # ABSENT — and `lifetime.get(name, 0)` turned that absence into a
        # measured zero, which carried basis `never_closed_lifetime` and the
        # note "has never closed a single trade in its life".
        #
        # Measured 2026-09-05 against `/api/bot/performance?window=all`: of 52
        # enabled legs, 46 were in the capture and **11 were absent and silently
        # defaulted to 0**. Nine of the ten legs the 2026-09-01 packet proposed
        # retiring were among them, and **five of those ten had closed trades in
        # `trade_journal.db::trades` before the packet was written** — so the
        # packet's stated basis was false for half of what it proposed retiring.
        # See docs/claude/diagnoses/MI-124-never-firing-legs-diagnosis.md §2.
        #
        # So absence is carried as its own state and never as a number:
        #   `observed`     — the leg is IN a `read` capture; its count is real.
        #   `not_observed` — the capture was read and this leg is NOT in it. We
        #                    did not observe a pnl-bearing close. That is NOT
        #                    the same fact as "it never closed a trade", and it
        #                    must never on its own propose a retirement.
        #   `not_read` / `unreadable` — we did not look at all.
        leg_lifetime_state = (
            lifetime_state if lifetime_state != "read"
            else "observed" if name in lifetime
            else "not_observed"
        )
        # None whenever the leg was not measured — never a manufactured 0.
        life = lifetime.get(name)

        verdict, basis, note = "watch", "none", ""

        if st["reached_matrix_on"]:
            verdict, basis = "governed_elsewhere", "gate_reached_matrix"
            note = (f"the M7 gate graded this on merit on "
                    f"{len(st['reached_matrix_on'])} date(s) — E3 stands off.")
        elif routing is None:
            verdict, basis = "not_assessed", "routing_unreadable"
            note = "config/accounts.yaml could not be read; routing is unknown, not empty."
        elif "prop" in classes:
            verdict, basis = "not_assessed", "prop_routed"
            note = ("routed to a prop account — prop fills live in `prop_fills`, isolated "
                    "from `trades`, so the lifetime read structurally cannot see them. "
                    "A zero here is OUR blindness, not the leg's silence.")
        elif not routed:
            verdict, basis = "retire_candidate", "unrouted"
            note = ("declared in strategies.yaml and routed to NO account — it cannot "
                    "reach the order path at all, so it can never become gradeable.")
        elif leg_lifetime_state == "observed" and (life or 0) == 0:
            verdict, basis = "retire_candidate", "never_closed_lifetime"
            # Stated as what was MEASURED. This leg is IN the capture and reads
            # zero — it is not the absent case, which is `lifetime_not_observed`
            # and is never a candidate.
            note = ("is present in the lifetime capture and reads ZERO pnl-bearing closes "
                    "across its life, and the gate files it identically to a healthy "
                    "young leg every day.")
        elif (st["indices_seen"] >= MIN_PASSES_FOR_INDEX_BASIS
              and (st["max_n_closed_seen"] or 0) == 0):
            verdict, basis = "retire_candidate", "persistently_silent"
            note = (f"zero closed trades across {st['indices_seen']} consecutive packet "
                    f"dates; the gate has never had anything to grade.")
        else:
            verdict = "watch"
            if lifetime_state != "read":
                basis = "lifetime_not_read"
            elif leg_lifetime_state == "not_observed":
                basis = "lifetime_not_observed"
            elif (life or 0) > 0:
                basis = "still_producing"
            else:
                basis = "too_few_passes"
            if basis == "lifetime_not_observed":
                # ⚠️ NOT a zero. This leg is absent from a capture that lists
                # only pnl-BEARING closes, so its lifetime closes are UNKNOWN to
                # this pass — which is exactly the fact the old default erased.
                note = ("absent from the `/api/bot/performance` capture, which lists only "
                        "strategies with a pnl-BEARING close (`t.pnl IS NOT NULL`). That "
                        "means we did NOT OBSERVE a pnl-bearing close — it does NOT mean "
                        "the leg never closed a trade, and it is not grounds for a "
                        "retirement proposal. A leg whose every close landed `pnl NULL` "
                        "looks identical here to one that never traded. To settle it, "
                        "count closes directly in `trade_journal.db::trades`.")
            else:
                note = ("the gate cannot grade it yet, but the evidence does not support a "
                        "retirement proposal.")

        rows.append({
            "id": f"strategy:{name}",
            "class": "strategy_leg",
            "name": name,
            "verdict": verdict,
            "basis": basis,
            "note": note,
            "tier": 3,      # retiring a leg edits config/strategies.yaml
            "evidence": {
                "indices_seen": st["indices_seen"],
                "first_seen": st["first_seen"],
                "last_seen": st["last_seen"],
                "closes_in_latest_window": st["closes_latest"],
                "max_n_closed_ever_seen": st["max_n_closed_seen"],
                "gate_floor": st["floor"],
                "gate_reached_matrix_on": st["reached_matrix_on"],
                "lifetime_closed_trades": life,
                "lifetime_state": lifetime_state,
                # The PER-LEG state. `lifetime_state` describes the capture;
                # this describes whether THIS leg was in it. `None` closes
                # under `not_observed` means unknown, never zero.
                "leg_lifetime_state": leg_lifetime_state,
                "routed_to": routed,
            },
        })
    return rows


# ---------------------------------------------------------------------------
# the machinery half — IMPORTED, never re-implemented
# ---------------------------------------------------------------------------
def grade_machinery(repo: Path = REPO) -> Tuple[List[dict], dict]:
    """Consume `check_unwired_artifacts.scan`. Returns (rows, probe_meta).

    ⚠️ A TWO-SIDED POSITIVE CONTROL IS MANDATORY, AND ITS FAILURE REFUSES THE
    WHOLE HALF. *A search returning nothing is not proof of absence* — a
    negative needs a denominator and a demonstration that the probe can produce
    a positive. So before any silence here is believed:

      * the scan must return **at least one** finding (it can fire), and
      * a **known-WIRED** tool (`scripts/ci/run_guards.py`, invoked by
        `guards.yml`) must be **absent** from the findings (it does not fire
        indiscriminately).

    Either half failing grades the machinery population ``not_assessed`` rather
    than "the repo is clean", which is the exact inversion this repo has a rule
    against.

    ⚠️ WHAT IS CARRIED, AND WHY IT IS NOT EVERYTHING. `unwired` rows ride in
    full — that is the actionable class. `doc_only` and `skill_invoked` ride as
    a COUNT plus their names: they are the denominator (dropping them would let
    a shrinking headline hide a growing list) but 100+ full rows re-committed on
    every cadence is churn, not a record. Same trade the strategy-review
    workflow makes with `INDEX.json` always and full packets only on an action.
    """
    meta: Dict[str, Any] = {"probe_state": "not_assessed", "scanned": 0,
                            "targets": 0, "control": "not_run", "reason": ""}
    try:
        sys.path.insert(0, str(repo / "scripts" / "ci"))
        import check_unwired_artifacts as cua  # type: ignore
        targets = [f for f in (repo / "scripts").rglob("*.py")
                   if "__pycache__" not in f.parts and f.name != "__init__.py"]
        findings = cua.scan(repo, targets)
    except Exception as exc:  # noqa: BLE001 -- we could not look
        meta["reason"] = f"probe unavailable: {type(exc).__name__}: {exc}"
        return [], meta

    meta["targets"] = len(targets)
    flagged = {rel for rel, _v, _w in findings}
    control_wired = "scripts/ci/run_guards.py"
    fires = bool(findings)
    discriminates = control_wired not in flagged
    if not (fires and discriminates):
        meta["control"] = "failed"
        meta["reason"] = (
            f"positive control FAILED (fires={fires}, discriminates={discriminates}) "
            f"over {len(targets)} targets — the machinery population is graded "
            f"`not_assessed`, NOT clean.")
        return [], meta

    meta["control"] = "passed"
    meta["probe_state"] = "measured"
    meta["scanned"] = len(findings)

    rows: List[dict] = []
    summarised: Dict[str, List[str]] = {}
    for rel, verdict, why in sorted(findings):
        if verdict == cua.V_UNWIRED:
            rows.append({
                "id": f"tool:{rel}", "class": "ops_tool", "name": rel,
                "verdict": "unwired", "basis": "check_unwired_artifacts",
                "note": why, "tier": 1,
                "evidence": {"reported_by": "scripts/ci/check_unwired_artifacts.py"},
            })
        else:
            summarised.setdefault(verdict, []).append(rel)
    meta["summarised"] = {k: {"count": len(v), "names": v} for k, v in summarised.items()}
    return rows, meta


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------
def build(*, lifetime_capture: Optional[Any] = None, repo: Path = REPO,
          today: Optional[date] = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    indices, bad_dates = read_review_indices(repo / "comms" / "strategy_reviews")
    lstate, life = lifetime_closes(lifetime_capture)
    routing = account_routing(repo / "config" / "accounts.yaml")
    strat = grade_strategies(indices, lifetime_state=lstate, lifetime=life, routing=routing)
    mach, probe = grade_machinery(repo)

    def _count(rows, key="verdict"):
        c: Dict[str, int] = {}
        for r in rows:
            c[r[key]] = c.get(r[key], 0) + 1
        return c

    candidates = [r for r in strat if r["verdict"] == "retire_candidate"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "utc_date": today.isoformat(),
        "generated_by": "scripts/ops/sunset_pass.py",
        # ⚠️ THE POPULATION, ALWAYS. A candidate count with no denominator is
        # the unstated-population error this repo has a top-level rule about.
        "population": {
            "packet_dates_read": [d for d, _ in indices],
            "packet_dates_unreadable": bad_dates,
            "strategy_legs_graded": len(strat),
            "lifetime_state": lstate,
            "lifetime_strategies_in_capture": len(life),
            # ⚠️ THE DENOMINATOR FOR THE ABSENCE. These legs were graded with
            # NO lifetime measurement — they are not zeros. A reader who cannot
            # see this count cannot tell how much of the pass rests on silence.
            "legs_lifetime_not_observed": sum(
                1 for r in strat
                if r["evidence"].get("leg_lifetime_state") == "not_observed"),
            "routing_state": "read" if routing is not None else "unreadable",
            "machinery_probe": probe,
            "min_passes_for_index_basis": MIN_PASSES_FOR_INDEX_BASIS,
        },
        "strategy_verdicts": _count(strat),
        "machinery_verdicts": _count(mach),
        "retire_candidates": len(candidates),
        "rows": strat + mach,
    }


def render_markdown(doc: dict) -> str:
    p = doc["population"]
    L = [f"# Sunset pass — {doc['utc_date']}", "",
         f"Generated `{doc['generated_at']}` by `{doc['generated_by']}`.", "",
         "**E3 proposes. It never enacts.** Retiring a strategy leg is Tier-3; nothing "
         "here writes config or deletes a file.", "",
         "## Population", "",
         f"- packet dates read: **{len(p['packet_dates_read'])}** "
         f"({', '.join(p['packet_dates_read']) or 'none'})",
         f"- strategy legs graded: **{p['strategy_legs_graded']}**",
         f"- lifetime read: **`{p['lifetime_state']}`** "
         f"({p['lifetime_strategies_in_capture']} strategies in the capture)",
         f"- legs absent from that capture (**`not_observed`, NOT zero**): "
         f"**{p.get('legs_lifetime_not_observed', 0)}** — the capture lists only "
         f"pnl-bearing closes, so these legs have no lifetime measurement and are "
         f"never proposed on it",
         f"- account routing: **`{p['routing_state']}`**",
         f"- machinery probe: **`{p['machinery_probe']['probe_state']}`** "
         f"({p['machinery_probe']['scanned']} findings consumed)"]
    if p["machinery_probe"].get("reason"):
        L.append(f"  - {p['machinery_probe']['reason']}")
    if p["packet_dates_unreadable"]:
        L.append(f"- ⚠️ unreadable packet dates: {', '.join(p['packet_dates_unreadable'])}")
    L += ["", f"## Strategy verdicts — {doc['strategy_verdicts']}", "",
          f"## Machinery verdicts — {doc['machinery_verdicts']}", ""]
    summ = p["machinery_probe"].get("summarised") or {}
    for k, v in sorted(summ.items()):
        L.append(f"- `{k}` — **{v['count']}** (names carried in `INDEX.json`; the "
                 f"denominator, not an action list)")
    L += ["", f"## Retirement candidates — {doc['retire_candidates']}", ""]
    cands = [r for r in doc["rows"] if r["verdict"] == "retire_candidate"]
    if not cands:
        L.append("_None. This is a graded result over the population above, not an "
                 "empty scan._")
    for r in cands:
        e = r["evidence"]
        L.append(f"- **`{r['name']}`** (Tier-{r['tier']}, basis `{r['basis']}`) — {r['note']}")
        L.append(f"  - lifetime closes `{e.get('lifetime_closed_trades')}` "
                 f"(capture `{e.get('lifetime_state')}`, this leg "
                 f"`{e.get('leg_lifetime_state')}`) · latest-window closes "
                 f"`{e.get('closes_in_latest_window')}` · best ever seen "
                 f"`{e.get('max_n_closed_ever_seen')}` against gate floor "
                 f"`{e.get('gate_floor')}` · routed to "
                 f"`{[a for a, _ in (e.get('routed_to') or [])] or 'NOTHING'}`")
    L.append("")
    return "\n".join(L)


def render_brief_lines(doc: Optional[dict]) -> List[str]:
    """The `CLAUDE.md` session-brief lines. Mirrors `constraint_readout`'s contract.

    ⚠️ AN ABSENT PASS IS RENDERED, NOT SKIPPED — *nobody has run one* and *the
    renderer broke* must not look identical.
    """
    if not isinstance(doc, dict) or not doc.get("population"):
        return ["**No sunset pass has been recorded.** (E3 — generated into "
                "`comms/sunset/`; this line means none was found or it was unreadable, "
                "NOT that nothing is a retirement candidate.)", ""]
    p = doc["population"]
    n = doc.get("retire_candidates", 0)
    head = (f"**🗑️ SUNSET (E3, {doc.get('utc_date')}): {n} retirement candidate(s)** "
            f"over {p['strategy_legs_graded']} strategy legs "
            f"(lifetime read `{p['lifetime_state']}`, "
            f"{len(p['packet_dates_read'])} packet date(s)) · "
            f"machinery probe `{p['machinery_probe']['probe_state']}`, "
            f"{p['machinery_probe']['scanned']} findings carried.")
    out = [head]
    if n:
        names = [r["name"] for r in doc.get("rows", []) if r["verdict"] == "retire_candidate"]
        out.append(f"- Candidates: {', '.join(f'`{x}`' for x in names[:12])}"
                   f"{' …' if len(names) > 12 else ''}. Retiring a leg is **Tier-3** — "
                   f"propose, never enact. Disposition them in "
                   f"`docs/claude/SUNSET-DISPOSITIONS.json`.")
    out.append("")
    return out


def write(doc: dict, repo: Path = REPO) -> List[Path]:
    d = repo / "comms" / "sunset" / doc["utc_date"]
    d.mkdir(parents=True, exist_ok=True)
    a, b = d / "INDEX.json", d / "SUNSET.md"
    a.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    b.write_text(render_markdown(doc), encoding="utf-8")
    return [a, b]


def latest(repo: Path = REPO) -> Optional[dict]:
    root = repo / "comms" / "sunset"
    if not root.is_dir():
        return None
    for day in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        doc = _load_json(day / "INDEX.json")
        if isinstance(doc, dict):
            return doc
    return None


# ---------------------------------------------------------------------------
def _self_test() -> int:
    """The failure paths, exercised. A grader whose branches never fire is
    indistinguishable from one that always says `watch`."""
    ok = 0
    checks: List[Tuple[str, bool]] = []

    idx = [("2026-09-01", {"rows": [
        {"strategy": "silent", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]},
        {"strategy": "young", "n_closed": 3,
         "reasons": ["insufficient evidence (n_closed=3 < 20) — no KILL/DEMOTE fires."]},
        {"strategy": "graded", "n_closed": 44,
         "reasons": ["win rate 0.10 over 44 closes; expectancy negative."]},
        {"strategy": "propleg", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]},
        {"strategy": "unrouted", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]},
    ]})]
    routing = {"silent": [("bybit_1", "paper")], "young": [("bybit_1", "paper")],
               "graded": [("bybit_1", "paper")], "propleg": [("breakout_1", "prop")]}

    # `silent` is PRESENT in the capture reading 0 — the genuinely-measured
    # zero. `absent` (below) is the leg that is missing from it entirely, which
    # is a different fact and must NOT grade the same.
    r = {x["name"]: x for x in grade_strategies(
        idx, lifetime_state="read", lifetime={"silent": 0, "young": 3, "graded": 44},
        routing=routing)}
    checks.append(("a leg the gate GRADED is governed_elsewhere, not re-graded here",
                   r["graded"]["verdict"] == "governed_elsewhere"))
    checks.append(("a leg MEASURED at zero lifetime closes is a retire_candidate",
                   r["silent"]["verdict"] == "retire_candidate"
                   and r["silent"]["basis"] == "never_closed_lifetime"
                   and r["silent"]["evidence"]["leg_lifetime_state"] == "observed"))
    checks.append(("a PROP-routed silent leg is not_assessed, NEVER a candidate",
                   r["propleg"]["verdict"] == "not_assessed"
                   and r["propleg"]["basis"] == "prop_routed"))
    checks.append(("a leg routed to NO account is a candidate on that basis alone",
                   r["unrouted"]["verdict"] == "retire_candidate"
                   and r["unrouted"]["basis"] == "unrouted"))
    checks.append(("a young leg that IS closing trades only reaches `watch`",
                   r["young"]["verdict"] == "watch"))

    # ⚠️ THE ABSENCE. `silent` is routed and gate-short-circuited exactly like
    # the measured-zero case above, but is NOT in the capture. Under the old
    # `lifetime.get(name, 0)` default it read a manufactured 0 and graded
    # `retire_candidate` / `never_closed_lifetime`. It must not.
    r_abs = {x["name"]: x for x in grade_strategies(
        idx, lifetime_state="read", lifetime={"young": 3, "graded": 44}, routing=routing)}
    checks.append(("an ABSENT leg is never a retire_candidate on that absence",
                   r_abs["silent"]["verdict"] != "retire_candidate"))
    checks.append(("...it carries `not_observed` as its own state, not a zero",
                   r_abs["silent"]["evidence"]["leg_lifetime_state"] == "not_observed"
                   and r_abs["silent"]["evidence"]["lifetime_closed_trades"] is None
                   and r_abs["silent"]["basis"] == "lifetime_not_observed"))

    # the lifetime read is three-state and `not_read` must not manufacture candidates
    r2 = {x["name"]: x for x in grade_strategies(
        idx, lifetime_state="not_read", lifetime={}, routing=routing)}
    checks.append(("`not_read` lifetime proposes NOTHING on lifetime evidence",
                   r2["silent"]["verdict"] == "watch"
                   and r2["silent"]["basis"] == "lifetime_not_read"))

    # Unreadable routing must never MANUFACTURE a candidate. A leg the gate
    # already graded on merit stays `governed_elsewhere` — routing is irrelevant
    # once the gate could judge it — so the invariant is about candidates, not
    # about every row.
    r3 = grade_strategies(idx, lifetime_state="read", lifetime={}, routing=None)
    checks.append(("unreadable routing manufactures ZERO retire_candidates",
                   not any(x["verdict"] == "retire_candidate" for x in r3)))
    checks.append(("...and the ungradeable ones say `routing_unreadable`, not `fine`",
                   all(x["basis"] == "routing_unreadable"
                       for x in r3 if x["verdict"] == "not_assessed")
                   and sum(1 for x in r3 if x["verdict"] == "not_assessed") == 4))

    checks.append(("the gate's floor is READ from its own words, never restated",
                   _floor_from_reason("insufficient evidence (n_closed=3 < 20) —") == 20
                   and _floor_from_reason("no closed trades in window") is None))

    checks.append(("an absent pass RENDERS a line rather than going quiet",
                   "No sunset pass has been recorded" in render_brief_lines(None)[0]))

    for name, good in checks:
        print(f"  {'ok  ' if good else 'FAIL'}  {name}")
        ok += bool(good)
    print(f"self-test: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--lifetime-json", type=Path,
                    help="a /api/bot/performance?window=all capture. Without it the "
                         "lifetime read is `not_read` and NO leg is proposed on "
                         "lifetime evidence — deliberately.")
    ap.add_argument("--write", action="store_true", help="write comms/sunset/<date>/")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    cap = None
    if a.lifetime_json:
        cap = _load_json(a.lifetime_json)
        if cap is None:
            print(f"warn: {a.lifetime_json} unreadable — lifetime_state will be "
                  f"`unreadable`, which is NOT `no closed trades`.", file=sys.stderr)
            cap = {}
    doc = build(lifetime_capture=cap)
    if a.write:
        for p in write(doc):
            print(f"wrote {p.relative_to(REPO)}")
    else:
        print(render_markdown(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
