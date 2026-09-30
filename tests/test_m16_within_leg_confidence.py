"""Synthetic-only tests for scripts/research/m16_within_leg_confidence.py.

The real committed ledgers are deliberately NOT read here: the unit's rule is registered
before its first run, and a smoke on the real data would be a look at the answer.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.research import m16_within_leg_confidence as m  # noqa: E402


def _write(tmp: Path, legs: dict, coverage="measured") -> Path:
    ev = tmp / "comms" / "strategy_evidence"
    (ev / "runs" / "d").mkdir(parents=True)
    for leg, rows in legs.items():
        led = ev / "runs" / "d" / f"{leg}__trades.jsonl"
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        (ev / f"{leg}.json").write_text(json.dumps(
            {"coverage_state": coverage, "source_run": f"comms/strategy_evidence/runs/d/{leg}__trades.jsonl"}))
    return ev


def _legs(n_legs, n, signal, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(n_legs):
        rows = []
        for i in range(n):
            c = float(rng.random())
            r = signal * (c - 0.5) * 4 + float(rng.normal())
            rows.append({"confidence": c, "net_r": r,
                         "entry_time": f"2025-{1 + i * 12 // n:02d}-{1 + i % 27:02d} 00:00:00+00:00"})
        out[f"ict_scalp_x{k}_5m" if k % 2 else f"trend_donchian_x{k}"] = rows
    return out


def _run(tmp_path, legs, monkeypatch, **bars):
    ev = _write(tmp_path, legs)
    monkeypatch.setattr(m, "REPO", tmp_path)
    monkeypatch.setattr(m, "PERM_N", 300)
    for k, v in bars.items():
        monkeypatch.setattr(m, k, v)
    loaded, census = m.load_legs(ev)
    return m.grade(loaded, census)


def test_planted_within_leg_signal_passes(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(8, 300, 0.5), monkeypatch)
    assert v["verdict"] == "pass", v["measurement"]


def test_no_signal_fails(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(8, 300, 0.0), monkeypatch)
    assert v["verdict"] == "fail" and v["read_state"] == "measured"


def test_thin_population_is_indeterminate_not_fail(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(3, 60, 0.5), monkeypatch)
    assert v["verdict"] == "indeterminate"


def test_no_usable_leg_is_not_applicable(tmp_path, monkeypatch):
    ev = _write(tmp_path, _legs(2, 100, 0.5), coverage="harness_failed")
    monkeypatch.setattr(m, "REPO", tmp_path)
    legs, census = m.load_legs(ev)
    v = m.grade(legs, census)
    assert v["verdict"] == "not_applicable" and v["read_state"] == "no_data"
    assert census["not_measured"] == 2


def test_percentile_uses_only_earlier_trades():
    c = np.array([0.1] * 20 + [0.5, 0.05])
    t = np.arange(len(c))
    p = m.past_percentile(c, t)
    assert np.isnan(p[:20]).all() and p[20] == 1.0 and p[21] == 0.0


def test_same_timestamp_trades_are_not_each_others_past():
    # 21 trades at distinct times, then two at the SAME time. Neither of the two may count the
    # other (list order inside a tie is arbitrary), and the first has exactly 21 earlier trades.
    c = np.array([0.1] * 21 + [0.9, 0.2])
    t = np.array(list(range(21)) + [100, 100])
    p = m.past_percentile(c, t)
    assert p[21] == 1.0 and p[22] == 1.0   # both ranked against the same 21 earlier trades only
    # a tie block too early to have MIN_PAST strictly-earlier trades gets no percentile
    t2 = np.array([0] * 19 + [1, 1, 2, 2])
    c2 = np.linspace(0, 1, 23)
    p2 = m.past_percentile(c2, t2)
    assert np.isnan(p2[:19]).all() and np.isnan(p2[19]) and np.isnan(p2[20])


def test_no_data_lands_through_derive_record_build_validate(tmp_path, monkeypatch):
    """The output the script writes when nothing qualifies must be ADMISSIBLE to the landing
    path: read_state no_data needs population.n null, and a fabricated 0 is refused."""
    from scripts.research import research_result as rr
    from scripts.research import script_run as sr
    ev = tmp_path / "comms" / "strategy_evidence"
    ev.mkdir(parents=True)                       # no legs at all
    out = Path("comms/research/RQ-20260930-601/run1")
    (tmp_path / out).mkdir(parents=True)
    assert m.main(["--evidence-dir", str(ev), "--out", str(tmp_path / out)]) == 0
    v = json.loads((tmp_path / out / "verdict.json").read_text())
    assert v["read_state"] == "no_data" and v["n"] is None
    (tmp_path / out / "run-manifest.json").write_text(json.dumps(
        {"all_ok": True, "commands": [{"index": 0, "argv": ["python3", "x.py"], "exit_code": 0}]}))
    plan = sr.Plan(unit="RQ-20260930-601", path=tmp_path, commands=[["python3", "x.py"]], timeout_minutes=30,
                   out_dir=out, rule_id=m.RULE_ID, rule_registered_at="2026-09-30")
    d = sr.derive_record(plan, repo=tmp_path)
    rec = rr.build(research_unit="RQ-20260930-601", decision_rule_id=d["decision_rule_id"],
                   decision_rule_registered_at=d["decision_rule_registered_at"],
                   verdict=d["verdict"], read_state=d["read_state"],
                   population_description=d["population"], n=rr._coerce_n(d["n"]),
                   workflow="research-script-run.yml", run_id="1", note=d["note"],
                   commit_sha="0" * 40, tool=d["tool"], artifact_store=d["artifact_store"],
                   artifact_locator=d["artifact_locator"])
    assert rr.validate(rec) == []
    # and the old shape really was refused
    bad = dict(rec, population=dict(rec["population"], n=0))
    assert any("population.n: null" in e for e in rr.validate(bad))


def test_measured_output_also_lands(tmp_path, monkeypatch):
    from scripts.research import research_result as rr
    v = _run(tmp_path, _legs(8, 300, 0.5), monkeypatch)
    rec = rr.build(research_unit="RQ-20260930-601", decision_rule_id=m.RULE_ID,
                   decision_rule_registered_at="2026-09-30", verdict=v["verdict"],
                   read_state=v["read_state"], population_description=v["population"],
                   n=v["n"], workflow="w", run_id="1", commit_sha="0" * 40, tool="t",
                   artifact_store="comms/research/x", artifact_locator="comms/research/x/",
                   measurement=json.loads(json.dumps(v["measurement"], default=float)))
    assert rr.validate(rec) == []


def test_drifting_confidence_with_no_rank_signal_cannot_pass_E(tmp_path, monkeypatch):
    """A leg whose confidence rises steadily has past-only percentile ~1 for every trade. Raw
    weights (0.5 + pct) are then ~1.5 throughout, so on a net-positive leg sum((w-1) r) is about
    0.5 x net R with NO ranking at all. Normalised per leg the tilt is flat and delta is 0."""
    rng = np.random.default_rng(7)
    legs = {}
    for k in range(6):
        n = 300
        legs[f"trend_donchian_d{k}"] = [
            {"confidence": 0.001 * i,                         # strictly increasing: pure drift
             "net_r": 0.3 + float(rng.normal()),              # positive mean, independent of rank
             "entry_time": str(pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(hours=36 * i))}
            for i in range(n)]
    v = _run(tmp_path, legs, monkeypatch)
    meas = v["measurement"]
    # the premise: the OLD un-normalised formula would have cleared E on this data
    old = 0.0
    for rows in legs.values():
        r = np.array([x["net_r"] for x in rows])
        c = np.array([x["confidence"] for x in rows])
        t = np.arange(len(rows))
        pct = m.past_percentile(c, t)
        ok = ~np.isnan(pct)
        old += float(((0.5 + pct[ok] - 1.0) * r[ok]).sum())
    assert old > 50, old
    assert meas["conditions"]["E_delta>0"] is False, v["informational"]
    assert abs(v["informational"]["delta_net_r_total"]) < 1e-9
    assert v["verdict"] != "pass"


def test_scale_differences_between_legs_do_not_matter(tmp_path, monkeypatch):
    legs = _legs(8, 300, 0.5)
    scaled = {k: [dict(r, confidence=r["confidence"] * (10 ** i)) for r in v]
              for i, (k, v) in enumerate(legs.items())}
    a = _run(tmp_path, legs, monkeypatch)["measurement"]["rho"]
    b = _run(tmp_path / "s", scaled, monkeypatch)["measurement"]["rho"]
    assert abs(a - b) < 1e-9
