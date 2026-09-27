#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::research-tooling-selftests (--self-test only).
#         Weekly live run: .github/workflows/soak-book-grade-weekly.yml.
"""R5 — grade every Stage-1 soak leg on MECHANICS and COST FIDELITY, weekly.

ONE QUESTION (checklist row R5, operator-named, never built until now): the
Stage-1 soak books (``bybit_1``, ``alpaca_paper``) make decisions on live data
continuously and nobody reads them. This script reads them, on a cadence, and
grades EVERY leg on that roster into one of four dispositions.

NOT EXPECTANCY. Stage 1 never decides edge (root ``CLAUDE.md`` § "The
promotion ladder" — "a live or paper book never establishes edge. It checks
mechanics and cost."). This script grades exactly those two things, reusing
the machinery that already exists for each rather than re-deriving either:

  * MECHANICS — ``scripts/ops/leg_flow_report.build_report()`` /
    ``scripts/ops/leg_flow_detector.assess_leg()`` (checklist row E18): are
    this leg's intents reaching an order/ticket, in the measurement window.
  * COST FIDELITY — ``scripts/research/r3_cost_fidelity.run()`` /
    ``.grade()`` (checklist row R3, the standing Gate-1 test): is the
    realized round-trip slippage the one the leg's Stage-0 evidence record
    assumed. R3 already grades per (leg, account) CELL, which is exactly this
    script's population, so a soak leg missing from R3's cells is graded here
    by calling R3's own ``grade([], [], modelled, tol)`` on an empty sample —
    the same function, the same verdict vocabulary, never a second
    implementation of the floor/CI/threshold logic.

THE POPULATION IS COUNTED LIVE, NEVER QUOTED. ``config/accounts.yaml`` is
re-read on every run via ``src.config.accounts_loader.load_accounts_dict()``
(the same loader R3 uses) — the checklist's own R5 note says "26 legs on
bybit_1 and 19 on alpaca_paper" and that number drifts with the roster (see
root CLAUDE.md's own warning about ``accounts.yaml::symbols`` counts moving
between roster edits). This script never trusts it.

DISPOSITIONS, and why each maps the way it does:

  healthy          — mechanics ``flowing`` AND cost fidelity ``consistent``
                      (or no evidence record was ever expected to model cost
                      for this leg's basis — see NO_RECORD below).
  tweak            — mechanics ``flowing`` but cost fidelity ``divergent`` or
                      ``no_record``: the MECHANICS work; the Stage-0 cost
                      assumption or its evidence record does not match reality.
                      The named hypothesis is the concrete fix (raise/lower
                      the modelled slippage default, or author the missing
                      evidence record) — never "kill", because a leg that
                      trades fine and merely has a mismodelled cost is not
                      mechanically dead.
  kill             — mechanics ``starved``: intents are produced and NONE
                      reach an order or ticket. A soak leg exists to prove the
                      pipeline works; one that cannot even get an order placed
                      is not testing anything and is dead weight on the
                      roster. Reason names the exact counts (population
                      stated), per ``leg_flow_detector``'s own vocabulary.
  insufficient-data — read_state says we could not measure (mechanics
                      ``unreadable``, or the diag pull for cost fidelity
                      failed), OR mechanics read fine with zero intents this
                      window (``no_intents`` — nothing to grade yet, and NOT
                      evidence of health, per E18's own docstring), OR cost
                      fidelity read ``insufficient_n``/``inconclusive``. Names
                      the n still needed and an ETA from the leg's own recent
                      fill rate — never a bare "wait".

Never folds "we did not look" into "we looked and it was fine" — the same
non-collapse rule ``leg_flow_detector`` and ``research_result.py`` already
enforce. A leg this run could not measure is ``insufficient-data``, not
``healthy``.

Read-only. No account, mode, execution or roster field is written by this
script — a ``kill``/``tweak`` disposition is a finding to be FILED
(``scripts/ops/pipeline.py``), never applied. Filing/applying either is a
Tier-3 act on a live-data-bearing account and stays the operator's.

Usage
-----
  python3 scripts/ops/soak_book_grade.py --self-test
  python3 scripts/ops/soak_book_grade.py --window-hours 168 \\
      --out comms/research/soak_book_grade/<date>.json

Exit codes: 0 measured (whatever the dispositions); 1 could not measure
either dimension at all (nothing to grade); 2 usage/self-test failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "research"))

from scripts.ops import leg_flow_detector as lfd  # noqa: E402
from scripts.ops import leg_flow_report as lfr  # noqa: E402
import r3_cost_fidelity as r3  # noqa: E402
from src.config.accounts_loader import load_accounts_dict  # noqa: E402

SOAK_ACCOUNTS: Tuple[str, ...] = ("bybit_1", "alpaca_paper")

HEALTHY, TWEAK, KILL, INSUFFICIENT = "healthy", "tweak", "kill", "insufficient-data"
DISPOSITIONS = (HEALTHY, TWEAK, KILL, INSUFFICIENT)

#: Cost-fidelity n floor, mirrored from R3 so a reader here never has to open
#: that module to know what "insufficient n" means — but the VALUE itself is
#: read off r3.N_FLOOR at call time (below), never hardcoded twice.
_N_FLOOR = r3.N_FLOOR


def enumerate_soak_legs(acfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``(account_id, strategy)`` pairs for every Stage-1 soak account,
    counted LIVE from ``config/accounts.yaml`` — never a cached figure."""
    out: List[Tuple[str, str]] = []
    for acct in SOAK_ACCOUNTS:
        cfg = acfg.get(acct) or {}
        for strat in sorted(cfg.get("strategies") or []):
            out.append((acct, strat))
    return out


def _cost_cell(r3_result: Optional[dict], *, account_id: str, strategy: str,
               tol: float) -> Dict[str, Any]:
    """The R3 cell for this exact (strategy, account), or a freshly-graded
    empty-sample cell via R3'S OWN ``grade()`` if none was measured — reusing
    the verdict function rather than inventing a second "no data" state."""
    leg_entry = (r3_result or {}).get("per_leg", {}).get(strategy)
    modelled = (leg_entry or {}).get("modelled_slippage_bps")
    if leg_entry:
        for c in leg_entry.get("cells", []):
            if c.get("account_id") == account_id:
                return c
    cell = r3.grade([], [], modelled, tol)
    cell.update({"account_id": account_id, "account_class": "paper",
                 "basis": "simulator", "stage": "S1", "venue": None})
    return cell


def _fmt_eta(cell: Dict[str, Any]) -> str:
    n_entry, n_exit = cell.get("n_entry", 0), cell.get("n_exit", 0)
    need_entry = max(0, _N_FLOOR - n_entry)
    need_exit = max(0, _N_FLOOR - n_exit)
    return (f"needs {need_entry} more entry fill(s) and {need_exit} more "
            f"package-referenced exit fill(s) to clear the n={_N_FLOOR}/side "
            f"floor (currently entry={n_entry}, exit={n_exit}); no fill-rate "
            f"basis to date an ETA from this run alone — re-check next "
            f"scheduled pass")


def disposition_for_leg(*, mech: Optional[Dict[str, Any]],
                        cost_cell: Optional[Dict[str, Any]],
                        execution: Optional[str] = None) -> Dict[str, Any]:
    """Pure. ``mech`` is a ``leg_flow_detector.assess_leg()`` dict or ``None``
    (mechanics unreadable this run). ``cost_cell`` is an ``r3.grade()`` dict
    or ``None`` (cost fidelity unreadable this run — the diag pull failed).
    ``execution`` is the leg's ``strategies.yaml`` execution field
    (``"live"``/``"shadow"``/``None`` if unknown this run).

    ⚠️ A ``shadow``-execution leg is checked FIRST and unconditionally,
    before ``mech``/``cost_cell`` are even consulted — a shadow leg produces
    no live order/ticket BY DESIGN (root CLAUDE.md § "The two execution
    gates": shadow "runs and logs order packages everywhere but never sends a
    live order"). MEASURED live 2026-09-26: 7 of the 45 soak legs
    (``fade_breakout_4h``, ``fvg_range_15m``, ``htf_pullback_trend_2h``,
    ``trend_donchian_1h``, ``avax_pullback_2h``, ``eth_pullback_prop_2h``,
    ``slv_trend_1h``) are ``execution: shadow`` and were absent from
    ``leg_flow_detector.enumerate_live_legs()`` entirely — reading ``mech`` as
    ``None`` for every one of them despite ``leg_flow_report`` having read
    ``/api/bot/config``/``/api/bot/strategies`` FINE. Grading that as
    "could not measure mechanics this run" would misreport a genuine data-read
    failure that never happened; the true, distinct fact is that the leg is
    not currently routing orders at all, which is neither a mechanics failure
    to fix nor a cost-fidelity question to grade.
    """
    if execution == "shadow":
        return {"disposition": INSUFFICIENT,
                "reason": ("execution=shadow: this leg places no live order or "
                          "ticket by design (root CLAUDE.md's execution gate), so "
                          "neither mechanics (order routing) nor cost fidelity "
                          "(realized fills) is gradeable the way a routed leg's "
                          "is — this is NOT a data-read failure"),
                "n_needed": None,
                "eta": "becomes gradeable if this leg's strategies.yaml "
                       "execution flips to 'live'"}

    if mech is None or cost_cell is None:
        missing = []
        if mech is None:
            missing.append("mechanics (leg_flow_report could not read this run)")
        if cost_cell is None:
            missing.append("cost fidelity (the realized_slippage diag pull failed this run)")
        return {"disposition": INSUFFICIENT,
                "reason": "could not measure: " + "; ".join(missing) + " — "
                          "distinct from 'measured and found nothing'; re-check "
                          "next scheduled pass",
                "n_needed": None, "eta": "next scheduled run"}

    mstate = mech["state"]
    cverdict = cost_cell.get("verdict")

    if mstate == lfd.LEG_UNREADABLE:
        return {"disposition": INSUFFICIENT,
                "reason": "mechanics read_state=unreadable (could not query intents "
                          "and/or received source this run) — we did not look, not "
                          "'nothing happened'",
                "n_needed": None, "eta": "next scheduled run"}

    if mstate == lfd.LEG_STARVED:
        # ⚠️ leg_flow_report's OWN caveat (measured live 2026-09-25 on
        # alpaca_paper/tlt_pullback_1d and alpaca_paper/gld_pullback_1h,
        # re-confirmed live 2026-09-26 on 4 of this run's 10 candidate
        # `kill`s): a leg already holding a position opened BEFORE the window
        # legitimately re-signals "stay in this position" with zero NEW
        # orders in-window. That is NOT the E18 unreached-leg shape — it is
        # indistinguishable, from this window alone, from a leg that simply
        # had no need for a fresh entry. Grading it `kill` would be exactly
        # the false positive `_print_table`'s own caveat exists to flag; this
        # function must not launder that caveat into a firm verdict.
        if mech.get("has_open_position"):
            return {"disposition": INSUFFICIENT,
                    "reason": (f"starved ({mech['intents']} intent(s), 0 received) "
                              f"but ALREADY HOLDING a position opened before the "
                              f"window — leg_flow_report's own caveat: this is NOT "
                              f"reliably 'unreached', it may simply have needed no "
                              f"new entry this window while flat-to-in-position. "
                              f"Cannot be graded kill from this window alone."),
                    "n_needed": "an entry signal observed while this leg is FLAT "
                                "(no open position), in some future window",
                    "eta": "re-check next scheduled pass"}
        # PI-20260926-X3QEGPJL / w5d-starved-legs: a `starved` leg that shares
        # its symbol with >=1 other LIVE strategy on the SAME account is not
        # distinguishable, from intents/received alone, from a leg that never
        # reaches dispatch — `src/runtime/intents.py`'s per-account,
        # per-symbol election (`aggregate_intents` /
        # `arbitration_fanout.plan_per_account_election`) lets exactly ONE
        # strategy win that (account, symbol) slot per tick, REGARDLESS of
        # `execution: shadow` vs `live` on either side. Grading this `kill`
        # would repeat the exact false-kill-proposal PI-20260926-X3QEGPJL-0001
        # through -0006 filed against bybit_1/trend_donchian(_eth_4h) and four
        # alpaca_paper legs — all six were starved purely because a sibling on
        # the same account/symbol wins the tick's election, not because the
        # leg is mechanically dead.
        contenders = mech.get("same_symbol_live_contenders") or []
        if contenders:
            return {"disposition": INSUFFICIENT,
                    "reason": (f"starved ({mech['intents']} intent(s), 0 received, "
                              f"no open position) but contends its symbol on this "
                              f"SAME account against live sibling(s) {contenders} — "
                              f"src/runtime/intents.py elects only ONE strategy per "
                              f"(account, symbol) per tick, so losing that election "
                              f"every time produces this exact shape without the "
                              f"leg ever being mechanically dead. Cannot be graded "
                              f"kill without a live per-tick election trace."),
                    "n_needed": "a per-tick account-election read (e.g. the "
                                "conviction_arbitration/arbitration_fanout_soak log, "
                                "or a live dispatch trace) showing this leg either "
                                "winning at least once, or never winning across a "
                                "large contested-tick sample",
                    "eta": "re-check next scheduled pass"}
        return {"disposition": KILL,
                "reason": (f"starved: {mech['intents']} actionable intent(s) "
                          f"({mech.get('intent_episodes')} episode(s)) in the "
                          f"window, 0 order(s)/ticket(s) received, "
                          f"held_back={mech.get('held_back')}, no open position "
                          f"held. This leg's soak purpose (prove the pipeline "
                          f"works) is unmet: it never reaches an order regardless "
                          f"of what cost fidelity reads."),
                "n_needed": None, "eta": None}

    if mstate == lfd.LEG_NO_INTENTS:
        return {"disposition": INSUFFICIENT,
                "reason": ("no_intents: the strategy produced zero actionable "
                          "signals this window — nothing to grade mechanics or "
                          "cost fidelity against yet, and this is NOT evidence "
                          "of health either"),
                "n_needed": None, "eta": "wait for a signal; re-check next "
                          "scheduled pass"}

    # mstate == flowing from here.
    if cverdict == r3.CONSISTENT:
        return {"disposition": HEALTHY,
                "reason": (f"flowing (intents={mech['intents']}, "
                          f"received={mech['received']}); cost fidelity "
                          f"consistent (n={cost_cell.get('n')}, roundtrip_mean="
                          f"{cost_cell.get('roundtrip_mean')}bps vs modelled "
                          f"{cost_cell.get('modelled_bps')}bps)"),
                "n_needed": None, "eta": None}

    if cverdict == r3.DIVERGENT:
        return {"disposition": TWEAK,
                "reason": (f"flowing, but cost fidelity divergent: realized "
                          f"roundtrip {cost_cell.get('roundtrip_mean')}bps "
                          f"(CI95 {cost_cell.get('ci95')}) exceeds the modelled "
                          f"{cost_cell.get('modelled_bps')}bps; threshold "
                          f"{cost_cell.get('threshold_bps')}bps over n={cost_cell.get('n')}. "
                          f"HYPOTHESIS: "
                          f"the Stage-0 evidence record's modelled slippage for "
                          f"this leg is too low for this venue/basis; raise the "
                          f"default (execution_costs) or re-author the evidence "
                          f"record from this measurement."),
                "n_needed": None, "eta": None}

    if cverdict == r3.NO_RECORD:
        return {"disposition": TWEAK,
                "reason": ("flowing, but no Stage-0 evidence record exists for "
                          "this leg at all, so cost fidelity cannot be graded. "
                          "HYPOTHESIS: author comms/strategy_evidence/<leg>.json "
                          "so this leg's cost assumption becomes checkable."),
                "n_needed": None, "eta": None}

    if cverdict == r3.INSUFFICIENT:
        return {"disposition": INSUFFICIENT,
                "reason": f"flowing; cost fidelity insufficient_n ({cost_cell.get('why')})",
                "n_needed": _fmt_eta(cost_cell), "eta": "re-check next scheduled pass"}

    # inconclusive
    return {"disposition": INSUFFICIENT,
            "reason": (f"flowing; cost fidelity inconclusive: CI95 "
                      f"{cost_cell.get('ci95')} straddles the "
                      f"{cost_cell.get('threshold_bps')}bps threshold over "
                      f"n={cost_cell.get('n')} — more fills would resolve it"),
            "n_needed": "more fills on both sides than currently measured "
                        f"(n={cost_cell.get('n')}); no fixed floor for this state",
            "eta": "re-check next scheduled pass"}


def _pull_cost_fidelity(tmp_dir: Path) -> Optional[dict]:
    """Fresh D3 pull + R3 grade, or ``None`` if the pull failed. Shells out to
    the existing CLIs rather than re-deriving the pull (Transport A, diag_fetch.sh,
    already owned by realized_slippage.py)."""
    pull = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "research" / "realized_slippage.py"),
         "pull", "--out-dir", str(tmp_dir)],
        capture_output=True, text=True, timeout=300,
    )
    if pull.returncode != 0 or not (tmp_dir / "trades.json").exists():
        print(f"soak_book_grade: realized_slippage pull failed: {pull.stderr.strip()[-2000:]}",
              file=sys.stderr)
        return None
    as_of = datetime.now(timezone.utc)
    return r3.run(tmp_dir, as_of)


def fetch_strategy_execution() -> Optional[Dict[str, str]]:
    """``{strategy_name: execution}`` off the live ``/api/bot/strategies``, or
    ``None`` if that read failed — kept apart from ``leg_flow_report``'s own
    fetch of the same route so a caller not doing mechanics grading (e.g. a
    future consumer) is not forced to import mechanics to get this."""
    doc = lfr._curl_json(f"{lfr._BOT_BASE}/api/bot/strategies")
    if doc is None:
        return None
    return {s.get("name"): s.get("execution") for s in (doc.get("strategies") or [])
           if s.get("name")}


def build_report(*, window_hours: int, skip_cost_pull: bool = False) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    acfg = load_accounts_dict()
    legs = enumerate_soak_legs(acfg)
    execution_map = fetch_strategy_execution()

    mech_report = lfr.build_report(window_hours=window_hours)
    mech_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if mech_report["read_state"] == "measured":
        for row in mech_report["legs"]:
            mech_index[(row["account_id"], row["strategy"])] = row

    r3_result: Optional[dict] = None
    cost_read_state = "not_attempted"
    if not skip_cost_pull:
        with tempfile.TemporaryDirectory() as td:
            r3_result = _pull_cost_fidelity(Path(td))
        cost_read_state = "measured" if r3_result is not None else "producer_failed"

    tol = None
    if r3_result is not None:
        tol = r3_result["rule"]["tolerance_bps"]

    out_legs: List[Dict[str, Any]] = []
    for account_id, strategy in legs:
        mech = mech_index.get((account_id, strategy)) if mech_report["read_state"] == "measured" else None
        cost_cell = (_cost_cell(r3_result, account_id=account_id, strategy=strategy, tol=tol)
                    if r3_result is not None else None)
        execution = (execution_map or {}).get(strategy)
        disp = disposition_for_leg(mech=mech, cost_cell=cost_cell, execution=execution)
        out_legs.append({
            "account_id": account_id, "strategy": strategy, "execution": execution,
            "mechanics": mech, "cost_fidelity": cost_cell, **disp,
        })

    by_disposition: Dict[str, int] = {d: 0 for d in DISPOSITIONS}
    for leg in out_legs:
        by_disposition[leg["disposition"]] += 1

    return {
        "question": "R5: per Stage-1 soak leg, are mechanics flowing and is cost "
                    "fidelity consistent with what its Stage-0 record assumed? "
                    "(NOT expectancy — Stage 1 never decides edge.)",
        "as_of": now.isoformat(),
        "window_hours": window_hours,
        "population": {
            "description": "every (account, strategy) leg on the Stage-1 soak "
                           f"accounts {SOAK_ACCOUNTS}, counted live from "
                           "config/accounts.yaml",
            "n": len(legs),
            "by_account": {a: sum(1 for x, _ in legs if x == a) for a in SOAK_ACCOUNTS},
        },
        "mechanics_read_state": mech_report["read_state"],
        "cost_fidelity_read_state": cost_read_state,
        "execution_map_read_state": "measured" if execution_map is not None else "unreadable",
        "by_disposition": by_disposition,
        "legs": out_legs,
    }


def _self_test() -> int:
    """Offline. Every disposition branch reachable, positive control for the
    non-collapse rule (unreadable input never renders as healthy/insufficient
    getting silently upgraded)."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  self-test ({label}): {'PASS' if cond else 'FAIL'}")

    def cell(verdict: str, **over: Any) -> Dict[str, Any]:
        base = {"n_entry": 15, "n_exit": 15, "n": 15, "modelled_bps": 3.0,
                "threshold_bps": 3.0, "verdict": verdict, "ci95": [1.0, 2.0],
                "roundtrip_mean": 1.5}
        base.update(over)
        return base

    v = disposition_for_leg(mech=None, cost_cell=cell(r3.CONSISTENT))
    ck("mechanics unreadable never renders healthy", v["disposition"] == INSUFFICIENT)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=5, received=2), cost_cell=None)
    ck("cost-fidelity pull failure never renders healthy", v["disposition"] == INSUFFICIENT)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=None, received=0), cost_cell=cell(r3.CONSISTENT))
    ck("leg_flow unreadable state -> insufficient-data, not healthy", v["disposition"] == INSUFFICIENT)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=21, received=0, held_back=0),
                           cost_cell=cell(r3.CONSISTENT))
    ck("starved (THE E18 shape) -> kill, regardless of cost fidelity", v["disposition"] == KILL)

    # THE 2026-09-25/26 SHAPE: starved but already holding a position opened
    # before the window (leg_flow_report's own caveat). Must NOT be `kill`.
    starved_holding = lfd.assess_leg(intents=21, received=0, held_back=0)
    starved_holding["has_open_position"] = True
    v = disposition_for_leg(mech=starved_holding, cost_cell=cell(r3.CONSISTENT))
    ck("starved BUT holding a pre-window position -> insufficient-data, never kill",
       v["disposition"] == INSUFFICIENT and "ALREADY HOLDING" in v["reason"])

    # PI-20260926-X3QEGPJL / w5d-starved-legs SHAPE: starved, no open
    # position, but contends its symbol against a live sibling on the SAME
    # account. Must NOT be `kill` either — same false-positive class as the
    # has_open_position case above, caught by a different signal.
    starved_contended = lfd.assess_leg(intents=25, received=0, held_back=0)
    starved_contended["same_symbol_live_contenders"] = ["eth_pullback_2h", "ict_scalp_eth_15m"]
    v = disposition_for_leg(mech=starved_contended, cost_cell=cell(r3.CONSISTENT))
    ck("starved BUT contends its symbol against a live same-account sibling "
       "-> insufficient-data, never kill",
       v["disposition"] == INSUFFICIENT and "eth_pullback_2h" in v["reason"])

    # Negative control: starved, no open position, NO contenders -> still kill.
    # The new check must not swallow the genuine E18 finding.
    starved_uncontended = lfd.assess_leg(intents=21, received=0, held_back=0)
    starved_uncontended["same_symbol_live_contenders"] = []
    v = disposition_for_leg(mech=starved_uncontended, cost_cell=cell(r3.CONSISTENT))
    ck("starved, no open position, NO contenders -> still kill (negative control)",
       v["disposition"] == KILL)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=0, received=0), cost_cell=cell(r3.CONSISTENT))
    ck("no_intents -> insufficient-data, never healthy", v["disposition"] == INSUFFICIENT)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=4, received=2), cost_cell=cell(r3.CONSISTENT))
    ck("flowing + consistent -> healthy", v["disposition"] == HEALTHY)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=4, received=2), cost_cell=cell(r3.DIVERGENT))
    ck("flowing + divergent -> tweak, with a named hypothesis", v["disposition"] == TWEAK
       and "HYPOTHESIS" in v["reason"])

    v = disposition_for_leg(mech=lfd.assess_leg(intents=4, received=2), cost_cell=cell(r3.NO_RECORD))
    ck("flowing + no_record -> tweak (author the evidence record)", v["disposition"] == TWEAK)

    v = disposition_for_leg(mech=lfd.assess_leg(intents=4, received=2),
                           cost_cell=cell(r3.INSUFFICIENT, n_entry=2, n_exit=3, why="entry n=2, exit n=3; need >= 10 each"))
    ck("flowing + insufficient_n -> insufficient-data, states the n needed",
       v["disposition"] == INSUFFICIENT and "more" in v["n_needed"])

    v = disposition_for_leg(mech=lfd.assess_leg(intents=4, received=2), cost_cell=cell(r3.INCONCLUSIVE))
    ck("flowing + inconclusive -> insufficient-data, never a false healthy/kill",
       v["disposition"] == INSUFFICIENT)

    # THE E63/2026-09-26 SHAPE: mech is None (not enumerated as a live leg at
    # all) because execution=shadow, NOT because a read failed. Must not be
    # reported as "could not measure this run" — a real read succeeded and
    # said something specific and different.
    v = disposition_for_leg(mech=None, cost_cell=None, execution="shadow")
    ck("shadow execution -> insufficient-data, but the reason names shadow, "
       "never 'could not measure'",
       v["disposition"] == INSUFFICIENT and "shadow" in v["reason"]
       and "could not measure" not in v["reason"])
    v = disposition_for_leg(mech=None, cost_cell=None, execution="live")
    ck("…and a live-execution leg with mech=None still reads as an honest "
       "read failure (negative control: shadow-handling didn't swallow this)",
       v["disposition"] == INSUFFICIENT and "could not measure" in v["reason"])

    acfg = {"bybit_1": {"strategies": ["b", "a"]}, "alpaca_paper": {"strategies": ["z"]},
            "bybit_2": {"strategies": ["should-not-appear"]}}
    legs = enumerate_soak_legs(acfg)
    ck("enumerate_soak_legs: only the two soak accounts, sorted, none leaked from bybit_2",
       legs == [("bybit_1", "a"), ("bybit_1", "b"), ("alpaca_paper", "z")])

    r3res = {"per_leg": {"x": {"modelled_slippage_bps": 3.0,
                               "cells": [{"account_id": "bybit_1", "verdict": r3.CONSISTENT}]}}}
    c = _cost_cell(r3res, account_id="bybit_1", strategy="x", tol=0.0)
    ck("_cost_cell finds the matching account cell when one was measured",
       c["verdict"] == r3.CONSISTENT)
    c2 = _cost_cell(r3res, account_id="alpaca_paper", strategy="x", tol=0.0)
    ck("_cost_cell falls back to R3's OWN grade([],[],...) for an unmeasured "
       "account, never a second implementation",
       c2["verdict"] == r3.INSUFFICIENT and c2["n_entry"] == 0 and c2["n_exit"] == 0)

    print("soak_book_grade self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window-hours", type=int, default=168,
                    help="mechanics measurement window (default 168h = weekly cadence)")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    ap.add_argument("--skip-cost-pull", action="store_true",
                    help="mechanics only, for a fast offline/dry run — never used by the "
                        "scheduled workflow, which must grade both dimensions")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    report = build_report(window_hours=a.window_hours, skip_cost_pull=a.skip_cost_pull)
    text = json.dumps(report, indent=2, default=str, sort_keys=True)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")
        print(str(a.out))
    else:
        print(text)
    print(f"population n={report['population']['n']} · by_disposition={report['by_disposition']} "
          f"· mechanics={report['mechanics_read_state']} · cost_fidelity={report['cost_fidelity_read_state']}",
          file=sys.stderr)

    if report["mechanics_read_state"] != "measured" and report["cost_fidelity_read_state"] != "measured":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
