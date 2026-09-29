#!/usr/bin/env python3
"""B4 (checklist row, 2026-09-27) — the stage-aware strategy review packet.

WHAT WAS BROKEN. ``scripts/ml/strategy_review_packet.py --all-strategies`` (the
only caller: ``generate_strategy_review_packets_action.sh``, dispatched daily
by ``.github/workflows/strategy-review-packets.yml``) grades every strategy
uniformly against an expectancy-based threshold table with a 20-closed-trade
floor. Committed 2026-09-20: ``comms/strategy_reviews/2026-09-20/INDEX.json``
graded 52 legs, 71 total closes across ALL of them, actionable 0, its own
``days_to_grade_all_reachable_point`` = 140 (roughly half the fleet reaches the
floor only after another 140 days at the observed close rate). It will never
produce a decision, because roughly half the population is Stage-1 SOAK legs
(``bybit_1``, ``alpaca_paper``), and root ``CLAUDE.md``'s promotion ladder says
a soak book never establishes edge in the first place: "a live or paper book
never establishes edge. It checks mechanics and cost." Grading a soak leg on
expectancy is not a slow version of the right measurement — it is the wrong
measurement, and it reads "0 actionable" regardless of how long it runs.

THE FIX is not a better matrix. It is grading each leg with the instrument the
operating plan (``docs/plans/OPERATING-PLAN-2026-09-21.md``) names for its
STAGE, reusing each wholesale rather than re-deriving either:

  Stage 2 (``bybit_2`` / ``alpaca_live``, the real-money accounts, and their
    strict mirrors ``bybit_portfolio`` / ``alpaca_portfolio``) — MONEY. The
    verdict is ``scripts/ops/r4_demotion_gate.py``'s own ``evaluate()`` (which
    itself imports ``src.runtime.research_results_gate.combined_leg_verdict`` —
    never re-derived past that point either). Called here READ-ONLY: this
    module never calls ``r4_demotion_gate.run(..., apply=True)`` and never
    writes to ``config/accounts.yaml`` — that mutation is R4's own enforcing
    workflow (``.github/workflows/r4-demotion-gate.yml``, Tier-3, mandate
    gated). This script is Tier-1 review tooling: it reports R4's verdict, it
    does not act on it.
  Stage 1 (``bybit_1``, ``alpaca_paper``) — MECHANICS + COST FIDELITY, never
    expectancy. The verdict is ``scripts/ops/soak_book_grade.py``'s own
    ``build_report()`` (R5), unmodified.

WHY THIS LIVES HERE AND NOT IN ``scripts/ml/strategy_review_packet.py``.
``scripts/ci/check_pr_landing.py::TIER1_SURFACE`` — the allowlist a Tier-1
self-land is checked against — covers ``scripts/ops/**`` but NOT
``scripts/ml/**`` (verified by reading the guard directly: the same MI-242 gap
that once excluded ``scripts/backtest_*.py`` before an operator-approved
widening). Editing ``strategy_review_packet.py`` in place would make this
genuinely Tier-1 change (review tooling, no roster/config/order-path touched)
fail R5's "diff outside TIER1_SURFACE" check and be refused self-land — not a
reason to widen the allowlist unilaterally (that widening is the operator's
act, same asymmetry as granting a mandate), just a reason to put the new code
where the existing allowlist already says review tooling may land. The legacy
per-strategy expectancy matrix in ``strategy_review_packet.py`` is UNCHANGED
and keeps answering a different, still-valid question — "how did this ONE
named strategy do over this window" — for
``GET /api/bot/strategies/{name}/review``.

WHERE THIS FITS. Invoked by ``generate_strategy_review_packets_action.sh``
in place of ``python3 -m scripts.ml.strategy_review_packet --all-strategies``
for the cron/committed path. Writes ONE ``INDEX.json`` (schema
``stage_aware_v1``) under ``<out-dir>/<UTC-date>/`` — the same directory the
workflow already tars back and commits to ``comms/strategy_reviews/<date>/``.
No per-leg ``.json``/``.md`` files: every row already carries its own
``reason`` text inline, so the index is self-sufficient.

Usage
-----
  python3 -m scripts.ops.strategy_review_stage_aware \\
      --db-path /data/bot-data/trade_journal.db \\
      --out-dir /data/bot-data/runtime_logs/strategy_reviews \\
      [--stage2-window 30d] [--stage1-window-hours 168] [--skip-stage1-cost-pull]

Exit codes: 0 ran (whether or not anything was actionable) · 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: R4's own declared demotion window (``.github/workflows/r4-demotion-gate.yml``
#: default ``window: "last40"``, ``r4_demotion_gate.WINDOW_LABEL``). Since
#: 2026-09-29 (operator, "Last 20 + strict test (Recommended)") the Stage-2
#: verdict is the T3 rule over each leg's LAST 40 closed trades, not a calendar
#: window, so there is nothing to choose: ``--stage2-window`` is accepted for
#: the caller script's sake and IGNORED.
STAGE2_WINDOW_DEFAULT = "last40"

#: R5's own default (``scripts/ops/soak_book_grade.py --window-hours``,
#: default 168 = weekly, matching its scheduled cadence).
STAGE1_WINDOW_HOURS_DEFAULT = 168

SCHEMA = "stage_aware_v1"


def _stage2_recent_payload(db_path: str) -> Dict[str, Any]:
    """Build the exact ``GET /api/bot/performance/recent?n=40`` shape
    ``scripts/ops/r4_demotion_gate.py`` expects, computed directly off the
    journal with the route's own helpers (``_book_blocks`` over ``_query``) --
    reused rather than curled since this run already has the journal on disk.
    """
    from src.web.api.routers.performance import (  # noqa: PLC0415
        RECENT_BLOCK,
        _book_blocks,
        _book_rosters,
        _newest_close,
        _query,
    )
    import scripts.ops.r4_demotion_gate as _r4  # noqa: PLC0415

    n = _r4.mr.T3_N
    path = Path(db_path)
    rosters = _book_rosters()
    real_rows = _query(path, None, demo=False)
    pids = rosters["portfolioAccounts"]
    pp_rows = _query(path, None, demo=True, account_ids=pids) if pids else []
    mirror_state = ("accounts_unreadable" if rosters["readState"] != "ok"
                    else "ok" if pids else "no_portfolio_accounts_declared")
    return {
        "n": n,
        "block": RECENT_BLOCK,
        "error": False,
        "realMoney": {"readState": "ok", "newestClosedAt": _newest_close(real_rows),
                      "perStrategy": _book_blocks(real_rows, n, RECENT_BLOCK,
                                                  rosters["realMoneyLegs"])},
        # No portfolio-mirror books declared: the mirror read is empty BY
        # DESIGN (not the full soak roster) -- every leg's mirror then abstains
        # thin rather than silently substituting the whole paper fleet.
        "mirror": {"readState": mirror_state, "accountIds": pids,
                   "newestClosedAt": _newest_close(pp_rows),
                   "perStrategy": (_book_blocks(pp_rows, n, RECENT_BLOCK,
                                                rosters["portfolioLegs"]) if pids else {})},
    }


def build_stage2_rows(db_path: str) -> Dict[str, Any]:
    """Stage-2 legs, graded on MONEY by R4 + the T3 rule -- read-only.

    There is no window to choose (see ``STAGE2_WINDOW_DEFAULT``); the returned
    ``window`` is always R4's own label.

    Returns ``{"rows": [...], "window": ..., "since": None, "n_legs": int}``.
    """
    import scripts.ops.r4_demotion_gate as _r4  # noqa: PLC0415
    from src.runtime.research_results_gate import ABSTAIN_STATES  # noqa: PLC0415

    recent = _stage2_recent_payload(db_path)
    decisions = _r4.evaluate(recent, _REPO_ROOT, coverage_floor=_r4.COVERAGE_FLOOR)
    rows: List[Dict[str, Any]] = []
    for d in decisions:
        v = d["r4"]
        chosen = v["real"] if v["chosenSource"] == "real_money" else v["mirror"]
        verdict_reached = v["status"] not in ABSTAIN_STATES and d["action"] != _r4.ABSTAIN
        rows.append({
            "stage": "S2",
            "account": d["account"],
            "strategy": d["leg"],
            "verdict_source": "scripts/ops/r4_demotion_gate.py (R4)",
            # Stage-qualified — never bare "demote"/"hold", so a consumer
            # scanning `by_action` cannot mistake it for R5's differently
            # scoped "kill"/"tweak" vocabulary.
            "proposed_action": f"S2:{d['action']}",
            "verdict_reached": verdict_reached,
            "actionable": d["action"] == _r4.DEMOTE,
            "r4_status": v["status"],
            "chosen_source": v["chosenSource"],
            "n_closed": chosen.get("trades"),
            "pnl_coverage_measured": chosen.get("pnlCoverage"),
            "coverage_floor": chosen.get("coverageFloor"),
            "min_trades": chosen.get("minTrades"),
            "net_usd_measured": chosen.get("totalPnlMeasured"),
            "net_r_net_of_full_cost": d.get("totalR"),
            "r_population": d.get("rTradeCount"),
            "t3_windows_r": d.get("windowsR"),
            "t3_p10": (d.get("threshold") or {}).get("p10"),
            "abstain": d.get("abstain"),
            "reason": d["why"],
        })
    return {"rows": rows, "window": _r4.WINDOW_LABEL, "since": None, "n_legs": len(rows)}


def build_stage1_rows(
    window_hours: int = STAGE1_WINDOW_HOURS_DEFAULT, skip_cost_pull: bool = False,
) -> Dict[str, Any]:
    """Stage-1 soak legs, graded on MECHANICS + COST FIDELITY by R5 — never
    expectancy.

    Returns ``{"rows": [...], "window_hours": int, "n_legs": int,
    "mechanics_read_state": ..., "cost_fidelity_read_state": ..., "population": ...}``.
    """
    from scripts.ops import soak_book_grade as _sbg  # noqa: PLC0415

    report = _sbg.build_report(window_hours=window_hours, skip_cost_pull=skip_cost_pull)
    rows: List[Dict[str, Any]] = []
    for leg in report["legs"]:
        disposition = leg["disposition"]
        rows.append({
            "stage": "S1",
            "account": leg["account_id"],
            "strategy": leg["strategy"],
            "verdict_source": "scripts/ops/soak_book_grade.py (R5)",
            "proposed_action": f"S1:{disposition}",
            "verdict_reached": disposition != _sbg.INSUFFICIENT,
            "actionable": disposition in (_sbg.TWEAK, _sbg.KILL),
            "disposition": disposition,
            "execution": leg.get("execution"),
            "n_needed": leg.get("n_needed"),
            "eta": leg.get("eta"),
            "reason": leg["reason"],
        })
    return {
        "rows": rows,
        "window_hours": window_hours,
        "n_legs": len(rows),
        "mechanics_read_state": report["mechanics_read_state"],
        "cost_fidelity_read_state": report["cost_fidelity_read_state"],
        "population": report["population"],
    }


def write_stage_aware_index(
    rows: List[Dict[str, Any]], out_dir: Path, extra: Dict[str, Any],
) -> Path:
    """Write the day's ``INDEX.json``.

    Same DENOMINATOR discipline as ``scripts/ml/strategy_review_packet.py``'s
    ``write_index()`` (not imported from there — that module sits outside the
    Tier-1 self-land allowlist, see the module docstring — but the CONTRACT is
    the same): every row this run examined is carried, including every row
    that could not reach a verdict, so a reader can tell "N examined, M
    actionable" from "only M were looked at". ``rows`` is sorted so the daily
    commit diff is stable and actionable rows are not buried.
    """
    utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir = Path(out_dir) / utc_date
    day_dir.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "utc_date": utc_date,
        "generated_by": "scripts/ops/strategy_review_stage_aware.py",
        # THE DENOMINATOR — every leg this run examined, whether or not a
        # verdict was reached. Never filtered before this count.
        "graded": len(rows),
        "verdict_reached": sum(1 for r in rows if r.get("verdict_reached")),
        "actionable": sum(1 for r in rows if r.get("actionable")),
        "by_action": {
            a: sum(1 for r in rows if r.get("proposed_action") == a)
            for a in sorted({r.get("proposed_action") for r in rows if r.get("proposed_action")})
        },
        **extra,
        "rows": sorted(
            rows, key=lambda r: (0 if r.get("actionable") else 1, r.get("stage") or "",
                                 r.get("strategy") or "")
        ),
    }
    path = day_dir / "INDEX.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def build_and_write_index(
    *,
    db_path: str,
    out_dir: Path,
    stage2_window: str = STAGE2_WINDOW_DEFAULT,  # inert: stage2_window — kept so the CLI/caller script keep working; R4 reads the last 40 closed trades (T3, 2026-09-29)
    stage1_window_hours: int = STAGE1_WINDOW_HOURS_DEFAULT,
    skip_stage1_cost_pull: bool = False,
) -> Tuple[Path, Dict[str, Any]]:
    """Build and write the re-scoped, stage-aware ``INDEX.json``. Returns
    ``(index_path, summary)`` where ``summary`` is what the CLI prints so an
    operator sees the actionable count and its denominator without opening
    the file.

    Every row states: which grader produced it (``verdict_source``), whether
    a verdict was reached at all (``verdict_reached``) as distinct from
    ``actionable`` (a verdict reached AND asking for a decision), and its own
    population (n_closed for Stage 2, the mechanics/cost-fidelity read for
    Stage 1).
    """
    import scripts.ops.r4_demotion_gate as _r4  # noqa: PLC0415

    s2 = build_stage2_rows(db_path)
    s1 = build_stage1_rows(window_hours=stage1_window_hours, skip_cost_pull=skip_stage1_cost_pull)
    rows = s2["rows"] + s1["rows"]

    index_path = write_stage_aware_index(
        rows,
        out_dir,
        extra={
            "question": (
                "Per leg, graded with the instrument its STAGE calls for: Stage 2 "
                "(bybit_2/alpaca_live + mirrors) on MONEY via R4's demotion "
                "verdict; Stage 1 (bybit_1, alpaca_paper) on MECHANICS + COST "
                "FIDELITY via R5 -- never expectancy, per root CLAUDE.md's "
                "promotion ladder ('a live or paper book never establishes edge')."
            ),
            "stage2": {
                "accounts": list(_r4.STAGE2_ACCOUNTS),
                "verdict_source": "scripts/ops/r4_demotion_gate.py (R4)",
                "window": s2["window"],
                "since": s2["since"],
                "n_legs": s2["n_legs"],
                "verdict_reached": sum(1 for r in s2["rows"] if r["verdict_reached"]),
                "actionable": sum(1 for r in s2["rows"] if r["actionable"]),
            },
            "stage1": {
                "accounts": list(s1["population"]["by_account"].keys()),
                "verdict_source": "scripts/ops/soak_book_grade.py (R5)",
                "window_hours": s1["window_hours"],
                "n_legs": s1["n_legs"],
                "verdict_reached": sum(1 for r in s1["rows"] if r["verdict_reached"]),
                "actionable": sum(1 for r in s1["rows"] if r["actionable"]),
                "mechanics_read_state": s1["mechanics_read_state"],
                "cost_fidelity_read_state": s1["cost_fidelity_read_state"],
            },
        },
    )
    summary = {
        "n_legs": len(rows),
        "verdict_reached": sum(1 for r in rows if r["verdict_reached"]),
        "actionable": sum(1 for r in rows if r["actionable"]),
        "stage2_actionable": sum(1 for r in s2["rows"] if r["actionable"]),
        "stage1_actionable": sum(1 for r in s1["rows"] if r["actionable"]),
        "index_path": str(index_path),
    }
    return index_path, summary


def main(argv: Optional[List[str]] = None) -> int:
    from src.utils.paths import runtime_logs_dir, trade_journal_db_path  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db-path", default=None,
                    help="trade_journal.db path (default: src.utils.paths.trade_journal_db_path()).")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="root for per-day output (default runtime_logs/strategy_reviews).")
    ap.add_argument("--stage2-window", default=STAGE2_WINDOW_DEFAULT,
                    help="DEPRECATED and IGNORED -- R4 reads each Stage-2 leg's last 40 "
                         "closed trades (T3 rule, 2026-09-29).")
    ap.add_argument("--stage1-window-hours", type=int, default=STAGE1_WINDOW_HOURS_DEFAULT,
                    help=f"R5 mechanics window in hours (default {STAGE1_WINDOW_HOURS_DEFAULT}).")
    ap.add_argument("--skip-stage1-cost-pull", action="store_true",
                    help="R5: mechanics only, skip the live cost-fidelity pull (never used by the cron).")
    args = ap.parse_args(argv)

    db_path = args.db_path or trade_journal_db_path()
    if not Path(db_path).exists():
        print(f"error: trade journal not found at {db_path}", file=sys.stderr)
        return 2
    out_dir = args.out_dir or (Path(runtime_logs_dir()) / "strategy_reviews")

    index_path, summary = build_and_write_index(
        db_path=db_path,
        out_dir=out_dir,
        stage2_window=args.stage2_window,
        stage1_window_hours=args.stage1_window_hours,
        skip_stage1_cost_pull=args.skip_stage1_cost_pull,
    )
    print(
        f"stage-aware index -> {index_path} (n_legs={summary['n_legs']} "
        f"verdict_reached={summary['verdict_reached']} actionable={summary['actionable']} "
        f"[S2={summary['stage2_actionable']} S1={summary['stage1_actionable']}])"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
