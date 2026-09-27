"""E57: the research loss detector's own self-test, run under pytest so a
regression in it reds CI rather than waiting for its next scheduled run."""
import importlib.util
import sys
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "scripts/ops/research_loss_detector.py"
_spec = importlib.util.spec_from_file_location("research_loss_detector", _P)
rld = importlib.util.module_from_spec(_spec)
sys.modules["research_loss_detector"] = rld
_spec.loader.exec_module(rld)


def test_self_test_passes():
    assert rld._self_test() == 0


def test_could_not_read_is_never_clean():
    # The collapsed-states rule, asserted directly: "we did not look" must
    # never be reported as "nothing lost".
    rep = {"overall": rld.COULD_NOT_READ, "generated_at": "t", "questions": {
        "A_stuck_research_prs": {"state": rld.COULD_NOT_READ, "error": "boom",
                                 "findings": None}}}
    assert "COULD NOT READ" in rld.alert_text(rep)
    assert rld.CLEAN != rld.COULD_NOT_READ
