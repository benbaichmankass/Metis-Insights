"""Theme fair share + aging + structured precondition in the research-queue dispatcher."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scripts.research import dispatch_queue as dq
from scripts.research.research_queue import QueueJob, load_themes, validate

NOW = datetime(2030, 1, 10, 12, tzinfo=timezone.utc)
TH = {"share_window_hours": 48, "aging_hours": 72,
      "themes": {"regime": {"weight": 5}, "live": {"weight": 4}, "macro": {"weight": 1}}}


def job(i, theme, prio=2, **kw):
    raw = {"id": f"RQ-20300110-{i:03d}", "status": "queued", "cadence": "once", "theme": theme,
           "priority": prio, "run": {"workflow": "x.yml"}, **kw}
    return QueueJob(path=Path(f"{raw['id']}.yaml"), raw=raw)


def themes_of(order, n):
    return [j.raw["theme"] for j in order[:n]]


def test_every_theme_with_work_is_served_before_any_gets_a_second_slot():
    jobs = [job(i, "regime") for i in range(1, 9)] + [job(20 + i, "live") for i in range(6)] \
        + [job(40 + i, "macro") for i in range(4)]
    first3 = themes_of(dq.fair_order(jobs, NOW, TH), 3)
    assert sorted(first3) == ["live", "macro", "regime"]       # low weight still gets a slot


def test_shares_follow_weights_over_a_long_run():
    jobs = [job(i, "regime") for i in range(1, 40)] + [job(100 + i, "live") for i in range(40)] \
        + [job(200 + i, "macro") for i in range(40)]
    order = themes_of(dq.fair_order(jobs, NOW, TH), 30)
    assert order.count("regime") > order.count("live") > order.count("macro") >= 2
    assert abs(order.count("regime") / 30 - 0.5) < 0.1          # 5 / (5+4+1)


def test_recent_fires_count_against_a_theme():
    stamp = (NOW - timedelta(hours=1)).isoformat()
    fired = [job(300 + i, "regime", last_dispatched_at=stamp, cadence="daily") for i in range(3)]
    waiting = [job(1, "regime"), job(2, "live"), job(3, "macro")]
    # regime already fired 3x in the window -> live and macro go first
    assert themes_of(dq.fair_order(fired + waiting, NOW, TH), 2) == ["live", "macro"] or \
        set(themes_of(dq.fair_order(fired + waiting, NOW, TH), 2)) == {"live", "macro"}


def test_aging_lifts_a_waiting_low_priority_unit_above_a_fresh_high_priority_one():
    old = job(1, "live", prio=3)
    old.raw["id"] = "RQ-20300101-001"            # waiting 9 days => 3 aging steps => effective 0
    fresh = job(2, "live", prio=1)
    order = dq.fair_order([fresh, old], NOW, TH)
    assert order[0] is old
    assert dq.effective_priority(old.raw, old.id, NOW, 72) == 0
    assert dq.effective_priority(fresh.raw, fresh.id, NOW, 72) == 1


def test_not_due_and_session_bound_units_are_not_candidates():
    bad = job(1, "live", run={"workflow": "none -- session-local"})
    done = job(2, "live", status="done")
    ran = job(3, "live", last_dispatched_at=(NOW - timedelta(hours=1)).isoformat())
    ok = job(4, "live")
    order = dq.fair_order([bad, done, ran, ok], NOW, TH)
    assert order[0] is ok and set(map(id, order)) == set(map(id, [bad, done, ran, ok]))


def test_requires_result_gate(tmp_path):
    e = {"requires_result": {"unit": "RQ-20300101-001", "verdict": "pass"}}
    met, why = dq.precondition_met(e, tmp_path)
    assert not met and "unmet" in why
    d = tmp_path / "research/results/RQ-20300101-001"
    d.mkdir(parents=True)
    (d / "1.jsonl").write_text(json.dumps({"read_state": "measured", "verdict": "fail"}) + "\n")
    assert not dq.precondition_met(e, tmp_path)[0]             # measured but the wrong verdict
    (d / "2.jsonl").write_text(json.dumps({"read_state": "producer_failed", "verdict": "pass"}) + "\n")
    assert not dq.precondition_met(e, tmp_path)[0]             # right verdict, never measured
    (d / "3.jsonl").write_text(json.dumps({"read_state": "measured", "verdict": "pass"}) + "\n")
    assert dq.precondition_met(e, tmp_path)[0]
    assert dq.precondition_met({}, tmp_path)[0]                # no field => no gate


def test_unmet_precondition_is_not_dispatched(tmp_path, capsys):
    u = {"id": "RQ-20300110-001", "status": "queued", "cadence": "once", "title": "t", "question": "q",
         "theme": "live_strategy", "priority": 1, "run": {"workflow": "x.yml"}, "lands": {"store": "s"},
         "requires_result": {"unit": "RQ-20300101-999", "verdict": "pass"}}
    (tmp_path / "RQ-20300110-001.yaml").write_text(yaml.safe_dump(u))
    dq.main(["--queue-dir", str(tmp_path), "--json"])
    row = json.loads(capsys.readouterr().out)["decisions"][0]
    assert row["outcome"] == "not_due" and "precondition unmet" in row["reason"]


def test_validator_requires_theme_and_priority_on_runnable_units():
    base = {"id": "RQ-20300110-001", "title": "t", "question": "q", "cadence": "once", "status": "queued",
            "run": {"workflow": "x.yml"}, "lands": {"store": "s"}}
    errs = validate(base)
    assert any("theme" in e for e in errs) and any("priority" in e for e in errs)
    ok = {**base, "theme": "regime", "priority": 1}
    assert not [e for e in validate(ok) if "theme" in e or "priority" in e]
    assert any("priority" in e for e in validate({**ok, "priority": 4}))
    assert any("priority" in e for e in validate({**ok, "priority": True}))
    assert any("theme" in e for e in validate({**ok, "theme": "nope"}))
    done = {**base, "status": "done"}
    assert not [e for e in validate(done) if "theme" in e or "priority" in e]


def test_themes_file_is_well_formed_and_orders_regime_first_macro_last():
    t = load_themes()["themes"]
    w = {k: v["weight"] for k, v in t.items()}
    assert w["regime"] == max(w.values()) and w["macro"] == min(w.values())
    with pytest.raises(ValueError):
        load_themes(Path(__file__))            # not a themes file


def test_committed_queue_dispatches_every_theme_it_holds():
    from scripts.research.research_queue import load_queue
    jobs, err = load_queue(Path(dq._DEFAULT_QUEUE))
    assert err is None
    order = dq.fair_order(jobs, datetime.now(timezone.utc), load_themes())
    live = [j for j in order if j.valid and j.status == "queued" and dq._is_due(j.raw, datetime.now(timezone.utc))[0]
            and str((j.raw.get("run") or {}).get("workflow") or "").endswith(".yml")]
    themes = {j.raw["theme"] for j in live}
    # No theme is starved: each one holding due work shows up in the first 3 picks per theme. (Not the
    # first len(themes): a theme that already fired a lot in the window is legitimately behind.)
    assert set(themes_of(live, 3 * len(themes))) == themes
