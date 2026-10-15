"""leg_replay_emit: synthetic-fixture tests (no network, no harness run)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    s = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


lre = _load("leg_replay_emit_t", "scripts/research/leg_replay_emit.py")
sr = _load("script_run_lre", "scripts/research/script_run.py")
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def write_emit(path: Path, leg: str, rows) -> str:
    """rows: (day_offset, direction, net_r)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for d, direction, r in rows:
            t = T0 + timedelta(days=d)
            fh.write(json.dumps({"strategy": leg, "entry_time": str(t), "exit_time": str(t + timedelta(hours=8)),
                                 "direction": direction, "net_r": r}) + "\n")
    return str(path)


def drag_rows(n=80, short_r=-0.5, long_r=0.4):
    return [(i * 5, "short" if i % 2 else "long", short_r if i % 2 else long_r) for i in range(n)]


def fold_view(tmp_path, rows):
    p = write_emit(tmp_path / "a.jsonl", "x", rows)
    return lre.leg_folds([p], "x")


def test_short_mirror_counts(tmp_path):
    f = fold_view(tmp_path, drag_rows())
    assert f["n_short"] == 40 and f["folds_negative_short"] == 4 and f["pooled_short_net_r"] < 0


def test_short_drag_confirmed_not_confirmed_indeterminate(tmp_path):
    a = fold_view(tmp_path, drag_rows())
    b = lre.leg_folds([write_emit(tmp_path / "b.jsonl", "b", drag_rows())], "b")
    pooled = lre.leg_folds([str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")], "p")
    assert lre.grade_short_drag({"a": a, "b": b}, pooled)["outcome"] == "CONFIRMED"
    good = lre.leg_folds([write_emit(tmp_path / "g.jsonl", "g", drag_rows(short_r=0.5))], "g")
    poolg = lre.leg_folds([str(tmp_path / "g.jsonl")], "p")
    g = lre.grade_short_drag({"g": good}, poolg)
    assert g["outcome"] == "NOT_CONFIRMED" and g["verdict"] == "fail"
    thin = lre.leg_folds([write_emit(tmp_path / "t.jsonl", "t", drag_rows(n=20))], "t")
    assert lre.grade_short_drag({"t": thin}, thin)["outcome"] == "INDETERMINATE"


def test_mixed_grades_pass_only_when_a_leg_confirms_alone(tmp_path):
    bad = fold_view(tmp_path, drag_rows())
    good = lre.leg_folds([write_emit(tmp_path / "g.jsonl", "g", drag_rows(short_r=0.5))], "g")
    pooled = lre.leg_folds([str(tmp_path / "a.jsonl"), str(tmp_path / "g.jsonl")], "p")
    g = lre.grade_short_drag({"a": bad, "g": good}, pooled)
    assert g["outcome"] == "MIXED" and g["confirmed_legs"] == ["a"]


def test_trend_asym_never_grades_side_constrained_leg(tmp_path):
    f = fold_view(tmp_path, drag_rows())
    g = lre.grade_trend_asym({"eth": f, "xrp": f}, {"eth": None, "xrp": "short"})
    assert g["per_leg"] == {"eth": "CONFIRMED"} and "xrp" in g["controls"] and g["verdict"] == "pass"


def test_side_constraint_reads_config_keys():
    assert lre.side_constraint({"side_filter": "short"}) == "short"
    assert lre.side_constraint({"long_only": True}) == "long"
    assert lre.side_constraint({}) is None


def series(fn, days=400):
    return {(T0 + timedelta(days=i)).date().isoformat(): fn(i) for i in range(days) if fn(i)}


def test_portfolio_proposes_removing_the_leg_that_carries_the_drawdown():
    good = series(lambda i: 0.2 if i % 3 == 0 else 0.0)
    bad = series(lambda i: -0.5 if i % 3 == 1 else (0.1 if i % 3 == 0 else 0.0))
    g = lre.grade_portfolio({"good": good, "bad": bad}, {"good": "ETHUSDT", "bad": "XRPUSDT"})
    assert g["outcome"] == "PROPOSE" and g["propose_remove"] == ["bad"] and g["verdict"] == "pass"


def test_portfolio_no_action_when_removals_are_worse():
    a = series(lambda i: 0.3 if i % 4 == 0 else 0.0)
    b = series(lambda i: 0.3 if i % 4 == 2 else 0.0)
    g = lre.grade_portfolio({"a": a, "b": b}, {"a": "ETHUSDT", "b": "SOLUSDT"})
    assert g["outcome"] == "NO_ACTION" and g["verdict"] == "no_action_warranted"


def test_portfolio_indeterminate_below_oos_floor():
    a = series(lambda i: 0.3, days=60)
    b = series(lambda i: -0.1, days=60)
    assert lre.grade_portfolio({"a": a, "b": b}, {"a": "X", "b": "Y"})["verdict"] == "indeterminate"


def test_base_args_are_config_exact_for_a_live_leg():
    import m20_fleet_exit_sweep as m20
    cfg = lre.load_strategies()["xrp_pullback_2h"]
    a = m20.base_args("xrp_pullback_2h", cfg, "pullback", "d.csv", "2h", m20.LIVE_TP_CAP_PCT)
    assert "--tp-cap-pct" in a and "--pullback-frac" in a and "--resample" in a


def test_build_cmd_refuses_missing_data_and_non_emit_family(tmp_path):
    st = lre.load_strategies()
    cmd, why = lre.build_cmd("xrp_pullback_2h", st["xrp_pullback_2h"], tmp_path, Path("e"), Path("s"), "a", "b")
    assert cmd is None and "no candle file" in why
    scalp = next((k for k in st if k.startswith("ict_scalp")), None)
    if scalp:
        cmd, why = lre.build_cmd(scalp, st[scalp], tmp_path, Path("e"), Path("s"), "a", "b")
        assert cmd is None and "no emit-capable harness" in why


def test_main_from_emits_writes_contract_verdict_and_daily_r(tmp_path):
    legs = ["xrp_pullback_2h", "trend_donchian_eth_4h"]
    em = tmp_path / "em"
    for lg in legs:
        write_emit(em / f"{lg}.jsonl", lg, drag_rows())
    out = tmp_path / "out"
    assert lre.main(["--legs", ",".join(legs), "--from-emits", str(em), "--rule", "short-drag",
                     "--out", str(out)]) == 0
    v = json.loads((out / "verdict.json").read_text())
    assert v["read_state"] == "measured" and isinstance(v["n"], int) and v["verdict"] in sr.VERDICTS
    assert (out / "daily_r" / "xrp_pullback_2h.json").exists() and (out / "replay.json").exists()
    assert set(v["measurement"]["per_leg"]) == set(legs)
    # the REAL collector accepts it
    rel = Path("o")
    (tmp_path / rel).mkdir()
    (tmp_path / rel / "verdict.json").write_text(json.dumps(v))
    (tmp_path / rel / "run-manifest.json").write_text(json.dumps(
        {"all_ok": True, "commands": [{"index": 0, "argv": ["python3", "x.py"], "exit_code": 0}]}))
    plan = SimpleNamespace(unit="RQ-20261010-419b-02", out_dir=rel, rule_id="R", rule_registered_at="2026-10-10")
    rec = sr.derive_record(plan, repo=tmp_path)
    assert rec["read_state"] == "measured" and rec["n"] == str(v["n"])


def test_failed_leg_is_producer_failed_never_a_partial_grade(tmp_path):
    out = tmp_path / "o"
    assert lre.main(["--legs", "xrp_pullback_2h", "--from-emits", str(tmp_path / "none"), "--out", str(out)]) == 0
    v = json.loads((out / "verdict.json").read_text())
    assert v["read_state"] == "producer_failed" and v["n"] is None and v["verdict"] == "not_applicable"


def test_unknown_leg_is_producer_failed(tmp_path):
    lre.main(["--legs", "nope_leg", "--out", str(tmp_path)])
    assert json.loads((tmp_path / "verdict.json").read_text())["read_state"] == "producer_failed"


def test_long_drag_mirrors_short_drag(tmp_path):
    # drag_rows with the sides' returns swapped: longs lose, shorts win
    rows = [(d, side, r) for d, side, r in drag_rows(short_r=0.4, long_r=-0.5)]
    a = fold_view(tmp_path, rows)
    assert a["n_long"] == 40 and a["folds_negative_long"] == 4 and a["pooled_long_net_r"] < 0
    g = lre.grade_long_drag({"a": a}, a)
    assert g["outcome"] == "CONFIRMED" and g["verdict"] == "pass" and g["confirmed_legs"] == ["a"]
    good = lre.leg_folds([write_emit(tmp_path / "g.jsonl", "g", drag_rows())], "g")
    assert lre.grade_long_drag({"g": good}, good)["outcome"] == "NOT_CONFIRMED"
    thin = lre.leg_folds([write_emit(tmp_path / "t.jsonl", "t", drag_rows(n=20, long_r=-0.5))], "t")
    assert lre.grade_long_drag({"t": thin}, thin)["outcome"] == "INDETERMINATE"


def test_long_drag_never_grades_side_constrained_leg(tmp_path):
    a = fold_view(tmp_path, [(d, s, r) for d, s, r in drag_rows(short_r=0.4, long_r=-0.5)])
    g = lre.grade_long_drag({"a": a, "sol": a}, a, {"a": None, "sol": "short"})
    assert "sol" in g["controls"] and g["confirmed_legs"] == ["a"]
    assert g["pooled_confirmed"] is False        # pool holds a control -> never graded
    only = lre.grade_long_drag({"sol": a}, a, {"sol": "short"})
    assert only["verdict"] == "indeterminate"
