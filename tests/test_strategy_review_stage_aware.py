"""B4 (checklist row, 2026-09-27) — stage-aware re-scoping of the review packet.

Tests the GLUE `scripts/ops/strategy_review_stage_aware.py` adds, not R4/R5's
own verdict logic — that is already covered by `tests/test_r4_demotion_gate.py`
and `scripts/ops/soak_book_grade.py --self-test` respectively. What matters
here: does the packet generator correctly translate each grader's own verdict
into `verdict_reached`/`actionable`, tag rows with which grader produced them,
and does the combined index state its population (never re-deriving either
grader's own vocabulary, never fabricating a denominator)?
"""
from __future__ import annotations

import json

from scripts.ops import strategy_review_stage_aware as srp


def _r4_decision(leg, account, action, status, **over):
    import scripts.ops.r4_demotion_gate as r4

    base = {
        "leg": leg,
        "account": account,
        "action": action,
        "why": f"why-{action}-{status}",
        "r4": {
            "status": status,
            "chosenSource": "real_money",
            "real": {
                "trades": 25, "totalPnlMeasured": -100.0, "pnlCoverage": 0.9,
                "coverageFloor": r4.COVERAGE_FLOOR, "minTrades": r4.mr.T3_N,
            },
            "mirror": {
                "trades": 25, "totalPnlMeasured": -100.0, "pnlCoverage": 0.9,
                "coverageFloor": r4.COVERAGE_FLOOR, "minTrades": r4.mr.T3_N,
            },
        },
        "totalR": -1.5,
        "rTradeCount": 25,
    }
    base.update(over)
    return base


def test_build_stage2_rows_translates_r4_verdicts(monkeypatch):
    import scripts.ops.r4_demotion_gate as r4

    monkeypatch.setattr(
        srp,
        "_stage2_recent_payload",
        lambda db_path: {"n": 40, "block": 20, "error": False,
                         "realMoney": {"readState": "ok", "perStrategy": {}},
                         "mirror": {"readState": "ok", "perStrategy": {}}},
    )
    monkeypatch.setattr(
        r4,
        "evaluate",
        lambda perf, root, **kw: [
            _r4_decision("leg_a", "bybit_2", r4.DEMOTE, "would_block"),
            _r4_decision("leg_b", "bybit_2", r4.HOLD, "pass"),
            _r4_decision("leg_c", "bybit_2", r4.HOLD, "abstain_thin"),
            _r4_decision("leg_d", "alpaca_live", r4.HOLD, "abstain_unverified"),
        ],
    )
    out = srp.build_stage2_rows("db.sqlite")
    by_leg = {r["strategy"]: r for r in out["rows"]}

    # would_block + demote -> a verdict was reached, and it is ACTIONABLE.
    assert by_leg["leg_a"]["verdict_reached"] is True
    assert by_leg["leg_a"]["actionable"] is True
    assert by_leg["leg_a"]["proposed_action"] == "S2:demote"
    assert by_leg["leg_a"]["verdict_source"] == "scripts/ops/r4_demotion_gate.py (R4)"

    # pass + hold -> a verdict was reached, not actionable (measured net is fine).
    assert by_leg["leg_b"]["verdict_reached"] is True
    assert by_leg["leg_b"]["actionable"] is False

    # abstain_thin / abstain_unverified -> CANNOT be graded yet, never actionable.
    assert by_leg["leg_c"]["verdict_reached"] is False
    assert by_leg["leg_c"]["actionable"] is False
    assert by_leg["leg_d"]["verdict_reached"] is False
    assert by_leg["leg_d"]["actionable"] is False

    assert out["n_legs"] == 4
    assert all(r["proposed_action"].startswith("S2:") for r in out["rows"])
    # The R sign + population travel with the row so a reader never has to
    # re-open R4's own record to see why.
    assert by_leg["leg_a"]["net_r_net_of_full_cost"] == -1.5
    assert by_leg["leg_a"]["r_population"] == 25


def test_build_stage1_rows_translates_r5_dispositions(monkeypatch):
    from scripts.ops import soak_book_grade as sbg

    def fake_report(window_hours, skip_cost_pull=False):
        return {
            "legs": [
                {"account_id": "bybit_1", "strategy": "leg_x", "execution": "live",
                 "disposition": sbg.KILL, "reason": "starved",
                 "n_needed": None, "eta": None},
                {"account_id": "alpaca_paper", "strategy": "leg_y", "execution": "live",
                 "disposition": sbg.HEALTHY, "reason": "flowing, consistent",
                 "n_needed": None, "eta": None},
                {"account_id": "alpaca_paper", "strategy": "leg_z", "execution": "live",
                 "disposition": sbg.TWEAK, "reason": "flowing, divergent cost",
                 "n_needed": None, "eta": None},
                {"account_id": "bybit_1", "strategy": "leg_w", "execution": "shadow",
                 "disposition": sbg.INSUFFICIENT, "reason": "execution=shadow",
                 "n_needed": None, "eta": "becomes gradeable if flipped live"},
            ],
            "mechanics_read_state": "measured",
            "cost_fidelity_read_state": "measured",
            "population": {"n": 4, "by_account": {"bybit_1": 2, "alpaca_paper": 2}},
        }

    monkeypatch.setattr(sbg, "build_report", fake_report)
    out = srp.build_stage1_rows(window_hours=168)
    by_leg = {r["strategy"]: r for r in out["rows"]}

    assert by_leg["leg_x"]["verdict_reached"] is True and by_leg["leg_x"]["actionable"] is True
    assert by_leg["leg_x"]["proposed_action"] == "S1:kill"
    assert by_leg["leg_y"]["verdict_reached"] is True and by_leg["leg_y"]["actionable"] is False
    assert by_leg["leg_z"]["verdict_reached"] is True and by_leg["leg_z"]["actionable"] is True
    # insufficient-data (including the shadow-execution case) reached no
    # verdict and is never actionable — never collapsed into a false "healthy".
    assert by_leg["leg_w"]["verdict_reached"] is False and by_leg["leg_w"]["actionable"] is False

    assert out["n_legs"] == 4
    assert out["mechanics_read_state"] == "measured"
    assert out["cost_fidelity_read_state"] == "measured"
    assert all(r["verdict_source"] == "scripts/ops/soak_book_grade.py (R5)" for r in out["rows"])


def test_write_stage_aware_index_keeps_the_denominator(tmp_path):
    rows = [
        {"stage": "S2", "account": "bybit_2", "strategy": "leg_a",
         "proposed_action": "S2:demote", "verdict_reached": True, "actionable": True},
        {"stage": "S2", "account": "bybit_2", "strategy": "leg_c",
         "proposed_action": "S2:hold", "verdict_reached": False, "actionable": False},
        {"stage": "S1", "account": "bybit_1", "strategy": "leg_b",
         "proposed_action": "S1:healthy", "verdict_reached": True, "actionable": False},
    ]
    path = srp.write_stage_aware_index(rows, tmp_path, extra={"stage2": {}, "stage1": {}})
    payload = json.loads(path.read_text())
    assert payload["schema"] == "stage_aware_v1"
    assert payload["graded"] == 3           # THE DENOMINATOR — leg_c included
    assert payload["verdict_reached"] == 2
    assert payload["actionable"] == 1
    assert {r["strategy"] for r in payload["rows"]} == {"leg_a", "leg_b", "leg_c"}
    assert payload["by_action"] == {"S1:healthy": 1, "S2:demote": 1, "S2:hold": 1}
    # Actionable rows sort first so they are never buried under an arbitrary
    # ordering in a long committed index.
    assert payload["rows"][0]["strategy"] == "leg_a"


def test_build_and_write_index_states_population_never_fabricated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        srp,
        "build_stage2_rows",
        lambda db_path: {
            "rows": [
                {"stage": "S2", "account": "bybit_2", "strategy": "leg_a",
                 "proposed_action": "S2:demote", "verdict_reached": True, "actionable": True,
                 "verdict_source": "scripts/ops/r4_demotion_gate.py (R4)", "reason": "r4"},
                {"stage": "S2", "account": "bybit_2", "strategy": "leg_c",
                 "proposed_action": "S2:hold", "verdict_reached": False, "actionable": False,
                 "verdict_source": "scripts/ops/r4_demotion_gate.py (R4)", "reason": "abstain_thin"},
            ],
            "window": "last40", "since": "2026-09-01T00:00:00+00:00", "n_legs": 2,
        },
    )
    monkeypatch.setattr(
        srp,
        "build_stage1_rows",
        lambda window_hours, skip_cost_pull=False: {
            "rows": [
                {"stage": "S1", "account": "bybit_1", "strategy": "leg_b",
                 "proposed_action": "S1:healthy", "verdict_reached": True, "actionable": False,
                 "verdict_source": "scripts/ops/soak_book_grade.py (R5)", "reason": "flowing"},
            ],
            "window_hours": window_hours, "n_legs": 1,
            "mechanics_read_state": "measured", "cost_fidelity_read_state": "measured",
            "population": {"n": 1, "by_account": {"bybit_1": 1}},
        },
    )

    index_path, summary = srp.build_and_write_index(db_path="db.sqlite", out_dir=tmp_path)

    assert summary["n_legs"] == 3
    assert summary["verdict_reached"] == 2  # leg_a + leg_b; leg_c abstained
    assert summary["actionable"] == 1       # leg_a only
    assert summary["stage2_actionable"] == 1
    assert summary["stage1_actionable"] == 0

    payload = json.loads(index_path.read_text())
    assert payload["schema"] == "stage_aware_v1"
    assert payload["graded"] == 3
    assert payload["actionable"] == 1
    assert {r["strategy"] for r in payload["rows"]} == {"leg_a", "leg_b", "leg_c"}
    assert payload["stage2"]["window"] == "last40"
    assert payload["stage2"]["actionable"] == 1
    assert payload["stage2"]["verdict_reached"] == 1
    assert payload["stage1"]["window_hours"] == 168
    assert payload["stage1"]["mechanics_read_state"] == "measured"


def test_main_wires_flags_through_to_build_and_write_index(tmp_path, monkeypatch):
    import scripts.ops.strategy_review_stage_aware as mod

    db = tmp_path / "j.sqlite"
    db.write_text("")
    called = {}

    def fake(**kwargs):
        called.update(kwargs)
        idx = tmp_path / "INDEX.json"
        idx.write_text("{}")
        return idx, {"n_legs": 0, "verdict_reached": 0, "actionable": 0,
                     "stage2_actionable": 0, "stage1_actionable": 0}

    monkeypatch.setattr(mod, "build_and_write_index", fake)
    rc = mod.main(["--db-path", str(db), "--out-dir", str(tmp_path),
                   "--stage2-window", "7d", "--stage1-window-hours", "24"])
    assert rc == 0
    assert called["db_path"] == str(db)
    assert called["stage2_window"] == "7d"
    assert called["stage1_window_hours"] == 24


def test_main_refuses_on_missing_journal(tmp_path):
    import scripts.ops.strategy_review_stage_aware as mod

    rc = mod.main(["--db-path", str(tmp_path / "does-not-exist.sqlite"),
                   "--out-dir", str(tmp_path)])
    assert rc == 2


def test_stage2_payload_off_a_real_journal_is_the_routes_shape(tmp_path, monkeypatch):
    """The payload is built with the route's own helpers, so the gate reads
    the same last-40 / two-20-trade-window population the workflow fetches."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _perf_window_golden_seed as S
    from src.web.api.routers import performance as P

    db = tmp_path / "tj.db"
    S.seed(db, n_per=45)
    monkeypatch.setattr(P, "journal_trust_map", lambda: S.TRUST_MAP)
    monkeypatch.setattr(P, "_book_rosters", lambda: {
        "readState": "ok", "realMoneyLegs": ["leg_a", "leg_idle"],
        "portfolioAccounts": ["bybit_portfolio"], "portfolioLegs": ["leg_a"]})
    recent = srp._stage2_recent_payload(str(db))
    assert recent["realMoney"]["perStrategy"]["leg_idle"]["closedAvailable"] == 0
    assert recent["n"] == 40 and recent["block"] == 20
    leg = recent["realMoney"]["perStrategy"]["leg_a"]
    assert leg["complete"] and [b["totalTrades"] for b in leg["blocks"]] == [20, 20]
    assert list(recent["mirror"]["perStrategy"]) == ["leg_a"]
