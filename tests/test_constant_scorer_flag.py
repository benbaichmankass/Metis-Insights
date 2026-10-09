"""A constant scorer is flagged, not graded (ML-HYGIENE item 2)."""
from datetime import datetime, timezone

from ml.promotion.attribution import JoinedScore, aggregate_attribution
from ml.promotion.gates import GateThresholds, _gate_live_agreement
from ml.shadow.inspector import ModelStats, format_stats_table


def _joined(scores_wins):
    import inspect
    names = [p for p in inspect.signature(JoinedScore).parameters]
    out = []
    for i, (sc, win) in enumerate(scores_wins):
        kw = {n: None for n in names}
        kw.update(model_id="m", stage="shadow", score=sc, win=win)
        out.append(JoinedScore(**{k: v for k, v in kw.items()}))
    return out


def test_constant_attribution_flagged_and_gate_not_graded():
    att = aggregate_attribution(_joined([(-0.0572, i % 2 == 0) for i in range(40)]))[0]
    assert att.auc == 0.5 and att.constant_scorer is True
    g = _gate_live_agreement(att, GateThresholds())
    assert g.status == "insufficient_data" and "CONSTANT SCORER" in g.detail


def test_varying_scorer_is_still_graded():
    att = aggregate_attribution(_joined([(i / 40, i % 2 == 0) for i in range(40)]))[0]
    assert att.constant_scorer is False
    assert _gate_live_agreement(att, GateThresholds()).status in ("pass", "fail")


def test_stats_read_state_and_table_marker():
    s = ModelStats(model_id="m", stage="shadow")
    assert s.score_read_state == "empty"
    s.count, s.score_min, s.score_max = 10, 1.0, 1.0
    assert s.score_read_state == "insufficient_n"
    s.count = 31
    assert s.score_read_state == "constant"
    s.first_seen = s.last_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert "CONSTANT" in format_stats_table([s])
    s.score_max = 2.0
    assert s.score_read_state == "varying"
