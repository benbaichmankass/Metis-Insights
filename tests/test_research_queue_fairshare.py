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


def test_themes_file_is_well_formed_and_orders_prop_first_macro_last():
    # OPERATOR DECISION 2026-10-05 (popup, verbatim "Prop first (Recommended)"): new_strategy_prop
    # carries the single highest weight so prop-account strategy units lead the fair share. Before
    # that the pin was regime-first (operator 2026-09-30); macro stays last. Pinned so a re-weight is
    # a visible decision, not drift. Closes PI-20261005-MG7BGR46-0001.
    t = load_themes()["themes"]
    w = {k: v["weight"] for k, v in t.items()}
    assert w["new_strategy_prop"] == max(w.values()), w
    assert sum(1 for v in w.values() if v == w["new_strategy_prop"]) == 1, w   # strictly the top, not tied
    assert w["macro"] == min(w.values()), w
    with pytest.raises(ValueError):
        load_themes(Path(__file__))            # not a themes file


def _starved_themes(order_fn, jobs, now, themes_doc):
    """Themes holding due work that `order_fn` leaves out of the first 3 picks per theme.

    The population is the committed queue's due, fireable units (decided with their real stamps).
    Ordering is then evaluated on COPIES with `last_dispatched_at` stripped, so the check is about
    the ordering's structure and not about how many units a theme happened to fire in the last
    `share_window_hours`. The old assertion ran on the stamped queue: a theme that had just fired a
    burst (new_strategy_prop, 28 fires in 48h at weight 2) is legitimately behind, so it went red the
    moment that theme received new due work, and passed on main only because the theme had none.
    """
    import copy
    live = [j for j in jobs if j.valid and j.status == "queued" and dq._is_due(j.raw, now)[0]
            and str((j.raw.get("run") or {}).get("workflow") or "").endswith(".yml")]
    copies = []
    for j in live:
        raw = copy.deepcopy(j.raw)
        raw.pop("last_dispatched_at", None)
        copies.append(QueueJob(path=j.path, raw=raw))
    themes = {j.raw["theme"] for j in copies}
    got = set(themes_of(order_fn(copies, now, themes_doc), 3 * len(themes)))
    return themes - got, themes


def test_committed_queue_dispatches_every_theme_it_holds():
    from scripts.research.research_queue import load_queue
    jobs, err = load_queue(Path(dq._DEFAULT_QUEUE))
    assert err is None
    now = datetime.now(timezone.utc)
    starved, themes = _starved_themes(dq.fair_order, jobs, now, load_themes())
    assert themes and not starved, f"themes starved of dispatch: {sorted(starved)}"


def _starved_synth(order_fn, jobs):
    themes = {j.raw["theme"] for j in jobs}
    return themes - set(themes_of(order_fn(jobs, NOW, TH), 3 * len(themes)))


def test_starvation_check_fails_on_a_fair_order_that_drops_a_theme():
    """Negative control: the starvation check goes red for an ordering that starves a theme, and
    stays green for the real one, on a queue where one theme has far more units than the picks examined."""
    synth = [job(i, "regime") for i in range(1, 12)] + [job(50 + i, "macro") for i in range(3)]

    def drops_macro(js, now, doc):
        order = dq.fair_order(js, now, doc)
        return [j for j in order if j.raw["theme"] != "macro"] + [j for j in order if j.raw["theme"] == "macro"]

    assert _starved_synth(drops_macro, synth) == {"macro"}
    assert _starved_synth(dq.fair_order, synth) == set()


# ── deterministic dispatch-config problems are not_due, never a red run ─────────
def test_unresolved_placeholder_input_is_not_due():
    e = {"status": "queued", "cadence": "once", "run": {"workflow": "x.yml",
         "inputs": {"fee_frac": "NOT YET DECIDED -- see design"}}}
    due, why = dq._is_due(e, NOW)
    assert not due and "unresolved placeholder" in why and "fee_frac" in why


def test_undeclared_inputs_are_not_due_even_with_a_path_prefixed_workflow():
    # macro-valuation-backfill.yml declares start_date/cadence_days/fee_frac/carry_frac_per_day
    # but NOT research_unit/label_trigger/note/power_state. The `.github/workflows/` prefix used to hide
    # that (declared_inputs returned None), so gh answered HTTP 422 every cycle.
    e = {"status": "queued", "cadence": "once", "run": {
         "workflow": ".github/workflows/macro-valuation-backfill.yml",
         "inputs": {"research_unit": "RQ-20300101-001", "fee_frac": "0.005"}}}
    assert dq.declared_inputs(e["run"]["workflow"]) == dq.declared_inputs("macro-valuation-backfill.yml")
    assert dq.declared_inputs(e["run"]["workflow"]) is not None
    due, why = dq._is_due(e, NOW)
    assert not due and "not declared" in why and "research_unit" in why


def test_a_well_formed_unit_is_still_due_and_the_committed_queue_has_no_doomed_unit():
    ok = {"status": "queued", "cadence": "once", "run": {"workflow": "research-script-run.yml",
          "inputs": {"research_unit": "RQ-20300101-001"}}}
    assert dq._is_due(ok, NOW)[0]
    from scripts.research.research_queue import load_queue
    jobs, err = load_queue(Path(dq._DEFAULT_QUEUE))
    assert err is None
    doomed = [(j.id, dq.config_problem(j.raw)) for j in jobs if j.valid and j.status == "queued"
              and dq.config_problem(j.raw)]
    assert doomed == []
